"""
integrations/erpnext/adapter.py
Guard utilities for the ERPNext adapter layer.

ALL functions in integrations/erpnext/ must call require_erpnext()
(or is_erpnext_installed()) before touching any ERPNext doctype.

This file has NO imports from erpnext at module level — imports are
deferred to the functions that use them, so this module can always be
imported safely, even when ERPNext is not installed.
"""

from __future__ import annotations

import frappe


def is_erpnext_installed() -> bool:
    """Return True if ERPNext is installed on this site."""
    return "erpnext" in frappe.get_installed_apps()


def require_erpnext() -> None:
    """
    Raise a clear error if ERPNext is not installed.
    Call this at the top of every ERPNext adapter function.
    """
    if not is_erpnext_installed():
        frappe.throw(
            frappe._(
                "This action requires ERPNext to be installed. "
                "Install ERPNext or use a payment flow that does not "
                "depend on ERPNext accounting documents."
            ),
            title=frappe._("ERPNext Required"),
        )


def get_gateway_company(gateway_setting_name: str) -> str:
    """
    Return the Company linked to a gateway setting.
    Raises if not set (required for ERPNext accounting).
    """
    company: str = frappe.db.get_value(
        "Paystack Gateway Setting", gateway_setting_name, "company"
    ) or ""
    if not company:
        frappe.throw(
            frappe._(
                "Gateway setting '{0}' has no Company set. "
                "A Company is required for ERPNext accounting."
            ).format(gateway_setting_name)
        )
    return company


def get_accounting_fields(gateway_setting_name: str) -> dict:
    """
    Return ERPNext-specific accounting fields from a gateway setting.
    All fields are validated as non-empty.
    """
    gw = frappe.get_doc("Paystack Gateway Setting", gateway_setting_name)
    fields = {
        "company": gw.company,
        "suspense_account": gw.suspense_account,
        "mode_of_payment": gw.mode_of_payment,
        "settlement_bank_account": gw.settlement_bank_account,
        "paystack_fee_account": gw.paystack_fee_account,
    }
    return fields
