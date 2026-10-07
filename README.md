# Tracer Africa — DevOps Training Registration

One-page **Register → Pay → Confirm** form. Payments run through **Paystack**; every registration lands in a **Google Sheet** that doubles as the admin dashboard.

```
netlify.toml          Netlify config: publishes site/ only
site/index.html       the public registration page (deployed by Netlify)
site/assets/mark.png  TA logo mark
site/assets/flyer.jpg full curriculum (linked from the page + used as the link-preview image)
backend/Code.gs       Google Apps Script: pricing, Paystack checkout, verification, Sheet
```

## How it works

1. Student fills the form and picks a program + payment option.
2. The backend validates the details, **sets the price itself**, creates a Paystack transaction with a unique reference (`TA-YYMMDD-XXXXXX`), and writes a row marked **Pending payment**.
3. Paystack checkout opens as a popup (falls back to Paystack's full-page checkout on blocked popups).
4. On success the backend calls Paystack's verify API, checks amount + currency, and marks the row **Paid**. The student sees "Registration Successful" and gets an email receipt.
5. Every 15 minutes a trigger re-checks pending rows, so students who close the tab right after paying still get marked Paid. Rows unpaid after 24 h become **Unpaid (abandoned)**.

Sheet statuses: `Paid` · `Paid – instalment 1` · `Pending payment` · `Plan – awaiting terms` · `Needs review` (amount/currency mismatch) · `Unpaid (abandoned)`.

## Setup (about 15 minutes)

### 1. Paystack
- Create/verify your business at paystack.com and get your keys (Settings → API Keys & Webhooks). Start with **test keys**.
- **USD**: charging in dollars needs a USD-enabled Paystack account (request it from Paystack). If you don't have it yet, set an NGN price in `Code.gs` and remove `USD`.
- **International cards** on an NGN account must be enabled on your Paystack dashboard.

### 2. Google Sheet + backend
1. Create a Google Sheet (e.g. "Tracer Africa Registrations").
2. Extensions → **Apps Script** → replace the contents of `Code.gs` with `backend/Code.gs`.
3. Edit the `CFG` block: `SITE_URL`, prices, `NOTIFY_EMAIL`.
4. Project Settings (gear) → **Script properties** → add `PAYSTACK_SECRET_KEY` = `sk_test_...`
5. Select `setup` in the function dropdown → **Run** → approve permissions. This formats the sheet and adds the 15-minute trigger.
6. **Deploy → New deployment → Web app**: Execute as **Me**, Who has access **Anyone** → Deploy. Copy the URL ending in `/exec`.

### 3. The page
1. In `site/index.html`, set `CONFIG.BACKEND_URL` to the `/exec` URL (and optionally `CONTACT_WHATSAPP`, `CONTACT_EMAIL`).
2. Netlify deploys `site/` automatically on every push to `main` (see `netlify.toml`). Put the site URL in `CFG.SITE_URL`, then **Deploy → Manage deployments → Edit → New version** in Apps Script.
3. In `site/index.html`, change `og:image` to the full URL (`https://YOUR-SITE/assets/flyer.jpg`) so LinkedIn and WhatsApp show the flyer in link previews.
4. Pay with one of Paystack's test cards (Paystack docs → Payments → Test Payments). Check the row turns **Paid** and the receipt email arrives.
5. Swap to `sk_live_...` in Script properties. Done.

Changing anything in `Code.gs` later? Always publish a **new version** of the deployment, or the live URL keeps running the old code.

## Payment plan

While `CFG.PLAN` is `null`, choosing "Payment plan" saves the registration as **Plan – awaiting terms** and takes no money. When terms are final, fill in `CFG.PLAN` (example in the file); the page then shows and charges the first instalment automatically.

## LinkedIn reply

> Thanks for your interest! You can view the program details and register here: https://YOUR-SITE.netlify.app

The flyer is the link-preview image, so the post shows the program at a glance. A custom domain (e.g. `register.tracerafrica.com`) reads more trustworthy than a netlify.app address.
