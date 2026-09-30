"""
setup/install.py
Installation and ERPNext-dependent setup helpers.
"""

from __future__ import annotations

import frappe


def after_install() -> None:
    """
    Called when paystack_payments is installed.

    If ERPNext is already installed, create the ERPNext-specific
    Paystack configuration. Otherwise, safely do nothing.
    """
    ensure_erpnext_setup()

    frappe.db.commit()
    frappe.clear_cache()


def ensure_erpnext_setup() -> None:
    """
    Ensure ERPNext-specific Paystack configuration exists.

    Safe to call repeatedly.

    Does nothing when ERPNext is not installed.
    """
    if "erpnext" not in frappe.get_installed_apps():
        return

    _create_mode_of_payment()


def _create_mode_of_payment() -> None:
    """Create the Paystack Mode of Payment if it doesn't already exist."""

    if not frappe.db.table_exists("tabMode of Payment"):
        return

    if frappe.db.exists("Mode of Payment", "Paystack"):
        return

    frappe.get_doc(
        {
            "doctype": "Mode of Payment",
            "mode_of_payment": "Paystack",
            "type": "General",
            "enabled": 1,
        }
    ).insert(ignore_permissions=True)

    frappe.logger("paystack").info(
        "paystack_payments: created Mode of Payment 'Paystack'"
    )
