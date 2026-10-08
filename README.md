# Tracer Africa — DevOps Training Registration

One-page **Register → Pay → Confirm** form for the Tracer Africa DevOps Training Program.
Payments run through **Paystack**; every registration lands in a **Google Sheet** that doubles as the admin dashboard.

```
site/                 public page (Netlify publishes this; Vercel serves it too)
  index.html
  assets/             logo-ta.png, flyer.jpg
api/index.py          Vercel entry point (Starlette ASGI app)
tracer/
  config.py           programs, prices, payment plan, env settings
  service.py          register / verify / reconcile logic
  paystack.py         Paystack client + webhook signature check
  store.py            Google Sheet store (service account) + in-memory store
tests/                python -m unittest discover -s tests -v
vercel.json           routes /api/* to the app, daily reconcile cron
netlify.toml          Netlify: publishes site/ only
```

## How it works

1. Student fills the form → `POST /api {action:"register"}`. The backend validates, **sets the price itself**, creates a Paystack transaction with a unique reference (`TA-YYMMDD-XXXXXX`) and appends a **Pending payment** row.
2. The student pays on Paystack's hosted checkout and is sent back to the page.
3. The page calls `verify`; the backend asks Paystack, checks amount + currency, and marks the row **Paid**.
4. Paystack also calls `POST /api/paystack/webhook` (HMAC-SHA512 signed) on `charge.success`, so a payment is recorded even if the student closes the tab.
5. A daily Vercel cron (`/api/cron/reconcile`) re-checks pending rows and marks rows older than 24 h **Unpaid (abandoned)**.

Statuses: `Paid` · `Paid – instalment 1` · `Pending payment` · `Plan – awaiting terms` · `Needs review` · `Unpaid (abandoned)`.
Paystack emails the receipt to the student and a notification to you (Settings → Preferences → Transaction receipts).

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api` | `{"action":"settings"}`, `{"action":"register", …}`, `{"action":"verify","reference":…}` |
| POST | `/api/paystack/webhook` | Paystack events (signature checked) |
| GET | `/api/cron/reconcile` | Daily sweep (needs `Authorization: Bearer $CRON_SECRET`) |
| GET | `/api/health` | Shows whether Paystack and the Sheet are configured |

## Setup

### 1. Google Sheet + service account
1. Google Cloud console → create a project → enable **Google Sheets API**.
2. IAM → Service accounts → create one → Keys → **Add key → JSON**. Keep the file private.
3. Create a Google Sheet, copy its ID from the URL (`/d/<SHEET_ID>/edit`), and **share it with the service account's email as Editor**.
   The app creates the `Registrations` tab, header row and status colours on first use.

### 2. Vercel
Import this repo at vercel.com → New Project (Framework: Other). Add Environment Variables:

| Name | Value |
|---|---|
| `PAYSTACK_SECRET_KEY` | `sk_test_…` (later `sk_live_…`) |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | full contents of the JSON key file |
| `SHEET_ID` | the Sheet ID |
| `CRON_SECRET` | any long random string |
| `ALLOWED_ORIGINS` | optional; defaults to the Netlify site |

Deploy, then open `https://<project>.vercel.app/api/health` — both `paystack` and `sheet` should be `true`.

### 3. Paystack
- Settings → API Keys & Webhooks → **Test Webhook URL**: `https://<project>.vercel.app/api/paystack/webhook` (same for Live later).
- USD: complete Compliance, add a USD (domiciliary) settlement account, then ask Paystack support to enable USD. Until then USD charges fail with "Currency not supported by merchant".

### 4. The page
Set `CONFIG.BACKEND_URL` in `site/index.html` to `https://<project>.vercel.app/api` and push. Netlify redeploys.

## Prices and payment plan

Edit `tracer/config.py` (`PRICES`, `PLAN`) and push; Vercel redeploys automatically. While `PLAN` is `None`, "Payment plan" registrations are recorded without charging.

## Local development

```
pip install -r requirements.txt uvicorn
USE_MEMORY_STORE=1 PAYSTACK_SECRET_KEY=sk_test_… uvicorn api.index:app --reload
python -m unittest discover -s tests -v
```

## LinkedIn reply

> Thanks for your interest! You can view the program details and register here: https://tracer-africa-devops.netlify.app
