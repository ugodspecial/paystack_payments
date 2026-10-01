"""
tests/test_webhook.py
Unit tests for the webhook handler.

Patch strategy (definitive)
----------------------------
webhook.py loads Payment Logs in two steps:
  1. frappe.db.get_value("Paystack Payment Log", reference, "name")
  2. frappe.db.get_value("Paystack Payment Log", reference, "gateway_setting")
  3. frappe.get_doc("Paystack Payment Log", reference)   <- only after 1+2 pass

Tests control steps 1+2 via patch("frappe.db.get_value") with a side_effect
list. Step 3 (frappe.get_doc) only runs in tests that need it, using a
selective side_effect that returns the mock log only for "Paystack Payment Log"
and calls the real frappe.get_doc for everything else (System Settings etc.).

frappe.db.get_value and frappe.db.exists are always reliably patchable on
both Frappe v15 (Python 3.10/3.12) and v16 (Python 3.14) — they resolve
through the frappe.db module object, not through the app's own namespace.
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

_NOW = "2024-01-01 10:00:00"
_REAL_GET_DOC = frappe.get_doc  # capture before any patching


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
    log = MagicMock()
    log.name = name
    log.gateway_setting = gw_name
    log.reference_doctype = ""
    log.reference_docname = ""
    return log


def _get_doc_for_log(mock_log: MagicMock):
    """
    Returns a frappe.get_doc side_effect that yields mock_log only for
    'Paystack Payment Log' — all other doctypes go to the real frappe.get_doc.
    This prevents MagicMocks from reaching Frappe's Redis cache.
    """
    def _side_effect(doctype_or_dict, *args, **kwargs):
        if doctype_or_dict == "Paystack Payment Log":
            return mock_log
        return _REAL_GET_DOC(doctype_or_dict, *args, **kwargs)
    return _side_effect


class TestWebhookSignatureResolution(FrappeTestCase):

    def test_handle_webhook_bad_sig_returns_safely(self):
        """Bad signature returns silently with no exception."""
        body = json.dumps({"event": "charge.success", "data": {}}).encode()
        with patch("paystack_payments.gateway.webhook._resolve_gateway", return_value=None):
            handle_webhook(body, "badsig", "1.2.3.4")


class TestIPAllowlist(FrappeTestCase):

    def _gw(self, ips: str) -> MagicMock:
        gw = MagicMock()
        gw.allowed_webhook_ips = ips
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
        self.assertFalse(_ip_allowed("8.8.8.8", self._gw("52.31.139.75\n52.49.173.169")))

    def test_invalid_source_ip_rejected(self):
        self.assertFalse(_ip_allowed("not-an-ip", self._gw("52.31.139.75")))


class TestChargeSuccess(FrappeTestCase):
    """
    _on_charge_success now uses frappe.db.get_value (steps 1+2) before
    frappe.get_doc (step 3). Tests patch frappe.db.get_value with a
    side_effect list matching the call order:
      call 1: get_value(..., "name")       -> log name or None
      call 2: get_value(..., "gateway_setting") -> gateway name
    """

    def test_missing_reference_ignored(self):
        _on_charge_success({"id": "TXN-001", "amount": 5000}, _make_gw())

    def test_missing_txn_id_ignored(self):
        _on_charge_success({"reference": "LOG-001", "amount": 5000}, _make_gw())

    def test_duplicate_txn_skipped(self):
        with patch("frappe.db.exists", return_value="LOG-EXISTING"):
            _on_charge_success(
                {"reference": "LOG-001", "id": "TXN-DUP", "amount": 5000},
                _make_gw(),
            )

    def test_unknown_reference_skipped(self):
        # db.get_value returns None for "name" -> handler returns early
        with patch("frappe.db.exists", return_value=None), \
             patch("frappe.db.get_value", return_value=None):
            _on_charge_success(
                {"reference": "UNKNOWN", "id": "TXN-001", "amount": 5000},
                _make_gw(),
            )

    def test_cross_gateway_rejected(self):
        """
        Log belongs to GW-001 but webhook arrives at GW-002.
        mark_processed must NOT be called.
        """
        gw = _make_gw("GW-002")

        # call 1: get_value(..., "name") -> "LOG-001"  (log exists)
        # call 2: get_value(..., "gateway_setting") -> "GW-001"  (different gateway)
        with patch("frappe.db.exists", return_value=None), \
             patch("frappe.db.get_value", side_effect=["LOG-001", "GW-001"]), \
             patch("frappe.log_error"):
            _on_charge_success(
                {"reference": "LOG-001", "id": "TXN-001", "amount": 5000},
                gw,
            )
        # frappe.get_doc is never called because scope check fails first

    def test_mark_processed_called_on_valid_event(self):
        """Valid charge.success calls mark_processed with correct arguments."""
        gw = _make_gw("GW-001")
        log = _make_log("LOG-001", "GW-001")

        # call 1: get_value(..., "name") -> "LOG-001"
        # call 2: get_value(..., "gateway_setting") -> "GW-001"
        with patch("frappe.db.exists", return_value=None), \
             patch("frappe.db.get_value", side_effect=["LOG-001", "GW-001"]), \
             patch("frappe.get_doc", side_effect=_get_doc_for_log(log)), \
             patch("paystack_payments.gateway.webhook._maybe_store_authorization"), \
             patch("paystack_payments.payment.lifecycle.on_payment_success"), \
             patch("paystack_payments.gateway.webhook._now_str", return_value=_NOW), \
             patch("frappe.log_error"):
            _on_charge_success(
                {
                    "reference": "LOG-001",
                    "id": "TXN-001",
                    "amount": 500000,  # kobo -> 5000.0 NGN
                    "fees": 7500,      # kobo -> 75.0 NGN
                    "paid_at": "2024-01-01T10:00:00Z",
                },
                gw,
            )

        log.mark_processed.assert_called_once_with(
            txn_id="TXN-001",
            amount_paid=5000.0,
            fee=75.0,
            payment_date="2024-01-01T10:00:00Z",
        )


class TestChargeFailed(FrappeTestCase):
    """
    _on_charge_failed also uses frappe.db.get_value (steps 1+2) before
    frappe.get_doc (step 3).
    """

    def test_charge_failed_marks_log(self):
        """charge.failed calls mark_failed with the gateway_response."""
        gw = _make_gw("GW-001")
        log = _make_log("LOG-001", "GW-001")

        with patch("frappe.db.get_value", side_effect=["LOG-001", "GW-001"]), \
             patch("frappe.get_doc", side_effect=_get_doc_for_log(log)), \
             patch("paystack_payments.payment.lifecycle.on_payment_failed"), \
             patch("frappe.log_error"):
            _on_charge_failed(
                {"reference": "LOG-001", "gateway_response": "Insufficient funds"},
                gw,
            )

        log.mark_failed.assert_called_once_with("Insufficient funds")

    def test_charge_failed_default_reason(self):
        """charge.failed with no gateway_response uses 'Charge failed'."""
        gw = _make_gw("GW-001")
        log = _make_log("LOG-001", "GW-001")

        with patch("frappe.db.get_value", side_effect=["LOG-001", "GW-001"]), \
             patch("frappe.get_doc", side_effect=_get_doc_for_log(log)), \
             patch("paystack_payments.payment.lifecycle.on_payment_failed"), \
             patch("frappe.log_error"):
            _on_charge_failed({"reference": "LOG-001"}, gw)

        log.mark_failed.assert_called_once_with("Charge failed")

    def test_charge_failed_missing_reference_ignored(self):
        _on_charge_failed({}, _make_gw())

    def test_charge_failed_wrong_gateway_ignored(self):
        """charge.failed for a different gateway does not call mark_failed."""
        gw = _make_gw("GW-002")
        log = _make_log("LOG-001", "GW-001")

        with patch("frappe.db.get_value", side_effect=["LOG-001", "GW-001"]), \
             patch("frappe.log_error"):
            _on_charge_failed(
                {"reference": "LOG-001", "gateway_response": "Failed"},
                gw,
            )

        log.mark_failed.assert_not_called()

    def test_charge_failed_unknown_reference_ignored(self):
        """charge.failed for unknown reference is silently ignored."""
        with patch("frappe.db.get_value", return_value=None):
            _on_charge_failed({"reference": "UNKNOWN", "gateway_response": "Failed"}, _make_gw())


class TestRefundProcessed(FrappeTestCase):

    def test_refund_processed_updates_log(self):
        """refund.processed sets paystack_refund_id and amount on the Refund Log."""
        refund_log = MagicMock()

        def _get_doc_refund(doctype, *args, **kwargs):
            if doctype == "Paystack Refund Log":
                return refund_log
            return _REAL_GET_DOC(doctype, *args, **kwargs)

        with patch("frappe.db.get_value") as mock_dbv, \
             patch("frappe.get_doc", side_effect=_get_doc_refund), \
             patch("paystack_payments.payment.lifecycle.on_refund_processed"), \
             patch("frappe.log_error"):
            mock_dbv.side_effect = ["LOG-001", "REFUND-LOG-001"]

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
        """refund.processed for unknown txn_id is silently ignored."""
        with patch("frappe.db.get_value", return_value=None):
            _on_refund_processed(
                {"transaction": {"id": "UNKNOWN"}, "id": "REF-001", "amount": 5000},
                _make_gw(),
            )


class TestSettlementSuccess(FrappeTestCase):

    def test_settlement_created_and_lifecycle_called(self):
        """settlement.success creates a Settlement doc and calls the lifecycle."""
        gw = _make_gw("GW-001")
        gw.currency = "NGN"
        settlement = MagicMock()

        with patch("frappe.db.exists", return_value=None), \
             patch("frappe.new_doc", return_value=settlement), \
             patch("frappe.db.commit"), \
             patch("frappe.utils.today", return_value="2024-01-01"), \
             patch("paystack_payments.payment.lifecycle.on_settlement_received") as mock_lc, \
             patch("frappe.log_error"):
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
        """A settlement already in the DB is silently ignored."""
        with patch("frappe.db.exists", return_value="STL-001"):
            _on_settlement_success({"id": "STL-DUP"}, _make_gw())
