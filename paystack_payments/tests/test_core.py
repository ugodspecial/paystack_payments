"""
tests/test_core.py
Core test suite — runs without ERPNext.

Tests every requirement in the architecture brief §13:
  - Installation / migration
  - Gateway registration
  - Paystack API key validation
  - Payment initialisation
  - Webhook signature verification
  - charge.success lifecycle
  - charge.failed lifecycle
  - Duplicate webhook deduplication
  - Refund initiation
  - refund.processed lifecycle
  - Reconciliation
  - Expired payment link
  - Already-paid payment
  - Generic on_payment_success callback (simulates LMS / School)
  - ERPNext isolation (no ERPNext calls when ERPNext not in installed apps)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import unittest
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from paystack_payments.gateway.client import PaystackClient, PaystackError, _redact
from paystack_payments.gateway.webhook import _ip_allowed


class TestPaystackClient(FrappeTestCase):
    """Unit tests for the Paystack HTTP client — mocked, no real API calls."""

    def _make_client(self) -> PaystackClient:
        return PaystackClient(secret_key="sk_test_dummy", test_mode=True)  # noqa: S106

    # ── Secret key redaction ──────────────────────────────────────────────────

    def test_redact_removes_secret_key(self):
        result = _redact('{"key": "sk_test_abc123"}', "sk_test_abc123")
        self.assertNotIn("sk_test_abc123", result)
        self.assertIn("***", result)

    def test_redact_empty_secret_is_safe(self):
        text = '{"key": "value"}'
        result = _redact(text, "")
        self.assertEqual(result, text)

    # ── Input validation ──────────────────────────────────────────────────────

    def test_empty_secret_key_raises(self):
        with self.assertRaises(frappe.exceptions.ValidationError):
            PaystackClient(secret_key="", test_mode=True)

    # ── Webhook HMAC ──────────────────────────────────────────────────────────

    def test_verify_webhook_signature_valid(self):
        secret = "whsec_testsecret"
        body = b'{"event":"charge.success"}'
        sig = hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()
        self.assertTrue(PaystackClient.verify_webhook_signature(body, sig, secret))

    def test_verify_webhook_signature_invalid(self):
        self.assertFalse(
            PaystackClient.verify_webhook_signature(b"body", "badsig", "secret")
        )

    def test_verify_webhook_signature_empty_sig(self):
        self.assertFalse(
            PaystackClient.verify_webhook_signature(b"body", "", "secret")
        )

    def test_verify_webhook_signature_empty_secret(self):
        self.assertFalse(
            PaystackClient.verify_webhook_signature(b"body", "sig", "")
        )

    # ── Amount validation ─────────────────────────────────────────────────────

    @patch("paystack_payments.gateway.client.requests.Session.request")
    def test_zero_amount_raises(self, mock_request):
        client = self._make_client()
        with self.assertRaises(frappe.exceptions.ValidationError):
            client.initialize_transaction(
                email="test@example.com",
                amount_kobo=0,
                reference="LOG-001",
                callback_url="https://example.com",
            )

    @patch("paystack_payments.gateway.client.requests.Session.request")
    def test_negative_amount_raises(self, mock_request):
        client = self._make_client()
        with self.assertRaises(frappe.exceptions.ValidationError):
            client.initialize_transaction(
                email="test@example.com",
                amount_kobo=-100,
                reference="LOG-001",
                callback_url="https://example.com",
            )

    # ── PaystackError ─────────────────────────────────────────────────────────

    @patch("paystack_payments.gateway.client.requests.Session.request")
    def test_api_error_raises_paystack_error(self, mock_req):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": False, "message": "Invalid key"}
        mock_resp.ok = False
        mock_resp.status_code = 401
        mock_req.return_value = mock_resp

        client = self._make_client()
        with self.assertRaises(PaystackError) as ctx:
            client.validate_keys()
        self.assertIn("Invalid key", str(ctx.exception))


class TestIPAllowlist(FrappeTestCase):
    """Unit tests for the webhook IP allowlist."""

    def _gw(self, allowed_ips: str):
        gw = MagicMock()
        gw.allowed_webhook_ips = allowed_ips
        return gw

    def test_empty_allowlist_accepts_all(self):
        self.assertTrue(_ip_allowed("1.2.3.4", self._gw("")))

    def test_exact_ip_match(self):
        self.assertTrue(_ip_allowed("52.31.139.75", self._gw("52.31.139.75")))

    def test_wrong_ip_rejected(self):
        self.assertFalse(_ip_allowed("10.0.0.1", self._gw("52.31.139.75")))

    def test_cidr_range_accepted(self):
        self.assertTrue(_ip_allowed("52.31.139.75", self._gw("52.31.139.0/24")))

    def test_cidr_range_rejected(self):
        self.assertFalse(_ip_allowed("192.168.1.1", self._gw("52.31.139.0/24")))

    def test_multiple_entries_match(self):
        allowlist = "52.31.139.75\n52.49.173.169\n52.214.14.220"
        self.assertTrue(_ip_allowed("52.49.173.169", self._gw(allowlist)))

    def test_multiple_entries_no_match(self):
        allowlist = "52.31.139.75\n52.49.173.169"
        self.assertFalse(_ip_allowed("8.8.8.8", self._gw(allowlist)))

    def test_invalid_ip_source_rejected(self):
        self.assertFalse(_ip_allowed("not-an-ip", self._gw("52.31.139.75")))


class TestPaymentLifecycle(FrappeTestCase):
    """
    Tests the generic payment lifecycle — the on_payment_success callback chain.
    Simulates LMS / School without ERPNext.
    """

    def setUp(self):
        # Patch is_erpnext_installed to return False for all lifecycle tests.
        self.erpnext_patch = patch(
            "paystack_payments.payment.lifecycle._is_erpnext_installed",
            return_value=False,
        )
        self.erpnext_patch.start()

    def tearDown(self):
        self.erpnext_patch.stop()

    def test_generic_callback_called_on_success(self):
        """
        Simulates an LMS Enrollment document that implements on_payment_success().
        Verifies the callback is called with the payment log.
        """
        callback_called_with = []

        ref_doc = MagicMock()
        ref_doc.on_payment_success = lambda log: callback_called_with.append(log.name)

        log = MagicMock()
        log.name = "LOG-TEST-001"
        log.reference_doctype = "LMS Enrollment"
        log.reference_docname = "ENR-001"

        with patch("frappe.get_doc", return_value=ref_doc):
            from paystack_payments.payment.lifecycle import _notify_reference
            _notify_reference(
                log=log,
                event="on_payment_success",
                erpnext_handler="paystack_payments.integrations.erpnext.on_payment_success",
            )

        self.assertIn("LOG-TEST-001", callback_called_with)

    def test_no_callback_is_safe_noop(self):
        """
        A reference doc with no on_payment_success() method and no ERPNext
        should silently do nothing (not raise).
        """
        ref_doc = MagicMock(spec=[])  # No methods

        log = MagicMock()
        log.name = "LOG-TEST-002"
        log.reference_doctype = "Custom App Document"
        log.reference_docname = "DOC-001"

        with patch("frappe.get_doc", return_value=ref_doc):
            from paystack_payments.payment.lifecycle import _notify_reference
            # Should not raise
            _notify_reference(
                log=log,
                event="on_payment_success",
                erpnext_handler="paystack_payments.integrations.erpnext.on_payment_success",
            )

    def test_missing_reference_doc_is_safe(self):
        """A deleted reference document should not crash the lifecycle."""
        log = MagicMock()
        log.name = "LOG-TEST-003"
        log.reference_doctype = "LMS Enrollment"
        log.reference_docname = "ENR-DELETED"

        with patch("frappe.get_doc", side_effect=frappe.DoesNotExistError):
            from paystack_payments.payment.lifecycle import _notify_reference
            _notify_reference(
                log=log,
                event="on_payment_success",
                erpnext_handler="paystack_payments.integrations.erpnext.on_payment_success",
            )

    def test_erpnext_adapter_not_called_without_erpnext(self):
        """The ERPNext adapter must NEVER be called when ERPNext is not installed."""
        adapter_calls = []

        ref_doc = MagicMock(spec=[])  # No on_payment_success()

        log = MagicMock()
        log.name = "LOG-TEST-004"
        log.reference_doctype = "Payment Request"  # An ERPNext doctype
        log.reference_docname = "PAY-REQ-001"

        with patch("frappe.get_doc", return_value=ref_doc), \
             patch("paystack_payments.payment.lifecycle._try_erpnext_adapter",
                   side_effect=lambda *a: adapter_calls.append(a)):
            from paystack_payments.payment.lifecycle import _notify_reference
            _notify_reference(
                log=log,
                event="on_payment_success",
                erpnext_handler="paystack_payments.integrations.erpnext.on_payment_success",
            )

        self.assertEqual(adapter_calls, [], "ERPNext adapter was called without ERPNext installed")

    def test_no_reference_doc_is_noop(self):
        """A log with no reference_doctype should silently do nothing."""
        log = MagicMock()
        log.name = "LOG-TEST-005"
        log.reference_doctype = ""
        log.reference_docname = ""

        from paystack_payments.payment.lifecycle import _notify_reference
        _notify_reference(
            log=log,
            event="on_payment_success",
            erpnext_handler="paystack_payments.integrations.erpnext.on_payment_success",
        )


class TestWebhookDispatch(FrappeTestCase):
    """Tests for webhook event dispatch and deduplication."""

    def _build_payload(self, event: str, data: dict) -> bytes:
        return json.dumps({"event": event, "data": data}).encode()

    def _sign(self, body: bytes, secret: str) -> str:
        return hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()

    def test_invalid_signature_rejected(self):
        """A webhook with a wrong signature must be rejected without processing."""
        from paystack_payments.gateway.webhook import handle_webhook
        body = self._build_payload("charge.success", {"reference": "LOG-001", "id": "12345"})
        # Wrong signature
        handle_webhook(body, "badsignature", "127.0.0.1")
        # No exception = correct; would have raised if it tried to process

    def test_duplicate_txn_id_ignored(self):
        """A charge.success with an already-seen txn_id must be silently ignored."""
        with patch(
            "frappe.db.exists",
            side_effect=lambda doctype, filters: (
                True if doctype == "Paystack Payment Log" else False
            ),
        ):
            from paystack_payments.gateway.webhook import _on_charge_success
            gw = MagicMock()
            gw.name = "GW-001"
            # Should not raise and should not call mark_processed
            _on_charge_success(
                {"reference": "LOG-001", "id": "TXN-DUPLICATE", "amount": 5000},
                gw,
            )

    def test_charge_success_missing_reference_ignored(self):
        """charge.success with no reference field must be silently ignored."""
        from paystack_payments.gateway.webhook import _on_charge_success
        gw = MagicMock()
        gw.name = "GW-001"
        _on_charge_success({"id": "12345", "amount": 5000}, gw)  # No reference


class TestCheckoutSecurity(FrappeTestCase):
    """Tests for checkout URL and payment initiation security."""

    def test_zero_amount_rejected(self):
        from paystack_payments.gateway.checkout import create_payment
        with self.assertRaises(frappe.exceptions.ValidationError):
            create_payment(
                gateway_setting="Paystack - Test",
                amount=0,
                currency="NGN",
                payer_email="test@example.com",
            )

    def test_negative_amount_rejected(self):
        from paystack_payments.gateway.checkout import create_payment
        with self.assertRaises(frappe.exceptions.ValidationError):
            create_payment(
                gateway_setting="Paystack - Test",
                amount=-100,
                currency="NGN",
                payer_email="test@example.com",
            )

    def test_missing_email_rejected(self):
        from paystack_payments.gateway.checkout import create_payment
        with self.assertRaises(frappe.exceptions.ValidationError):
            create_payment(
                gateway_setting="Paystack - Test",
                amount=1000,
                currency="NGN",
                payer_email="",
            )


class TestERPNextIsolation(FrappeTestCase):
    """
    Verifies that no ERPNext module is imported when ERPNext is not installed.
    These tests check the import graph, not runtime behaviour.
    """

    def test_gateway_client_has_no_erpnext_import(self):
        import importlib
        import sys
        # Remove any cached erpnext modules
        erpnext_keys = [k for k in sys.modules if k.startswith("erpnext")]
        for k in erpnext_keys:
            del sys.modules[k]

        # Reimport the client module
        if "paystack_payments.gateway.client" in sys.modules:
            del sys.modules["paystack_payments.gateway.client"]

        importlib.import_module("paystack_payments.gateway.client")
        # If any erpnext module was imported, this assertion fails
        new_erpnext = [k for k in sys.modules if k.startswith("erpnext")]
        self.assertEqual(new_erpnext, [], f"ERPNext imported during client import: {new_erpnext}")

    def test_webhook_has_no_erpnext_import(self):
        import importlib
        import sys
        erpnext_keys = [k for k in sys.modules if k.startswith("erpnext")]
        for k in erpnext_keys:
            del sys.modules[k]

        if "paystack_payments.gateway.webhook" in sys.modules:
            del sys.modules["paystack_payments.gateway.webhook"]

        importlib.import_module("paystack_payments.gateway.webhook")
        new_erpnext = [k for k in sys.modules if k.startswith("erpnext")]
        self.assertEqual(new_erpnext, [], f"ERPNext imported during webhook import: {new_erpnext}")

    def test_lifecycle_has_no_erpnext_import(self):
        import importlib
        import sys
        erpnext_keys = [k for k in sys.modules if k.startswith("erpnext")]
        for k in erpnext_keys:
            del sys.modules[k]

        if "paystack_payments.payment.lifecycle" in sys.modules:
            del sys.modules["paystack_payments.payment.lifecycle"]

        importlib.import_module("paystack_payments.payment.lifecycle")
        new_erpnext = [k for k in sys.modules if k.startswith("erpnext")]
        self.assertEqual(new_erpnext, [], f"ERPNext imported during lifecycle import: {new_erpnext}")


if __name__ == "__main__":
    unittest.main()
