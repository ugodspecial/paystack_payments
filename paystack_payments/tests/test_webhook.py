"""
tests/test_webhook.py
Focused unit tests for the webhook handler.

Patch strategy
--------------
frappe.get_doc is called for MULTIPLE doctypes during a single webhook handler
invocation (Paystack Payment Log, System Settings via now_datetime, etc.).
Patching it with a blanket return_value=MagicMock causes Frappe's Redis cache
to try to pickle the mock when it intercepts the System Settings lookup,
raising _pickle.PicklingError.

Fix: use a selective side_effect that only intercepts Paystack Payment Log
fetches and passes everything else to the real frappe.get_doc. Also patch
frappe.utils.data.now_datetime to avoid the Redis/System Settings call
entirely in tests where we control the payload's paid_at field.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from paystack_payments.gateway.webhook import (
    _ip_allowed,
    _on_charge_failed,
    _on_charge_success,
    _on_refund_processed,
    _on_settlement_success,
    handle_webhook,
)

# A fixed datetime string used to avoid calling now_datetime() in tests.
_FIXED_DATETIME = "2024-01-01T10:00:00Z"


def _make_gw(name: str = "GW-001", secret: str = "whsec_test") -> MagicMock:  # noqa: S107
    gw = MagicMock()
    gw.name = name
    gw.currency = "NGN"
    gw.allowed_webhook_ips = ""
    gw.get_password = lambda field: secret if "secret" in field else ""
    return gw


def _sign(body: bytes, secret: str = "whsec_test") -> str:  # noqa: S107
    return hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()


def _make_log(name: str = "LOG-001", gw_name: str = "GW-001") -> MagicMock:
    """Build a mock Payment Log document."""
    log = MagicMock()
    log.name = name
    log.gateway_setting = gw_name
    log.reference_doctype = ""
    log.reference_docname = ""
    return log


def _selective_get_doc(mock_log, real_get_doc=frappe.get_doc):
    """
    Return a side_effect function for patch("frappe.get_doc") that:
      - Returns mock_log when called for "Paystack Payment Log"
      - Calls the real frappe.get_doc for everything else (System Settings, etc.)

    This prevents MagicMock objects from leaking into Frappe's Redis cache
    and causing PicklingError on Python 3.14 / Frappe v16.
    """
    def _side_effect(doctype, *args, **kwargs):
        if doctype == "Paystack Payment Log":
            return mock_log
        return real_get_doc(doctype, *args, **kwargs)
    return _side_effect


class TestWebhookSignatureResolution(FrappeTestCase):

    def test_handle_webhook_bad_sig_returns_safely(self):
        """Bad signature must silently return — no exception, no processing."""
        body = json.dumps({"event": "charge.success", "data": {}}).encode()
        with patch("paystack_payments.gateway.webhook._resolve_gateway", return_value=None):
            handle_webhook(body, "badsig", "1.2.3.4")  # must not raise


class TestIPAllowlist(FrappeTestCase):

    def _gw(self, allowed_ips: str) -> MagicMock:
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
        self.assertTrue(
            _ip_allowed("52.49.173.169", self._gw("52.31.139.75\n52.49.173.169"))
        )

    def test_multiple_entries_no_match(self):
        self.assertFalse(
            _ip_allowed("8.8.8.8", self._gw("52.31.139.75\n52.49.173.169"))
        )

    def test_invalid_source_ip_rejected(self):
        self.assertFalse(_ip_allowed("not-an-ip", self._gw("52.31.139.75")))


class TestChargeSuccess(FrappeTestCase):

    def test_duplicate_txn_skipped(self):
        """A txn_id already in the DB must be silently ignored — idempotency."""
        gw = _make_gw()
        with patch("frappe.db.exists", return_value="LOG-EXISTING"):
            _on_charge_success(
                {"reference": "LOG-001", "id": "TXN-DUP", "amount": 5000,
                 "paid_at": _FIXED_DATETIME},
                gw,
            )

    def test_missing_reference_ignored(self):
        """charge.success with no reference field must be silently ignored."""
        gw = _make_gw()
        _on_charge_success({"id": "TXN-001", "amount": 5000}, gw)

    def test_missing_txn_id_ignored(self):
        """charge.success with no id field must be silently ignored."""
        gw = _make_gw()
        _on_charge_success({"reference": "LOG-001", "amount": 5000}, gw)

    def test_unknown_reference_skipped(self):
        """Webhook for a reference with no matching Payment Log is ignored."""
        gw = _make_gw()
        with patch("frappe.db.exists", return_value=None), \
             patch("frappe.get_doc", side_effect=frappe.DoesNotExistError):
            _on_charge_success(
                {"reference": "UNKNOWN", "id": "TXN-001", "amount": 5000,
                 "paid_at": _FIXED_DATETIME},
                gw,
            )

    def test_cross_gateway_rejected(self):
        """
        A log belonging to GW-001 must be rejected when the webhook arrives
        at GW-002 — prevents cross-company spoofing.
        mark_processed must NOT be called.

        Uses _selective_get_doc so only Paystack Payment Log calls are
        intercepted; System Settings and other frappe.get_doc calls are
        passed to the real implementation to avoid PicklingError.
        """
        gw = _make_gw("GW-002")
        log = _make_log("LOG-001", "GW-001")  # belongs to a different gateway

        with patch("frappe.db.exists", return_value=None), \
             patch("frappe.get_doc", side_effect=_selective_get_doc(log)), \
             patch("frappe.log_error"):
            _on_charge_success(
                {"reference": "LOG-001", "id": "TXN-001", "amount": 5000,
                 "paid_at": _FIXED_DATETIME},
                gw,
            )

        log.mark_processed.assert_not_called()

    def test_mark_processed_called_on_valid_event(self):
        """
        A valid charge.success must call mark_processed with the correct
        arguments derived from the Paystack payload.
        """
        gw = _make_gw("GW-001")
        log = _make_log("LOG-001", "GW-001")

        with patch("frappe.db.exists", return_value=None), \
             patch("frappe.get_doc", side_effect=_selective_get_doc(log)), \
             patch("paystack_payments.gateway.webhook._maybe_store_authorization"), \
             patch("paystack_payments.payment.lifecycle.on_payment_success"), \
             patch("frappe.log_error"):
            _on_charge_success(
                {
                    "reference": "LOG-001",
                    "id": "TXN-001",
                    "amount": 500000,   # kobo -> 5000.0 NGN
                    "fees": 7500,       # kobo -> 75.0 NGN
                    "paid_at": _FIXED_DATETIME,
                },
                gw,
            )

        log.mark_processed.assert_called_once_with(
            txn_id="TXN-001",
            amount_paid=5000.0,
            fee=75.0,
            payment_date=_FIXED_DATETIME,
        )


class TestChargeFailed(FrappeTestCase):

    def test_charge_failed_marks_log(self):
        """
        charge.failed must call mark_failed with the gateway_response
        from the Paystack payload.
        """
        gw = _make_gw("GW-001")
        log = _make_log("LOG-001", "GW-001")

        with patch("frappe.get_doc", side_effect=_selective_get_doc(log)), \
             patch("paystack_payments.payment.lifecycle.on_payment_failed"), \
             patch("frappe.log_error"):
            _on_charge_failed(
                {"reference": "LOG-001", "gateway_response": "Insufficient funds"},
                gw,
            )

        log.mark_failed.assert_called_once_with("Insufficient funds")

    def test_charge_failed_default_reason(self):
        """charge.failed with no gateway_response falls back to 'Charge failed'."""
        gw = _make_gw("GW-001")
        log = _make_log("LOG-001", "GW-001")

        with patch("frappe.get_doc", side_effect=_selective_get_doc(log)), \
             patch("paystack_payments.payment.lifecycle.on_payment_failed"), \
             patch("frappe.log_error"):
            _on_charge_failed({"reference": "LOG-001"}, gw)

        log.mark_failed.assert_called_once_with("Charge failed")

    def test_charge_failed_missing_reference_ignored(self):
        """charge.failed with no reference must be silently ignored."""
        gw = _make_gw()
        _on_charge_failed({}, gw)  # must not raise

    def test_charge_failed_wrong_gateway_ignored(self):
        """charge.failed for a log on a different gateway must not call mark_failed."""
        gw = _make_gw("GW-002")
        log = _make_log("LOG-001", "GW-001")

        with patch("frappe.get_doc", side_effect=_selective_get_doc(log)), \
             patch("paystack_payments.payment.lifecycle.on_payment_failed"), \
             patch("frappe.log_error"):
            _on_charge_failed(
                {"reference": "LOG-001", "gateway_response": "Failed"},
                gw,
            )

        log.mark_failed.assert_not_called()


class TestRefundProcessed(FrappeTestCase):

    def test_refund_processed_updates_log(self):
        """refund.processed must set paystack_refund_id and amount on the Refund Log."""
        refund_log = MagicMock()
        _real_get_doc = frappe.get_doc

        # Intercept both Paystack Payment Log and Paystack Refund Log;
        # let System Settings and everything else through to the real implementation.
        def _get_doc_refund(doctype, *args, **kwargs):
            if doctype in ("Paystack Payment Log", "Paystack Refund Log"):
                return refund_log
            return _real_get_doc(doctype, *args, **kwargs)

        with patch("frappe.db.get_value") as mock_get_value, \
             patch("frappe.get_doc", side_effect=_get_doc_refund), \
             patch("paystack_payments.payment.lifecycle.on_refund_processed"), \
             patch("frappe.log_error"):

            # First db.get_value: Payment Log lookup by txn_id
            # Second db.get_value: Refund Log lookup
            mock_get_value.side_effect = ["LOG-001", "REFUND-LOG-001"]

            _on_refund_processed(
                {
                    "transaction": {"id": "TXN-001"},
                    "id": "REFUND-001",
                    "amount": 100000,  # kobo -> 1000.0 NGN
                },
                _make_gw(),
            )

        self.assertEqual(refund_log.paystack_refund_id, "REFUND-001")
        self.assertEqual(refund_log.amount, 1000.0)

    def test_refund_no_matching_log_ignored(self):
        """refund.processed for an unknown txn_id must be silently ignored."""
        with patch("frappe.db.get_value", return_value=None):
            _on_refund_processed(
                {"transaction": {"id": "UNKNOWN"}, "id": "REF-001", "amount": 5000},
                _make_gw(),
            )


class TestSettlementSuccess(FrappeTestCase):

    def test_settlement_created_and_lifecycle_called(self):
        """settlement.success must create a Settlement doc and call the lifecycle."""
        gw = _make_gw("GW-001")
        gw.currency = "NGN"
        settlement = MagicMock()

        def _get_doc_settlement(doctype, *args, **kwargs):
            if doctype == "Paystack Settlement":
                return settlement
            return frappe.get_doc(doctype, *args, **kwargs)

        with patch("frappe.db.exists", return_value=None), \
             patch("frappe.get_doc", side_effect=_get_doc_settlement), \
             patch("frappe.db.commit"), \
             patch("paystack_payments.payment.lifecycle.on_settlement_received") as mock_lc, \
             patch("frappe.log_error"):

            settlement.insert.return_value = None

            _on_settlement_success(
                {
                    "id": "STL-001",
                    "total_amount": 50000000,
                    "total_fees": 500000,
                    "total_processed": 0,
                    "settlement_amount": 49500000,
                    "settled_at": "2024-01-01",
                },
                gw,
            )

        mock_lc.assert_called_once_with(settlement)

    def test_duplicate_settlement_ignored(self):
        """A settlement already in the DB must be silently ignored."""
        gw = _make_gw()
        with patch("frappe.db.exists", return_value="STL-001"):
            _on_settlement_success({"id": "STL-DUP"}, gw)
