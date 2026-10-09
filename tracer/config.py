"""Business settings. Prices live here (server-side) so the page can't change what a student is charged."""

import os

SITE_URL = os.environ.get("SITE_URL", "https://tracer-africa-devops.netlify.app/")

# Browser origins allowed to call the API.
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "ALLOWED_ORIGINS",
        "https://tracer-africa-devops.netlify.app,http://localhost:8000,http://127.0.0.1:8000",
    ).split(",")
    if o.strip()
]

PROGRAMS = {
    "1M": "1-Month DevOps Training",
    "2M": "2-Month DevOps Training",
}

# Major units (dollars).
PRICES = {
    "USD": {"1M": 300, "2M": 600},
}

# None until payment-plan terms are confirmed. While None, "Payment plan" registrations are saved as
# "Plan – awaiting terms" and no payment is taken. When ready, e.g.:
#   PLAN = {"USD": {"label": "2 monthly instalments",
#                   "description": "Pay half today and half in 30 days.",
#                   "first": {"1M": 150, "2M": 300}}}
PLAN = None

LEVELS = ["Complete beginner", "Beginner", "Some experience", "Intermediate", "Experienced professional"]
OPTIONS = ["Full payment", "Payment plan"]

ABANDON_AFTER_HOURS = 24
TIMEZONE = "Africa/Lagos"

# Secrets / environment (set in Vercel → Project → Settings → Environment Variables).
PAYMENT_PROVIDER = os.environ.get("PAYMENT_PROVIDER", "flutterwave").strip().lower()  # flutterwave | paystack | stripe
FLW_SECRET_KEY = os.environ.get("FLW_SECRET_KEY", "").strip()
FLW_WEBHOOK_HASH = os.environ.get("FLW_WEBHOOK_HASH", "").strip()    # the "Secret hash" you set in Flutterwave → Settings → Webhooks
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
PAYSTACK_SECRET_KEY = os.environ.get("PAYSTACK_SECRET_KEY", "").strip()
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
SHEET_ID = os.environ.get("SHEET_ID", "").strip()
if "/d/" in SHEET_ID:  # a full Sheet link was pasted: keep just the ID
    SHEET_ID = SHEET_ID.split("/d/", 1)[1].split("/", 1)[0]
SHEET_TAB = os.environ.get("SHEET_TAB", "Registrations").strip()
CRON_SECRET = os.environ.get("CRON_SECRET", "").strip()
