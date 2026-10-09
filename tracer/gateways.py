"""Payment gateways behind one small interface, chosen by PAYMENT_PROVIDER (flutterwave | paystack | stripe).

create(...)  -> {"url": checkout_url, "payment_id": id_to_store}
check(...)   -> {"state": "paid"|"pending"|"expired"|"failed", "amount": major_units, "currency": "USD",
                 "channel": str, "paid_at": datetime|None}
"""

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone


class GatewayError(Exception):
    """Raised when the provider can't be reached or isn't configured. Message is safe to show."""


def _http(method: str, url: str, headers: dict, data: bytes | None = None, timeout: float = 20.0) -> dict:
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"User-Agent": "tracer-africa-registration/1.0", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as e:  # providers return JSON bodies with 4xx
        try:
            return json.loads(e.read() or b"{}")
        except ValueError:
            return {"error": {"message": f"HTTP {e.code}"}}
    except (urllib.error.URLError, TimeoutError) as e:
        raise GatewayError("Could not reach the payment provider. Please try again in a minute.") from e


def _flatten(prefix: str, value, out: list) -> None:
    """Stripe form encoding: a[b][0][c]=v"""
    if isinstance(value, dict):
        for k, v in value.items():
            _flatten(f"{prefix}[{k}]" if prefix else k, v, out)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _flatten(f"{prefix}[{i}]", v, out)
    elif value is not None:
        out.append((prefix, "true" if value is True else "false" if value is False else str(value)))


# --------------------------------------------------------------------------------------------- Stripe
class Stripe:
    name = "stripe"
    API = "https://api.stripe.com/v1"

    def __init__(self, secret_key: str):
        if not secret_key:
            raise GatewayError("Payments are not configured yet. Please try again later.")
        self.key = secret_key

    def _call(self, method: str, path: str, params: dict | None = None) -> dict:
        data = None
        if params:
            pairs: list = []
            _flatten("", params, pairs)
            data = urllib.parse.urlencode(pairs).encode()
        return _http(method, self.API + path, {"Authorization": f"Bearer {self.key}",
                                               "Content-Type": "application/x-www-form-urlencoded"}, data)

    def create(self, *, reference, amount, currency, email, product, site_url, metadata) -> dict:
        s = self._call("POST", "/checkout/sessions", {
            "mode": "payment",
            "client_reference_id": reference,
            "customer_email": email,
            "success_url": f"{site_url}?ref={reference}",
            "cancel_url": f"{site_url}?cancelled={reference}",
            "line_items": [{"quantity": 1, "price_data": {
                "currency": currency.lower(),
                "unit_amount": round(amount * 100),
                "product_data": {"name": f"Tracer Africa — {product}"}}}],
            "payment_intent_data": {"description": f"Tracer Africa — {product} — {reference}",
                                    "metadata": {"reference": reference}},
            "metadata": {"reference": reference, **metadata},
        })
        if s.get("error") or not s.get("url"):
            raise GatewayError(f"We could not start checkout: {(s.get('error') or {}).get('message', 'payment provider error')}.")
        return {"url": s["url"], "payment_id": s["id"]}

    def check(self, *, reference, payment_id) -> dict:
        if not payment_id:
            return {"state": "pending"}
        s = self._call("GET", "/checkout/sessions/" + urllib.parse.quote(str(payment_id), safe=""))
        if s.get("error"):
            return {"state": "pending"}
        if s.get("client_reference_id") not in (None, reference):
            return {"state": "failed"}
        if s.get("payment_status") in ("paid", "no_payment_required"):
            return {"state": "paid", "amount": (s.get("amount_total") or 0) / 100,
                    "currency": str(s.get("currency", "")).upper(), "channel": "card",
                    "paid_at": datetime.now(timezone.utc)}
        if s.get("status") == "expired":
            return {"state": "expired"}
        return {"state": "pending"}

    @staticmethod
    def webhook_reference(raw: bytes, headers, secret: str, tolerance: int = 300):
        """Verify Stripe-Signature (t=…,v1=…; HMAC-SHA256 of 't.body'). Returns the reference to re-check, '' to ignore, or None if invalid."""
        sig = headers.get("stripe-signature", "")
        parts = dict(p.split("=", 1) for p in sig.split(",") if "=" in p)
        t, v1s = parts.get("t", ""), [p.split("=", 1)[1] for p in sig.split(",") if p.startswith("v1=")]
        if not secret or not t.isdigit() or not v1s or abs(time.time() - int(t)) > tolerance:
            return None
        expected = hmac.new(secret.encode(), f"{t}.".encode() + raw, hashlib.sha256).hexdigest()
        if not any(hmac.compare_digest(expected, v) for v in v1s):
            return None
        event = json.loads(raw or b"{}")
        if event.get("type") in ("checkout.session.completed", "checkout.session.async_payment_succeeded",
                                 "checkout.session.expired"):
            return (event.get("data", {}).get("object") or {}).get("client_reference_id") or ""
        return ""


# --------------------------------------------------------------------------------------------- Paystack
class Paystack:
    name = "paystack"
    API = "https://api.paystack.co"

    def __init__(self, secret_key: str):
        if not secret_key:
            raise GatewayError("Payments are not configured yet. Please try again later.")
        self.key = secret_key

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        return _http(method, self.API + path, {"Authorization": f"Bearer {self.key}",
                                               "Content-Type": "application/json"}, data)

    def create(self, *, reference, amount, currency, email, product, site_url, metadata) -> dict:
        r = self._call("POST", "/transaction/initialize", {
            "email": email, "amount": str(round(amount * 100)), "currency": currency, "reference": reference,
            "callback_url": site_url,  # Paystack appends ?trxref=…&reference=…
            "metadata": {"cancel_action": f"{site_url}?cancelled={reference}", **metadata},
        })
        if not r.get("status"):
            raise GatewayError(f"We could not start checkout: {r.get('message') or 'payment provider error'}.")
        return {"url": r["data"]["authorization_url"], "payment_id": reference}

    def check(self, *, reference, payment_id) -> dict:
        r = self._call("GET", "/transaction/verify/" + urllib.parse.quote(reference, safe=""))
        tx = r.get("data") or {}
        if not r.get("status") or not tx:
            return {"state": "pending"}
        if tx.get("status") == "success":
            paid_at = tx.get("paid_at") or tx.get("paidAt")
            try:
                paid_at = datetime.fromisoformat(paid_at.replace("Z", "+00:00")) if paid_at else None
            except ValueError:
                paid_at = None
            return {"state": "paid", "amount": tx["amount"] / 100, "currency": tx.get("currency", ""),
                    "channel": tx.get("channel", ""), "paid_at": paid_at or datetime.now(timezone.utc)}
        if tx.get("status") in ("failed", "reversed"):
            return {"state": "failed"}
        return {"state": "pending"}

    @staticmethod
    def webhook_reference(raw: bytes, headers, secret: str):
        sig = headers.get("x-paystack-signature", "")
        if not sig or not secret:
            return None
        if not hmac.compare_digest(hmac.new(secret.encode(), raw, hashlib.sha512).hexdigest(), sig):
            return None
        event = json.loads(raw or b"{}")
        return (event.get("data") or {}).get("reference", "") if event.get("event") == "charge.success" else ""


# --------------------------------------------------------------------------------------------- Flutterwave
class Flutterwave:
    """Flutterwave Standard (v3). Charges in the price currency (USD) — no domiciliary account needed to collect."""

    name = "flutterwave"
    API = "https://api.flutterwave.com/v3"

    def __init__(self, secret_key: str):
        if not secret_key:
            raise GatewayError("Payments are not configured yet. Please try again later.")
        self.key = secret_key

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        return _http(method, self.API + path, {"Authorization": f"Bearer {self.key}",
                                               "Content-Type": "application/json"}, data)

    def create(self, *, reference, amount, currency, email, product, site_url, metadata) -> dict:
        r = self._call("POST", "/payments", {
            "tx_ref": reference,
            "amount": amount,
            "currency": currency,
            "redirect_url": site_url,   # Flutterwave appends ?status=…&tx_ref=…&transaction_id=…
            "customer": {"email": email, "name": metadata.get("full_name", ""),
                         "phonenumber": metadata.get("whatsapp", "")},
            "customizations": {"title": "Tracer Africa", "description": product,
                               "logo": site_url.rstrip("/") + "/assets/logo-ta.png"},
            "meta": {k: str(v) for k, v in metadata.items()},
        })
        if r.get("status") != "success" or not (r.get("data") or {}).get("link"):
            raise GatewayError(f"We could not start checkout: {r.get('message') or 'payment provider error'}.")
        return {"url": r["data"]["link"], "payment_id": reference}

    def check(self, *, reference, payment_id) -> dict:
        r = self._call("GET", "/transactions/verify_by_reference?tx_ref=" + urllib.parse.quote(reference, safe=""))
        tx = r.get("data") or {}
        if r.get("status") != "success" or not tx or tx.get("tx_ref") not in (None, reference):
            return {"state": "pending"}
        if tx.get("status") == "successful":
            created = tx.get("created_at")
            try:
                paid_at = datetime.fromisoformat(created.replace("Z", "+00:00")) if created else None
            except ValueError:
                paid_at = None
            return {"state": "paid", "amount": float(tx.get("amount") or 0), "currency": tx.get("currency", ""),
                    "channel": tx.get("payment_type", ""), "paid_at": paid_at or datetime.now(timezone.utc)}
        if tx.get("status") == "failed":
            return {"state": "failed"}
        return {"state": "pending"}

    @staticmethod
    def webhook_reference(raw: bytes, headers, secret_hash: str):
        """Flutterwave sends your dashboard 'secret hash' back in the verif-hash header."""
        got = headers.get("verif-hash", "")
        if not secret_hash or not got or not hmac.compare_digest(got, secret_hash):
            return None
        event = json.loads(raw or b"{}")
        if event.get("event") == "charge.completed":
            return (event.get("data") or {}).get("tx_ref", "")
        return ""
