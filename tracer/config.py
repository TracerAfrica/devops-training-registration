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

# Major units (dollars). USD only: Paystack must enable USD on the account before live charges work.
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
PAYSTACK_SECRET_KEY = os.environ.get("PAYSTACK_SECRET_KEY", "")
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
SHEET_ID = os.environ.get("SHEET_ID", "")
SHEET_TAB = os.environ.get("SHEET_TAB", "Registrations")
CRON_SECRET = os.environ.get("CRON_SECRET", "")
