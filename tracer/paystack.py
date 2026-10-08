"""Minimal Paystack client (stdlib only)."""

import hashlib
import hmac
import json
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.paystack.co"


class PaystackError(Exception):
    pass


class Paystack:
    def __init__(self, secret_key: str, timeout: float = 20.0):
        if not secret_key:
            raise PaystackError("Payments are not configured yet. Please try again later.")
        self.secret_key = secret_key
        self.timeout = timeout

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            API + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.secret_key}",
                "Content-Type": "application/json",
                "User-Agent": "tracer-africa-registration/1.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                return json.loads(res.read() or b"{}")
        except urllib.error.HTTPError as e:  # Paystack returns JSON errors with 4xx codes
            try:
                return json.loads(e.read() or b"{}")
            except ValueError:
                return {"status": False, "message": f"HTTP {e.code}"}
        except (urllib.error.URLError, TimeoutError) as e:
            raise PaystackError(f"Could not reach Paystack ({e}).") from e

    def initialize(self, payload: dict) -> dict:
        return self._request("POST", "/transaction/initialize", payload)

    def verify(self, reference: str) -> dict:
        return self._request("GET", "/transaction/verify/" + urllib.parse.quote(reference, safe=""))


def valid_signature(raw_body: bytes, signature: str, secret_key: str) -> bool:
    """Paystack signs webhooks with HMAC-SHA512 of the raw body, keyed by the secret key."""
    if not signature or not secret_key:
        return False
    expected = hmac.new(secret_key.encode(), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature)
