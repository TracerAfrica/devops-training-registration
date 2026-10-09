"""Run: python -m unittest discover -s tests -v"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracer import config, service  # noqa: E402
from tracer.gateways import GatewayError  # noqa: E402
from tracer.store import MemoryStore  # noqa: E402

BASE = {"name": "Ada Okafor", "email": "Ada@Example.com", "phone": "+234 801 234 5678", "country": "Ghana",
        "level": "Beginner", "program": "2M", "option": "Full payment", "currency": "USD"}


class FakeGateway:
    name = "fake"

    def __init__(self):
        self.created = []
        self.result = {"state": "pending"}
        self.fail_create = None

    def create(self, **kw):
        if self.fail_create:
            raise GatewayError(self.fail_create)
        self.created.append(kw)
        return {"url": "https://checkout.example/" + kw["reference"], "payment_id": "pid-" + kw["reference"]}

    def check(self, *, reference, payment_id):
        self.seen = (reference, payment_id)
        return self.result


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.gw = FakeGateway()
        self.factory = lambda name=None: self.gw
        self.sf = lambda: self.store

    def test_register_prices_server_side(self):
        r = service.register({**BASE, "amount": 1}, self.sf, self.factory)
        self.assertTrue(r["requiresPayment"])
        self.assertEqual((r["amount"], r["currency"]), (600, "USD"))
        sent = self.gw.created[0]
        self.assertEqual((sent["amount"], sent["currency"], sent["email"]), (600, "USD", "ada@example.com"))
        self.assertEqual(sent["metadata"]["whatsapp"], "+234 801 234 5678")
        rec = self.store.find(r["reference"])[1]
        self.assertEqual((rec["Payment status"], rec["Amount due"], rec["Provider"], rec["Payment ID"]),
                         ("Pending payment", 600, "fake", "pid-" + r["reference"]))

    def test_validation_happens_before_store_or_gateway(self):
        def no_store():
            raise AssertionError("store touched")
        for field, value, msg in [("name", "Ada", "first and last"), ("email", "nope", "email"),
                                  ("phone", "12", "phone"), ("level", "Guru", "experience"),
                                  ("program", "3M", "program"), ("currency", "NGN", "currency")]:
            with self.assertRaises(service.UserError, msg=field) as ctx:
                service.register({**BASE, field: value}, no_store, self.factory)
            self.assertIn(msg, str(ctx.exception).lower())
        self.assertEqual(self.gw.created, [])

    def test_gateway_error_saves_nothing(self):
        self.gw.fail_create = "We could not start checkout: Currency not supported."
        with self.assertRaises(GatewayError):
            service.register(BASE, self.sf, self.factory)
        self.assertEqual(self.store.rows, [])

    def test_plan_without_terms_takes_no_payment(self):
        r = service.register({**BASE, "option": "Payment plan"}, self.sf, self.factory)
        self.assertFalse(r["requiresPayment"])
        self.assertEqual(self.gw.created, [])
        self.assertEqual(self.store.find(r["reference"])[1]["Payment status"], "Plan – awaiting terms")

    def test_verify_paths(self):
        ref = service.register(BASE, self.sf, self.factory)["reference"]
        self.assertEqual(service.verify(ref, self.store, self.factory)["status"], "Pending payment")
        self.assertEqual(self.gw.seen, (ref, "pid-" + ref))

        self.gw.result = {"state": "paid", "amount": 300, "currency": "USD"}
        self.assertEqual(service.verify(ref, self.store, self.factory)["status"], "Needs review")

        self.store.update(self.store.find(ref)[0], {"Payment status": "Pending payment"})
        self.gw.result = {"state": "paid", "amount": 600, "currency": "USD", "channel": "card",
                          "paid_at": datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)}
        out = service.verify(ref, self.store, self.factory)
        self.assertEqual((out["status"], out["amountPaid"]), ("Paid", 600))
        rec = self.store.find(ref)[1]
        self.assertEqual((rec["Channel"], rec["Paid at"]), ("card", "2026-10-09 16:00"))

        self.gw.result = {"state": "pending"}                     # already paid: provider not consulted
        self.assertEqual(service.verify(ref, self.store, self.factory)["status"], "Paid")

    def test_wrong_currency_needs_review(self):
        ref = service.register(BASE, self.sf, self.factory)["reference"]
        self.gw.result = {"state": "paid", "amount": 600, "currency": "NGN"}
        self.assertEqual(service.verify(ref, self.store, self.factory)["status"], "Needs review")

    def test_verify_rejects_bad_reference(self):
        for ref in ["", "x", "TA-../../etc", "TA-ZZZZZZ"]:
            with self.assertRaises(service.UserError):
                service.verify(ref, self.store, self.factory)

    def test_reconcile(self):
        old = service.register(BASE, self.sf, self.factory)["reference"]
        new = service.register(BASE, self.sf, self.factory)["reference"]
        stale = (datetime.now(timezone.utc) - timedelta(hours=30)).astimezone(
            ZoneInfo(config.TIMEZONE)).strftime(service.TS_FMT)
        self.store.update(self.store.find(old)[0], {"Registration date": stale})
        out = service.reconcile(self.store, self.factory)
        self.assertEqual((out["checked"], out["abandoned"], out["paid"]), (2, 1, 0))
        self.assertEqual(self.store.find(old)[1]["Payment status"], "Unpaid (abandoned)")
        self.assertEqual(self.store.find(new)[1]["Payment status"], "Pending payment")


if __name__ == "__main__":
    unittest.main()
