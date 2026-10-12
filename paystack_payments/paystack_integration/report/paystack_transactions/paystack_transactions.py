"""
Paystack Transactions — row-level transaction report with filters.
"""

import frappe
from frappe import _


def execute(filters=None):
    filters = filters or {}
    data = get_data(filters)
    return get_columns(), data, None, get_chart(data)


def get_chart(data):
    if not data:
        return None
    statuses = {}
    for row in data:
        statuses[row.status] = statuses.get(row.status, 0) + 1
    return {
        "data": {
            "labels": list(statuses),
            "datasets": [{"name": _("Transactions"), "values": list(statuses.values())}],
        },
        "type": "donut",
        "colors": ["#00c3f7", "#28a745", "#dc3545", "#ffc107"],
    }


def get_columns():
    return [
        {"label": _("Log"), "fieldname": "name", "fieldtype": "Link",
         "options": "Paystack Payment Log", "width": 160},
        {"label": _("Date"), "fieldname": "payment_date", "fieldtype": "Datetime", "width": 150},
        {"label": _("Gateway"), "fieldname": "gateway_setting", "fieldtype": "Link",
         "options": "Paystack Gateway Setting", "width": 160},
        {"label": _("Payer"), "fieldname": "payer_name", "fieldtype": "Data", "width": 150},
        {"label": _("Reference"), "fieldname": "reference_name", "fieldtype": "Data", "width": 160},
        {"label": _("Amount"), "fieldname": "amount", "fieldtype": "Currency", "width": 120},
        {"label": _("Amount Paid"), "fieldname": "amount_paid", "fieldtype": "Currency", "width": 120},
        {"label": _("Fee"), "fieldname": "paystack_fee", "fieldtype": "Currency", "width": 110},
        {"label": _("Refunded"), "fieldname": "total_refunded", "fieldtype": "Currency", "width": 110},
        {"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 130},
        {"label": _("Payment Entry"), "fieldname": "payment_entry", "fieldtype": "Link",
         "options": "Payment Entry", "width": 160},
        {"label": _("Txn ID"), "fieldname": "paystack_txn_id", "fieldtype": "Data", "width": 160},
    ]


def get_data(filters):
    conditions = []
    values = {}

    if filters.get("from_date"):
        conditions.append("DATE(pl.creation) >= %(from_date)s")
        values["from_date"] = filters["from_date"]
    if filters.get("to_date"):
        conditions.append("DATE(pl.creation) <= %(to_date)s")
        values["to_date"] = filters["to_date"]
    if filters.get("gateway_setting"):
        conditions.append("pl.gateway_setting = %(gateway_setting)s")
        values["gateway_setting"] = filters["gateway_setting"]
    if filters.get("status"):
        conditions.append("pl.status = %(status)s")
        values["status"] = filters["status"]
    if filters.get("company"):
        conditions.append("pl.company = %(company)s")
        values["company"] = filters["company"]

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    return frappe.db.sql(
        f"""
        SELECT
            pl.name, pl.payment_date, pl.gateway_setting,
            pl.payer_name, pl.reference_name,
            pl.amount, pl.amount_paid, pl.paystack_fee,
            pl.total_refunded, pl.status,
            pl.payment_entry, pl.paystack_txn_id
        FROM `tabPaystack Payment Log` pl
        {where}
        ORDER BY pl.creation DESC
        LIMIT 2000
        """,
        values,
        as_dict=True,
    )
