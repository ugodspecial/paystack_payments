"""
tests/test_webhook.py
Focused unit tests for the webhook handler.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import unittest
from unittest.mock import MagicMock, patch

from frappe.tests.utils import FrappeTestCase

from paystack_payments.gateway.webhook import (
    _on_charge_failed,
    _on_charge_success,
    _on_refund_processed,
    _on_settlement_success,
    handle_webhook,
)


def _make_gw(name: str = "GW-001", secret: str = "whsec_test") -> MagicMock:  # noqa: S107
    gw = MagicMock()
    gw.name = name
    gw.currency = "NGN"
    gw.allowed_webhook_ips = ""
    gw.get_password = lambda field: secret if "secret" in field else ""
    return gw


def _sign(body: bytes, secret: str = "whsec_test") -> str:  # noqa: S107
    return hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()


class TestWebhookSignatureResolution(FrappeTestCase):

    def test_handle_webhook_bad_sig_returns_safely(self):
        """Bad signature must silently return — no exception, no processing."""
        body = json.dumps({"event": "charge.success", "data": {}}).encode()
        with patch(
            "paystack_payments.gateway.webhook._resolve_gateway", return_value=None
        ):
            handle_webhook(body, "badsig", "1.2.3.4")  # must not raise


class TestChargeSuccess(FrappeTestCase):

    def _make_log(self, name: str = "LOG-001", gw_name: str = "GW-001") -> MagicMock:
        log = MagicMock()
        log.name = name
        log.gateway_setting = gw_name
        log.reference_doctype = ""
        log.reference_docname = ""
        return log

    def test_duplicate_txn_skipped(self):
        """Duplicate txn_id must be ignored — idempotency."""
        gw = _make_gw()
        with patch("frappe.db.exists", return_value=True):
            _on_charge_success({"reference": "LOG-001", "id": "TXN-DUP", "amount": 5000}, gw)
            # No exception = correct

    def test_unknown_reference_skipped(self):
        """Webhook for unknown reference must be ignored."""
        gw = _make_gw()
        with patch("frappe.db.exists", return_value=False), \
             patch("paystack_payments.gateway.webhook._get_log", return_value=None):
            _on_charge_success({"reference": "UNKNOWN", "id": "TXN-001", "amount": 5000}, gw)

    def test_cross_gateway_rejected(self):
        """
        A log belonging to GW-001 must be rejected if the webhook arrived
        at GW-002 (cross-company spoofing prevention).
        """
        gw = _make_gw("GW-002")
        log = self._make_log("LOG-001", "GW-001")  # Different gateway

        with patch("frappe.db.exists", return_value=False), \
             patch("paystack_payments.gateway.webhook._get_log", return_value=log):
            _on_charge_success(
                {"reference": "LOG-001", "id": "TXN-001", "amount": 5000},
                gw,
            )
            log.mark_processed.assert_not_called()

    def test_mark_processed_called_on_valid_event(self):
        """Valid charge.success must call mark_processed on the log."""
        gw = _make_gw("GW-001")
        log = self._make_log("LOG-001", "GW-001")

        with patch("frappe.db.exists", return_value=False), \
             patch("paystack_payments.gateway.webhook._get_log", return_value=log), \
             patch("paystack_payments.gateway.webhook._maybe_store_authorization"), \
             patch("paystack_payments.payment.lifecycle.on_payment_success"):
            _on_charge_success(
                {
                    "reference": "LOG-001",
                    "id": "TXN-001",
                    "amount": 500000,
                    "fees": 7500,
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

    def test_charge_failed_marks_log(self):
        gw = _make_gw("GW-001")
        log = MagicMock()
        log.gateway_setting = "GW-001"

        with patch("paystack_payments.gateway.webhook._get_log", return_value=log), \
             patch("paystack_payments.payment.lifecycle.on_payment_failed"):
            _on_charge_failed(
                {"reference": "LOG-001", "gateway_response": "Insufficient funds"},
                gw,
            )
            log.mark_failed.assert_called_once_with("Insufficient funds")

    def test_charge_failed_missing_reference_ignored(self):
        gw = _make_gw()
        _on_charge_failed({}, gw)  # must not raise


class TestRefundProcessed(FrappeTestCase):

    def test_refund_processed_updates_log(self):
        with patch("frappe.db.get_value") as mock_get_value, \
             patch("frappe.get_doc") as mock_get_doc, \
             patch("paystack_payments.payment.lifecycle.on_refund_processed"):
            mock_get_value.side_effect = [
                "LOG-001",        # Payment Log lookup by txn_id
                "REFUND-LOG-001", # Refund Log lookup
            ]
            refund_log = MagicMock()
            mock_get_doc.return_value = refund_log
            gw = _make_gw()

            _on_refund_processed(
                {
                    "transaction": {"id": "TXN-001"},
                    "id": "REFUND-001",
                    "amount": 100000,
                },
                gw,
            )
            self.assertEqual(refund_log.paystack_refund_id, "REFUND-001")
            self.assertEqual(refund_log.amount, 1000.0)


class TestSettlementSuccess(FrappeTestCase):

    def test_settlement_created_and_lifecycle_called(self):
        gw = _make_gw("GW-001")
        gw.currency = "NGN"

        with patch("frappe.db.exists", return_value=False), \
             patch("frappe.get_doc") as mock_get_doc, \
             patch("frappe.db.commit"), \
             patch("paystack_payments.payment.lifecycle.on_settlement_received") as mock_lifecycle:
            settlement = MagicMock()
            mock_get_doc.return_value = settlement
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
            mock_lifecycle.assert_called_once_with(settlement)

    def test_duplicate_settlement_ignored(self):
        gw = _make_gw()
        with patch("frappe.db.exists", return_value=True):
            _on_settlement_success({"id": "STL-DUP"}, gw)
            # No exception, no further processing


if __name__ == "__main__":
    unittest.main()
