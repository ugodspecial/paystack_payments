"""
setup/install.py
Called by after_install hook in hooks.py.
Creates the Paystack Mode of Payment if it does not exist.
No ERPNext dependency — Mode of Payment is a core Frappe/payments doctype.
"""

import frappe


def after_install():
    _create_mode_of_payment()
    frappe.db.commit()
    frappe.clear_cache()


def _create_mode_of_payment():
    if frappe.db.exists("Mode of Payment", "Paystack"):
        return

    mop = frappe.get_doc(
        {
            "doctype": "Mode of Payment",
            "mode_of_payment": "Paystack",
            "type": "General",
            "enabled": 1,
        }
    )
    mop.insert(ignore_permissions=True)
    frappe.logger().info("paystack_payments: created Mode of Payment 'Paystack'")
