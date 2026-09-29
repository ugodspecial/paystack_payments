"""
setup/install.py
Called by the after_install hook in hooks.py.

On all sites: registers the Payment Gateway in the payments app.
On ERPNext sites: also creates the Paystack Mode of Payment.
"""

from __future__ import annotations

import frappe


def after_install() -> None:
    _create_mode_of_payment_if_erpnext()
    frappe.db.commit()
    frappe.clear_cache()


def _create_mode_of_payment_if_erpnext() -> None:
    """
    Mode of Payment is an ERPNext doctype — only create it when ERPNext
    is installed. On pure Frappe + payments sites, skip silently.
    """
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
    frappe.logger("paystack").info("paystack_payments: created Mode of Payment 'Paystack'")
