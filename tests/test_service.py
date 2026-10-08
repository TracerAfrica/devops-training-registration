"""Run: python -m unittest discover -s tests -v"""

import hashlib
import hmac
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracer import config, service  # noqa: E402
from tracer.paystack import valid_signature  # noqa: E402
from tracer.store import MemoryStore  # noqa: E402

BASE = {"name": "Ada Okafor", "email": "Ada@Example.com", "phone": "+234 801 234 5678", "country": "Nigeria",
        "level": "Beginner", "program": "2M", "option": "Full payment", "currency": "USD"}


class FakePaystack:
    def __init__(self):
        self.init_payloads = []
        self.verify_response = {"status": True, "data": {"status": "abandoned"}}
        self.init_response = {"status": True, "data": {"authorization_url": "https://checkout.paystack.com/x"}}

    def initialize(self, payload):
        self.init_payloads.append(payload)
        return self.init_response

    def verify(self, reference):
        return self.verify_response


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.ps = FakePaystack()
        self.factory = lambda: self.ps

    def test_register_prices_server_side(self):
        r = service.register({**BASE, "amount": 1}, self.store, self.factory)
        self.assertTrue(r["requiresPayment"])
        self.assertEqual(r["amount"], 600)
        sent = self.ps.init_payloads[0]
        self.assertEqual(sent["amount"], "60000")
        self.assertEqual(sent["currency"], "USD")
        self.assertEqual(sent["email"], "ada@example.com")
        self.assertIn("?cancelled=" + r["reference"], sent["metadata"]["cancel_action"])
        _, rec = self.store.find(r["reference"])
        self.assertEqual(rec["Payment status"], "Pending payment")
        self.assertEqual(rec["Amount due"], 600)

    def test_validation(self):
        for field, value, msg in [("name", "Ada", "first and last"), ("email", "nope", "email"),
                                  ("phone", "12", "phone"), ("level", "Guru", "experience"),
                                  ("program", "3M", "program"), ("currency", "NGN", "currency")]:
            with self.assertRaises(service.UserError, msg=field) as ctx:
                service.register({**BASE, field: value}, self.store, self.factory)
            self.assertIn(msg, str(ctx.exception).lower())
        self.assertEqual(self.store.rows, [])

    def test_paystack_rejects_currency(self):
        self.ps.init_response = {"status": False, "message": "Currency not supported by merchant"}
        with self.assertRaises(service.UserError) as ctx:
            service.register(BASE, self.store, self.factory)
        self.assertIn("Currency not supported", str(ctx.exception))
        self.assertEqual(self.store.rows, [])

    def test_plan_without_terms_takes_no_payment(self):
        r = service.register({**BASE, "option": "Payment plan"}, self.store, self.factory)
        self.assertFalse(r["requiresPayment"])
        self.assertEqual(self.ps.init_payloads, [])
        self.assertEqual(self.store.find(r["reference"])[1]["Payment status"], "Plan – awaiting terms")

    def test_verify_paths(self):
        ref = service.register(BASE, self.store, self.factory)["reference"]
        self.assertEqual(service.verify(ref, self.store, self.factory)["status"], "Pending payment")

        self.ps.verify_response = {"status": True, "data": {"status": "success", "amount": 30000, "currency": "USD"}}
        self.assertEqual(service.verify(ref, self.store, self.factory)["status"], "Needs review")

        self.store.update(self.store.find(ref)[0], {"Payment status": "Pending payment"})
        self.ps.verify_response = {"status": True, "data": {"status": "success", "amount": 60000, "currency": "USD",
                                                            "channel": "card", "paid_at": "2026-10-08T15:00:00.000Z"}}
        out = service.verify(ref, self.store, self.factory)
        self.assertEqual((out["status"], out["amountPaid"]), ("Paid", 600))
        rec = self.store.find(ref)[1]
        self.assertEqual((rec["Channel"], rec["Paid at"]), ("card", "2026-10-08 16:00"))

        # Already paid: no further Paystack call changes anything.
        self.ps.verify_response = {"status": False}
        self.assertEqual(service.verify(ref, self.store, self.factory)["status"], "Paid")

    def test_verify_rejects_bad_reference(self):
        for ref in ["", "x", "TA-../../etc", "TA-ZZZZZZ"]:
            with self.assertRaises(service.UserError):
                service.verify(ref, self.store, self.factory)

    def test_reconcile(self):
        old = service.register(BASE, self.store, self.factory)["reference"]
        new = service.register(BASE, self.store, self.factory)["reference"]
        stale = (datetime.now(timezone.utc) - timedelta(hours=30)).astimezone(
            __import__("zoneinfo").ZoneInfo(config.TIMEZONE)).strftime(service.TS_FMT)
        self.store.update(self.store.find(old)[0], {"Registration date": stale})
        out = service.reconcile(self.store, self.factory)
        self.assertEqual((out["checked"], out["abandoned"], out["paid"]), (2, 1, 0))
        self.assertEqual(self.store.find(old)[1]["Payment status"], "Unpaid (abandoned)")
        self.assertEqual(self.store.find(new)[1]["Payment status"], "Pending payment")

    def test_webhook_signature(self):
        body = b'{"event":"charge.success"}'
        sig = hmac.new(b"sk_test_x", body, hashlib.sha512).hexdigest()
        self.assertTrue(valid_signature(body, sig, "sk_test_x"))
        self.assertFalse(valid_signature(body, sig, "sk_test_y"))
        self.assertFalse(valid_signature(body + b" ", sig, "sk_test_x"))


if __name__ == "__main__":
    unittest.main()
