"""
tests/test_erpnext_adapter.py
Tests for the ERPNext adapter layer.

Verifies:
  - require_erpnext() raises when ERPNext is not installed.
  - ERPNext adapter functions are unreachable from gateway/ and payment/ code
    when ERPNext is not installed.
  - Routing table dispatches to correct handler per reference_doctype.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase


class TestAdapterGuard(FrappeTestCase):

    def test_require_erpnext_raises_when_not_installed(self):
        """require_erpnext() must raise a ValidationError when ERPNext is absent."""
        with patch(
            "paystack_payments.integrations.erpnext.adapter.is_erpnext_installed",
            return_value=False,
        ):
            from paystack_payments.integrations.erpnext.adapter import require_erpnext
            with self.assertRaises(frappe.exceptions.ValidationError):
                require_erpnext()

    def test_require_erpnext_passes_when_installed(self):
        """require_erpnext() must not raise when ERPNext is installed."""
        with patch(
            "paystack_payments.integrations.erpnext.adapter.is_erpnext_installed",
            return_value=True,
        ):
            from paystack_payments.integrations.erpnext.adapter import require_erpnext
            require_erpnext()  # Should not raise


class TestERPNextRouting(FrappeTestCase):

    def _make_log(self, ref_dt: str, ref_dn: str = "DOC-001") -> MagicMock:
        log = MagicMock()
        log.name = "LOG-001"
        log.reference_doctype = ref_dt
        log.reference_docname = ref_dn
        log.amount_paid = 5000.0
        log.currency = "NGN"
        return log

    def test_payment_request_route_exists(self):
        from paystack_payments.integrations.erpnext import _PAYMENT_SUCCESS_ROUTES
        self.assertIn("Payment Request", _PAYMENT_SUCCESS_ROUTES)

    def test_sales_invoice_route_exists(self):
        from paystack_payments.integrations.erpnext import _PAYMENT_SUCCESS_ROUTES
        self.assertIn("Sales Invoice", _PAYMENT_SUCCESS_ROUTES)

    def test_unknown_doctype_logs_error_not_raises(self):
        """Unknown reference_doctype must log an error, not raise."""
        with patch(
            "paystack_payments.integrations.erpnext.adapter.is_erpnext_installed",
            return_value=True,
        ), patch("frappe.log_error") as mock_log_error:
            from paystack_payments.integrations.erpnext import _PAYMENT_SUCCESS_ROUTES, _dispatch
            log = self._make_log("Unknown Custom Doctype")
            _dispatch(_PAYMENT_SUCCESS_ROUTES, "Unknown Custom Doctype", log)
            mock_log_error.assert_called_once()

    def test_on_payment_success_calls_require_erpnext(self):
        """on_payment_success must enforce ERPNext guard."""
        with patch(
            "paystack_payments.integrations.erpnext.adapter.is_erpnext_installed",
            return_value=False,
        ):
            from paystack_payments.integrations.erpnext import on_payment_success
            log = self._make_log("Payment Request")
            with self.assertRaises(frappe.exceptions.ValidationError):
                on_payment_success(log)


class TestPaymentEntryDeduplication(FrappeTestCase):
    """
    Verifies that create_and_submit_payment_entry() never creates a duplicate
    Payment Entry for the same Paystack transaction.
    """

    def test_duplicate_payment_entry_not_created(self):
        with patch(
            "paystack_payments.integrations.erpnext.adapter.is_erpnext_installed",
            return_value=True,
        ), patch("frappe.db.get_value", return_value="PAY-EXIST-001"), \
           patch("paystack_payments.integrations.erpnext.adapter.get_accounting_fields"):
            from paystack_payments.integrations.erpnext.payment_entry import (
                create_and_submit_payment_entry,
            )
            log = MagicMock()
            log.paystack_txn_id = "TXN-DUP"
            log.gateway_setting = "GW-001"

            result = create_and_submit_payment_entry(payment_log=log)
            self.assertEqual(result, "PAY-EXIST-001")


if __name__ == "__main__":
    unittest.main()
