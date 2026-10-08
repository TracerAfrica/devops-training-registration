"""Registration and payment logic. Pure: takes a store and a Paystack client, so it is easy to test."""

import re
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config

REF_RE = re.compile(r"^TA-[A-Z0-9-]{6,40}$")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
TS_FMT = "%Y-%m-%d %H:%M"


class UserError(Exception):
    """A problem the student can fix; the message is shown on the page."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(dt: datetime) -> str:
    return dt.astimezone(ZoneInfo(config.TIMEZONE)).strftime(TS_FMT)


def _clean(v, max_len: int) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:max_len]


def _new_reference() -> str:
    day = datetime.now(ZoneInfo(config.TIMEZONE)).strftime("%y%m%d")
    return f"TA-{day}-{uuid.uuid4().hex[:6].upper()}"


def settings() -> dict:
    return {"ok": True, "prices": config.PRICES, "plan": config.PLAN,
            "programs": config.PROGRAMS, "provider": "paystack"}


def _validate(b: dict) -> dict:
    d = {
        "name": _clean(b.get("name"), 120),
        "email": _clean(b.get("email"), 160).lower(),
        "phone": _clean(b.get("phone"), 25),
        "country": _clean(b.get("country"), 60),
        "level": _clean(b.get("level"), 40),
        "program": str(b.get("program") or ""),
        "option": str(b.get("option") or ""),
        "currency": str(b.get("currency") or ""),
    }
    if len(d["name"].split()) < 2:
        raise UserError("Enter your first and last name.")
    if not EMAIL_RE.match(d["email"]):
        raise UserError("Enter a valid email address.")
    if len(re.sub(r"\D", "", d["phone"])) < 7:
        raise UserError("Enter a valid WhatsApp / phone number.")
    if len(d["country"]) < 2:
        raise UserError("Enter your country.")
    if d["level"] not in config.LEVELS:
        raise UserError("Choose your experience level.")
    if d["program"] not in config.PROGRAMS:
        raise UserError("Choose a program.")
    if d["option"] not in config.OPTIONS:
        raise UserError("Choose a payment option.")
    if d["currency"] not in config.PRICES:
        raise UserError("That currency is not available.")
    return d


def register(body: dict, store, paystack_factory) -> dict:
    d = _validate(body)
    total = config.PRICES[d["currency"]][d["program"]]
    plan = (config.PLAN or {}).get(d["currency"])
    reference = _new_reference()
    program = config.PROGRAMS[d["program"]]
    row = {
        "Registration date": _stamp(_now()), "Reference": reference, "Full name": d["name"],
        "Email": d["email"], "WhatsApp / phone": d["phone"], "Country": d["country"],
        "Experience level": d["level"], "Program": program, "Payment option": d["option"],
        "Currency": d["currency"],
    }

    # Payment plan without confirmed terms: record the registration, take no payment.
    if d["option"] == "Payment plan" and not plan:
        store.append({**row, "Amount due": total, "Payment status": "Plan – awaiting terms",
                      "Notes": "Send plan terms + payment link"})
        return {"ok": True, "requiresPayment": False, "reference": reference, "program": program}

    due = plan["first"][d["program"]] if d["option"] == "Payment plan" else total
    init = paystack_factory().initialize({
        "email": d["email"],
        "amount": str(round(due * 100)),          # subunits (cents / kobo)
        "currency": d["currency"],
        "reference": reference,
        "callback_url": config.SITE_URL,          # Paystack appends ?trxref=…&reference=…
        "metadata": {
            "cancel_action": f"{config.SITE_URL}?cancelled={reference}",
            "program": program, "option": d["option"], "total_price": total,
            "custom_fields": [
                {"display_name": "Full name", "variable_name": "full_name", "value": d["name"]},
                {"display_name": "WhatsApp", "variable_name": "whatsapp", "value": d["phone"]},
                {"display_name": "Program", "variable_name": "program", "value": program},
                {"display_name": "Payment option", "variable_name": "payment_option", "value": d["option"]},
            ],
        },
    })
    if not init.get("status"):
        raise UserError(f"We could not start checkout: {init.get('message') or 'payment provider error'}.")

    store.append({**row, "Amount due": due, "Payment status": "Pending payment",
                  "Notes": f"Instalment 1 of plan; total {total}" if d["option"] == "Payment plan" else ""})
    return {"ok": True, "requiresPayment": True, "reference": reference,
            "checkoutUrl": init["data"]["authorization_url"], "amount": due, "currency": d["currency"]}


def _result(rec: dict) -> dict:
    status = str(rec["Payment status"])
    return {"ok": True, "status": "Paid" if status.startswith("Paid") else status,
            "reference": rec["Reference"], "name": rec["Full name"], "program": rec["Program"],
            "option": rec["Payment option"], "amountPaid": rec["Amount paid"], "currency": rec["Currency"]}


def verify(reference: str, store, paystack_factory) -> dict:
    reference = str(reference or "")
    if not REF_RE.match(reference):
        raise UserError("Invalid payment reference.")
    row_id, rec = store.find(reference)
    if row_id is None:
        raise UserError("We could not find that registration.")
    if str(rec["Payment status"]).startswith("Paid"):
        return _result(rec)

    res = paystack_factory().verify(reference)
    tx = res.get("data") or {}
    if not res.get("status") or not tx:
        return {"ok": True, "status": rec["Payment status"], "reference": reference}

    if tx.get("status") == "success":
        paid = tx["amount"] / 100
        due = float(rec["Amount due"] or 0)
        if tx.get("currency") != rec["Currency"] or paid + 0.001 < due:
            note = f"Paid {tx.get('currency')} {paid} vs due {rec['Currency']} {due}"
            store.update(row_id, {"Payment status": "Needs review", "Amount paid": paid,
                                  "Notes": f"{rec['Notes']}; {note}" if rec["Notes"] else note})
            return {"ok": True, "status": "Needs review", "reference": reference}
        status = "Paid – instalment 1" if rec["Payment option"] == "Payment plan" else "Paid"
        paid_at = tx.get("paid_at") or tx.get("paidAt")
        try:
            paid_at = _stamp(datetime.fromisoformat(paid_at.replace("Z", "+00:00"))) if paid_at else _stamp(_now())
        except ValueError:
            paid_at = _stamp(_now())
        fields = {"Payment status": status, "Amount paid": paid, "Paid at": paid_at, "Channel": tx.get("channel", "")}
        store.update(row_id, fields)
        return _result({**rec, **fields})
    return {"ok": True, "status": rec["Payment status"], "reference": reference, "gateway": tx.get("status")}


def reconcile(store, paystack_factory, now: datetime | None = None) -> dict:
    """Confirm payments for students who closed the tab early; mark old unpaid rows as abandoned."""
    now = now or _now()
    cutoff = now - timedelta(hours=config.ABANDON_AFTER_HOURS)
    tz = ZoneInfo(config.TIMEZONE)
    counts = {"checked": 0, "paid": 0, "abandoned": 0, "errors": 0}
    for row_id, rec in store.all():
        if rec["Payment status"] != "Pending payment":
            continue
        counts["checked"] += 1
        try:
            r = verify(rec["Reference"], store, paystack_factory)
            if r["status"] == "Paid":
                counts["paid"] += 1
            elif r["status"] == "Pending payment":
                created = datetime.strptime(str(rec["Registration date"]), TS_FMT).replace(tzinfo=tz)
                if created < cutoff:
                    store.update(row_id, {"Payment status": "Unpaid (abandoned)"})
                    counts["abandoned"] += 1
        except Exception:  # keep going; one bad row must not stop the rest
            counts["errors"] += 1
    return {"ok": True, **counts}
