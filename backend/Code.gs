/**
 * Tracer Africa — DevOps Training registration backend
 * Google Apps Script web app bound to a Google Sheet.
 *
 *  - Prices are set HERE (the page can't change what a student is charged).
 *  - Paystack transactions are created server-side, then verified against
 *    Paystack's API before a row is marked Paid.
 *  - A 15-minute trigger confirms payments for anyone who closed the page early.
 *
 * Setup: see README.md. Put your Paystack SECRET key in
 * Project Settings → Script properties as PAYSTACK_SECRET_KEY.
 */

const CFG = {
  SHEET_NAME: 'Registrations',

  // Public URL of the registration page. Paystack returns students here
  // (with ?reference=...) when the full-page checkout is used.
  SITE_URL: 'https://YOUR-SITE.netlify.app/',

  PROGRAMS: { '1M': '1-Month DevOps Training', '2M': '2-Month DevOps Training' },

  // Major units (dollars, naira). Add NGN once you've set a naira price, e.g.
  //   NGN: { '1M': 1500000, '2M': 3000000 },
  // USD requires a USD-enabled Paystack account; NGN works on every Nigerian account.
  PRICES: {
    USD: { '1M': 1000, '2M': 2000 },
  },

  // Leave null until payment-plan terms are confirmed. While null, "Payment plan"
  // registrations are saved as "Plan – awaiting terms" and no payment is taken.
  // When ready, e.g.:
  //   PLAN: { USD: { label: '2 monthly instalments', description: 'Pay half today and half in 30 days.',
  //                  first: { '1M': 500, '2M': 1000 } } },
  PLAN: null,

  NOTIFY_EMAIL: '',            // organiser inbox for "new paid registration" alerts ('' = off)
  SEND_STUDENT_RECEIPT: true,  // email the student a confirmation after payment
  ABANDON_AFTER_HOURS: 24,     // pending rows older than this become "Unpaid (abandoned)"
};

const HEADERS = ['Registration date', 'Reference', 'Full name', 'Email', 'WhatsApp / phone', 'Country',
  'Experience level', 'Program', 'Payment option', 'Currency', 'Amount due', 'Amount paid',
  'Payment status', 'Paid at', 'Channel', 'Notes'];
const COL = Object.fromEntries(HEADERS.map((h, i) => [h, i + 1]));
const LEVELS = ['Complete beginner', 'Beginner', 'Some experience', 'Intermediate', 'Experienced professional'];

/* ------------------------------------------------------------------ */
/* One-time setup: run this from the editor once.                      */
/* ------------------------------------------------------------------ */
function setup() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(CFG.SHEET_NAME) || ss.insertSheet(CFG.SHEET_NAME);
  sh.getRange(1, 1, 1, HEADERS.length).setValues([HEADERS])
    .setFontWeight('bold').setBackground('#0a1a3a').setFontColor('#ffffff');
  sh.setFrozenRows(1);
  sh.getRange('A:A').setNumberFormat('yyyy-mm-dd hh:mm');
  sh.getRange('N:N').setNumberFormat('yyyy-mm-dd hh:mm');
  sh.getRange('K:L').setNumberFormat('#,##0.00');
  sh.setColumnWidths(1, HEADERS.length, 150);

  const status = sh.getRange('M2:M');
  const rule = (text, bg, fg) => SpreadsheetApp.newConditionalFormatRule()
    .whenTextStartsWith(text).setBackground(bg).setFontColor(fg).setRanges([status]).build();
  sh.setConditionalFormatRules([
    rule('Paid', '#e5f6ef', '#12805c'),
    rule('Pending', '#fff3dc', '#9a5b00'),
    rule('Plan', '#eaf2ff', '#1b6fe4'),
    rule('Needs review', '#fdecea', '#b42318'),
    rule('Unpaid', '#f1f3f6', '#5a6a85'),
  ]);
  if (!sh.getFilter()) sh.getRange(1, 1, sh.getMaxRows(), HEADERS.length).createFilter();

  ScriptApp.getProjectTriggers().filter(t => t.getHandlerFunction() === 'reconcilePending')
    .forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('reconcilePending').timeBased().everyMinutes(15).create();

  if (!secretKey_()) Logger.log('⚠️  Add PAYSTACK_SECRET_KEY under Project Settings → Script properties.');
  Logger.log('Setup complete.');
}

/* ------------------------------------------------------------------ */
/* HTTP entry points                                                    */
/* ------------------------------------------------------------------ */
function doGet() {
  return json_({ ok: true, service: 'tracer-africa-registration' });
}

function doPost(e) {
  try {
    const body = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    switch (body.action) {
      case 'settings': return json_(settings_());
      case 'register': return json_(register_(body));
      case 'verify':   return json_(verify_(String(body.reference || '')));
      default:         return json_({ ok: false, error: 'Unknown request.' });
    }
  } catch (err) {
    console.error(err);
    return json_({ ok: false, error: err.userMessage || 'Something went wrong on our side. Please try again in a minute.' });
  }
}

/* ------------------------------------------------------------------ */
function settings_() {
  return { ok: true, prices: CFG.PRICES, plan: CFG.PLAN, programs: CFG.PROGRAMS };
}

function register_(b) {
  const d = {
    name: clean_(b.name, 120), email: clean_(b.email, 160).toLowerCase(), phone: clean_(b.phone, 25),
    country: clean_(b.country, 60), level: clean_(b.level, 40),
    program: String(b.program || ''), option: String(b.option || ''), currency: String(b.currency || ''),
  };
  if (d.name.split(/\s+/).length < 2) fail_('Enter your first and last name.');
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(d.email)) fail_('Enter a valid email address.');
  if (d.phone.replace(/\D/g, '').length < 7) fail_('Enter a valid WhatsApp / phone number.');
  if (d.country.length < 2) fail_('Enter your country.');
  if (LEVELS.indexOf(d.level) < 0) fail_('Choose your experience level.');
  if (!CFG.PROGRAMS[d.program]) fail_('Choose a program.');
  if (['Full payment', 'Payment plan'].indexOf(d.option) < 0) fail_('Choose a payment option.');
  if (!CFG.PRICES[d.currency]) fail_('That currency is not available.');

  const total = CFG.PRICES[d.currency][d.program];
  const plan = CFG.PLAN && CFG.PLAN[d.currency];
  const reference = newReference_();
  const now = new Date();

  // Payment plan without confirmed terms: record the registration, take no payment.
  if (d.option === 'Payment plan' && !plan) {
    appendRow_(now, reference, d, total, 'Plan – awaiting terms', 'Send plan terms + payment link');
    return { ok: true, requiresPayment: false, reference, program: CFG.PROGRAMS[d.program] };
  }

  const dueNow = d.option === 'Payment plan' ? plan.first[d.program] : total;
  const init = paystack_('post', '/transaction/initialize', {
    email: d.email,
    amount: String(Math.round(dueNow * 100)),   // subunits (cents / kobo)
    currency: d.currency,
    reference,
    callback_url: CFG.SITE_URL,
    metadata: {
      program: CFG.PROGRAMS[d.program], option: d.option, total_price: total,
      custom_fields: [
        { display_name: 'Full name', variable_name: 'full_name', value: d.name },
        { display_name: 'WhatsApp', variable_name: 'whatsapp', value: d.phone },
        { display_name: 'Program', variable_name: 'program', value: CFG.PROGRAMS[d.program] },
        { display_name: 'Payment option', variable_name: 'payment_option', value: d.option },
      ],
    },
  });
  if (!init.status) fail_('We could not start checkout: ' + (init.message || 'payment provider error') + '.');

  appendRow_(now, reference, d, dueNow, 'Pending payment',
    d.option === 'Payment plan' ? 'Instalment 1 of plan; total ' + total : '');
  return {
    ok: true, requiresPayment: true, reference,
    accessCode: init.data.access_code, authorizationUrl: init.data.authorization_url,
    amount: dueNow, currency: d.currency,
  };
}

function verify_(reference) {
  if (!/^TA-[A-Z0-9-]{6,40}$/.test(reference)) fail_('Invalid payment reference.');
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const sh = sheet_();
    const row = findRow_(sh, reference);
    if (!row) fail_('We could not find that registration.');
    const rec = readRow_(sh, row);
    if (String(rec['Payment status']).indexOf('Paid') === 0) return result_(rec);

    const res = paystack_('get', '/transaction/verify/' + encodeURIComponent(reference));
    const tx = res && res.data;
    if (!res.status || !tx) return { ok: true, status: rec['Payment status'], reference };

    if (tx.status === 'success') {
      const paidMajor = tx.amount / 100;
      const due = Number(rec['Amount due']);
      if (tx.currency !== rec['Currency'] || paidMajor + 0.001 < due) {
        setCells_(sh, row, { 'Payment status': 'Needs review', 'Amount paid': paidMajor,
          'Notes': appendNote_(rec['Notes'], 'Paid ' + tx.currency + ' ' + paidMajor + ' vs due ' + rec['Currency'] + ' ' + due) });
        return { ok: true, status: 'Needs review', reference };
      }
      const isPlan = rec['Payment option'] === 'Payment plan';
      const status = isPlan ? 'Paid – instalment 1' : 'Paid';
      setCells_(sh, row, { 'Payment status': status, 'Amount paid': paidMajor,
        'Paid at': new Date(tx.paid_at || Date.now()), 'Channel': tx.channel || '' });
      const updated = readRow_(sh, row);
      notify_(updated);
      return result_(updated);
    }
    return { ok: true, status: rec['Payment status'], reference, gateway: tx.status };
  } finally {
    lock.releaseLock();
  }
}

/** Runs every 15 min: confirms payments where the student closed the page early. */
function reconcilePending() {
  const sh = sheet_();
  const last = sh.getLastRow();
  if (last < 2) return;
  const values = sh.getRange(2, 1, last - 1, HEADERS.length).getValues();
  const cutoff = Date.now() - CFG.ABANDON_AFTER_HOURS * 3600 * 1000;
  values.forEach((v, i) => {
    if (v[COL['Payment status'] - 1] !== 'Pending payment') return;
    const ref = v[COL['Reference'] - 1];
    try {
      const r = verify_(ref);
      if (r.status === 'Pending payment' && new Date(v[0]).getTime() < cutoff) {
        setCells_(sh, i + 2, { 'Payment status': 'Unpaid (abandoned)' });
      }
    } catch (err) { console.warn('reconcile ' + ref + ': ' + err); }
  });
}

/* ------------------------------------------------------------------ */
/* Helpers                                                              */
/* ------------------------------------------------------------------ */
function secretKey_() {
  return PropertiesService.getScriptProperties().getProperty('PAYSTACK_SECRET_KEY');
}

function paystack_(method, path, payload) {
  const key = secretKey_();
  if (!key) fail_('Payments are not configured yet. Please try again later.');
  const opts = { method, muteHttpExceptions: true, headers: { Authorization: 'Bearer ' + key } };
  if (payload) { opts.contentType = 'application/json'; opts.payload = JSON.stringify(payload); }
  const res = UrlFetchApp.fetch('https://api.paystack.co' + path, opts);
  try { return JSON.parse(res.getContentText()); }
  catch (e) { return { status: false, message: 'HTTP ' + res.getResponseCode() }; }
}

function sheet_() {
  const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(CFG.SHEET_NAME);
  if (!sh) fail_('Registration sheet missing. Run setup() first.');
  return sh;
}

function appendRow_(when, reference, d, amountDue, status, notes) {
  const sh = sheet_();
  const row = HEADERS.map(() => '');
  const put = (h, v) => { row[COL[h] - 1] = v; };
  put('Registration date', when); put('Reference', reference); put('Full name', safe_(d.name));
  put('Email', safe_(d.email)); put('WhatsApp / phone', "'" + d.phone); put('Country', safe_(d.country));
  put('Experience level', d.level); put('Program', CFG.PROGRAMS[d.program]); put('Payment option', d.option);
  put('Currency', d.currency); put('Amount due', amountDue); put('Payment status', status); put('Notes', notes || '');
  sh.appendRow(row);
}

function findRow_(sh, reference) {
  const hit = sh.getRange(2, COL['Reference'], Math.max(sh.getLastRow() - 1, 1), 1)
    .createTextFinder(reference).matchEntireCell(true).findNext();
  return hit ? hit.getRow() : 0;
}

function readRow_(sh, row) {
  const v = sh.getRange(row, 1, 1, HEADERS.length).getValues()[0];
  return Object.fromEntries(HEADERS.map((h, i) => [h, v[i]]));
}

function setCells_(sh, row, obj) {
  Object.keys(obj).forEach(h => sh.getRange(row, COL[h]).setValue(obj[h]));
}

function result_(rec) {
  return {
    ok: true, status: String(rec['Payment status']).indexOf('Paid') === 0 ? 'Paid' : rec['Payment status'],
    reference: rec['Reference'], name: rec['Full name'], program: rec['Program'], option: rec['Payment option'],
    amountPaid: rec['Amount paid'], currency: rec['Currency'],
  };
}

function notify_(rec) {
  const amount = rec['Currency'] + ' ' + Number(rec['Amount paid']).toLocaleString();
  try {
    if (CFG.NOTIFY_EMAIL) {
      MailApp.sendEmail(CFG.NOTIFY_EMAIL, 'New paid registration: ' + rec['Full name'],
        [rec['Full name'], rec['Email'], rec['WhatsApp / phone'], rec['Country'], rec['Experience level'],
         rec['Program'] + ' · ' + rec['Payment option'], amount + ' · ' + rec['Reference']].join('\n'));
    }
    if (CFG.SEND_STUDENT_RECEIPT) {
      MailApp.sendEmail({
        to: rec['Email'], name: 'Tracer Africa',
        subject: 'Registration successful: Tracer Africa DevOps Training',
        body: 'Hi ' + String(rec['Full name']).split(' ')[0] + ',\n\n' +
          'Thank you for registering for the Tracer Africa DevOps Training Program. Your payment has been received successfully.\n\n' +
          'Program: ' + rec['Program'] + '\nPayment: ' + rec['Payment option'] + '\nAmount paid: ' + amount +
          '\nReference: ' + rec['Reference'] + '\n\n' +
          "We'll contact you by email or WhatsApp with your training schedule and onboarding information.\n\nTracer Africa\nLearn · Build · Grow",
      });
    }
  } catch (err) { console.warn('email failed: ' + err); }
}

function newReference_() {
  const date = Utilities.formatDate(new Date(), 'Africa/Lagos', 'yyMMdd');
  const rand = Utilities.getUuid().replace(/-/g, '').slice(0, 6).toUpperCase();
  return 'TA-' + date + '-' + rand;
}

function clean_(v, max) { return String(v == null ? '' : v).replace(/\s+/g, ' ').trim().slice(0, max); }
function safe_(v) { return /^[=+\-@]/.test(v) ? "'" + v : v; }          // block formula injection
function appendNote_(a, b) { return a ? a + '; ' + b : b; }
function fail_(msg) { const e = new Error(msg); e.userMessage = msg; throw e; }
function json_(obj) { return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON); }
