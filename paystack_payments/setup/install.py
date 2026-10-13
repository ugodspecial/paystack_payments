"""
setup/install.py
Called by the after_install hook in hooks.py.

On all sites:
  - Re-registers all enabled Paystack Gateway Settings so the Payment Gateway
    and Payment Gateway Account records are correct after install/migrate.

On ERPNext sites:
  - Also creates the Paystack Mode of Payment if it does not exist.
"""

from __future__ import annotations

import json

import frappe


def after_install() -> None:
    _create_mode_of_payment_if_erpnext()
    _ensure_dashboard_charts()
    _reregister_all_enabled_gateways()


def _ensure_dashboard_charts() -> None:
    """Create native Frappe charts used by the Paystack workspace."""
    charts = [
        {
            "name": "Paystack Activity Chart",
            "chart_type": "Sum",
            "document_type": "Paystack Payment Log",
            "based_on": "payment_date",
            "value_based_on": "amount_paid",
            "timespan": "Last Month",
            "time_interval": "Daily",
            "timeseries": 1,
            "type": "Line",
            "filters_json": json.dumps(
                [[
                    "Paystack Payment Log",
                    "status",
                    "in",
                    ["Processed", "Completed", "Partially Refunded", "Refunded"],
                ]]
            ),
        },
        {
            "name": "Paystack Transactions Chart",
            "chart_type": "Group By",
            "document_type": "Paystack Payment Log",
            "group_by_based_on": "status",
            "group_by_type": "Count",
            "type": "Donut",
            "filters_json": "[]",
        },
    ]
    for chart in charts:
        values = {k: v for k, v in chart.items() if k != "name"}
        # Dashboard Chart validation rejects edits to standard charts on
        # installed sites. These are app-managed records, but deliberately
        # site-owned so they can be created/updated on v15 and v16.
        values.update({"is_public": 1, "is_standard": 0, "module": "Paystack Integration"})
        if frappe.db.exists("Dashboard Chart", chart["name"]):
            frappe.db.set_value("Dashboard Chart", chart["name"], values)
        else:
            doc = frappe.get_doc(
                {"doctype": "Dashboard Chart", "chart_name": chart["name"], **values}
            )
            doc.insert(ignore_permissions=True)
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


def _reregister_all_enabled_gateways() -> None:
    """
    Re-register all enabled Paystack Gateway Settings after install/migrate.

    This ensures the Payment Gateway and Payment Gateway Account records in
    the payments app are correct and present, even if the app was updated or
    the records were accidentally deleted.
    """
    settings = frappe.get_all(
        "Paystack Gateway Setting",
        filters={"enabled": 1},
        pluck="name",
        ignore_permissions=True,
    )
    for name in settings:
        try:
            gw = frappe.get_doc("Paystack Gateway Setting", name)
            gw._register_payment_gateway()
            frappe.logger("paystack").info(
                "paystack_payments: re-registered gateway '%s'", name
            )
        except Exception:  # noqa: BLE001
            frappe.log_error(
                title=f"paystack_payments: failed to re-register gateway '{name}'",
                message=frappe.get_traceback(),
            )
