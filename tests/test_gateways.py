"""Gateway request/response handling with the HTTP layer mocked (no network)."""

import hashlib
import hmac
import json
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracer import gateways  # noqa: E402
from tracer.gateways import Flutterwave, GatewayError, Paystack, Stripe  # noqa: E402

KW = dict(reference="TA-261009-ABC123", amount=300, currency="USD", email="ada@example.com",
          product="1-Month DevOps Training", site_url="https://tracer-africa-devops.netlify.app/",
          metadata={"full_name": "Ada Okafor", "whatsapp": "+233 20 000 0000", "total_price": "300"})


class FlutterwaveTests(unittest.TestCase):
    def test_create(self):
        with mock.patch.object(gateways, "_http", return_value={
                "status": "success", "data": {"link": "https://checkout.flutterwave.com/v3/hosted/pay/x"}}) as h:
            out = Flutterwave("FLWSECK_TEST-x").create(**KW)
        method, url, headers, data = h.call_args.args
        body = json.loads(data)
        self.assertEqual((method, url), ("POST", "https://api.flutterwave.com/v3/payments"))
        self.assertEqual(headers["Authorization"], "Bearer FLWSECK_TEST-x")
        self.assertEqual((body["tx_ref"], body["amount"], body["currency"]), (KW["reference"], 300, "USD"))
        self.assertEqual(body["redirect_url"], KW["site_url"])
        self.assertEqual(body["customer"], {"email": "ada@example.com", "name": "Ada Okafor",
                                            "phonenumber": "+233 20 000 0000"})
        self.assertEqual(out, {"url": "https://checkout.flutterwave.com/v3/hosted/pay/x", "payment_id": KW["reference"]})

    def test_create_error_message_shown(self):
        with mock.patch.object(gateways, "_http", return_value={"status": "error", "message": "Invalid currency"}):
            with self.assertRaises(GatewayError) as ctx:
                Flutterwave("k").create(**KW)
        self.assertIn("Invalid currency", str(ctx.exception))

    def test_check_states(self):
        fw = Flutterwave("k")
        cases = [
            ({"status": "success", "data": {"tx_ref": KW["reference"], "status": "successful", "amount": 300,
                                            "currency": "USD", "payment_type": "card",
                                            "created_at": "2026-10-09T14:00:00.000Z"}}, "paid"),
            ({"status": "success", "data": {"tx_ref": KW["reference"], "status": "failed"}}, "failed"),
            ({"status": "success", "data": {"tx_ref": KW["reference"], "status": "pending"}}, "pending"),
            ({"status": "error", "message": "No transaction was found for this id"}, "pending"),
            ({"status": "success", "data": {"tx_ref": "TA-OTHER-000000", "status": "successful"}}, "pending"),
        ]
        for resp, want in cases:
            with mock.patch.object(gateways, "_http", return_value=resp) as h:
                got = fw.check(reference=KW["reference"], payment_id=KW["reference"])
            self.assertEqual(got["state"], want, resp)
        self.assertIn("/transactions/verify_by_reference?tx_ref=TA-261009-ABC123", h.call_args.args[1])
        with mock.patch.object(gateways, "_http", return_value=cases[0][0]):
            paid = fw.check(reference=KW["reference"], payment_id=None)
        self.assertEqual((paid["amount"], paid["currency"], paid["channel"]), (300.0, "USD", "card"))

    def test_webhook(self):
        body = json.dumps({"event": "charge.completed", "data": {"tx_ref": KW["reference"]}}).encode()
        self.assertEqual(Flutterwave.webhook_reference(body, {"verif-hash": "s3cret"}, "s3cret"), KW["reference"])
        self.assertIsNone(Flutterwave.webhook_reference(body, {"verif-hash": "wrong"}, "s3cret"))
        self.assertIsNone(Flutterwave.webhook_reference(body, {}, "s3cret"))
        self.assertIsNone(Flutterwave.webhook_reference(body, {"verif-hash": "s3cret"}, ""))
        other = json.dumps({"event": "transfer.completed", "data": {}}).encode()
        self.assertEqual(Flutterwave.webhook_reference(other, {"verif-hash": "s3cret"}, "s3cret"), "")

    def test_missing_key(self):
        with self.assertRaises(GatewayError):
            Flutterwave("")


class OtherGatewayWebhookTests(unittest.TestCase):
    def test_paystack_signature(self):
        body = json.dumps({"event": "charge.success", "data": {"reference": KW["reference"]}}).encode()
        sig = hmac.new(b"sk_test_x", body, hashlib.sha512).hexdigest()
        self.assertEqual(Paystack.webhook_reference(body, {"x-paystack-signature": sig}, "sk_test_x"), KW["reference"])
        self.assertIsNone(Paystack.webhook_reference(body, {"x-paystack-signature": sig}, "sk_test_y"))

    def test_stripe_signature(self):
        body = json.dumps({"type": "checkout.session.completed",
                           "data": {"object": {"client_reference_id": KW["reference"]}}}).encode()
        t = str(int(time.time()))
        v1 = hmac.new(b"whsec_x", f"{t}.".encode() + body, hashlib.sha256).hexdigest()
        self.assertEqual(Stripe.webhook_reference(body, {"stripe-signature": f"t={t},v1={v1}"}, "whsec_x"), KW["reference"])
        self.assertIsNone(Stripe.webhook_reference(body, {"stripe-signature": f"t={t},v1=bad"}, "whsec_x"))
        old = str(int(time.time()) - 3600)
        v1o = hmac.new(b"whsec_x", f"{old}.".encode() + body, hashlib.sha256).hexdigest()
        self.assertIsNone(Stripe.webhook_reference(body, {"stripe-signature": f"t={old},v1={v1o}"}, "whsec_x"))


if __name__ == "__main__":
    unittest.main()
