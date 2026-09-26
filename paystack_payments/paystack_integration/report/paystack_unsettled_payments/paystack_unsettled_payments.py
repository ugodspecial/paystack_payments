"""
Paystack Unsettled Payments — Processed logs with no Payment Entry.
These are the funds Paystack has captured but Frappe hasn't booked yet.
"""

import frappe
from frappe import _


def execute(filters=None):
    filters = filters or {}
    data = get_data(filters)
    columns = get_columns()
    summary = get_summary(data)
    return columns, data, None, None, summary


def get_columns():
    return [
        {"label": _("Log"), "fieldname": "name", "fieldtype": "Link",
         "options": "Paystack Payment Log", "width": 160},
        {"label": _("Payment Date"), "fieldname": "payment_date", "fieldtype": "Datetime", "width": 150},
        {"label": _("Gateway"), "fieldname": "gateway_setting", "fieldtype": "Link",
         "options": "Paystack Gateway Setting", "width": 160},
        {"label": _("Amount Paid"), "fieldname": "amount_paid", "fieldtype": "Currency", "width": 130},
        {"label": _("Currency"), "fieldname": "currency", "fieldtype": "Link",
         "options": "Currency", "width": 90},
        {"label": _("Reference"), "fieldname": "reference_name", "fieldtype": "Data", "width": 150},
        {"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 120},
        {"label": _("Retry Count"), "fieldname": "retry_count", "fieldtype": "Int", "width": 110},
        {"label": _("Next Retry"), "fieldname": "next_retry_at", "fieldtype": "Datetime", "width": 150},
        {"label": _("Txn ID"), "fieldname": "paystack_txn_id", "fieldtype": "Data", "width": 160},
    ]


def get_data(filters):
    conditions = ["pl.payment_entry IS NULL OR pl.payment_entry = ''",
                  "pl.status IN ('Processed','Needs Attention')"]
    values = {}

    if filters.get("gateway_setting"):
        conditions.append("pl.gateway_setting = %(gateway_setting)s")
        values["gateway_setting"] = filters["gateway_setting"]
    if filters.get("company"):
        conditions.append("pl.company = %(company)s")
        values["company"] = filters["company"]

    return frappe.db.sql(
        "SELECT pl.name, pl.payment_date, pl.gateway_setting, pl.amount_paid, "
        "pl.currency, pl.reference_name, pl.status, pl.retry_count, "
        "pl.next_retry_at, pl.paystack_txn_id "
        "FROM `tabPaystack Payment Log` pl "
        "WHERE " + " AND ".join(conditions) +
        " ORDER BY pl.payment_date ASC",
        values,
        as_dict=True,
    )


def get_summary(data):
    if not data:
        return []
    total = sum(r.amount_paid or 0 for r in data)
    return [
        {"label": _("Unsettled Count"), "value": len(data), "datatype": "Int", "color": "orange"},
        {"label": _("Total Unsettled"), "value": total, "datatype": "Currency", "color": "orange"},
    ]
