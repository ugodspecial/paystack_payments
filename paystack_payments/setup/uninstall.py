"""
setup/uninstall.py
Called by before_uninstall hook. Deregisters all Paystack Payment Gateways.
"""

from __future__ import annotations

import frappe


def before_uninstall() -> None:
    _deregister_all_gateways()
    frappe.db.commit()


def _deregister_all_gateways() -> None:
    gateways = frappe.get_all(
        "Paystack Gateway Setting",
        pluck="name",
        ignore_permissions=True,
    )
    for name in gateways:
        try:
            gw = frappe.get_doc("Paystack Gateway Setting", name)
            gw._deregister_payment_gateway()
        except Exception:  # noqa: BLE001
            frappe.log_error(
                title=f"Paystack uninstall: failed to deregister gateway {name}",
                message=frappe.get_traceback(),
            )
