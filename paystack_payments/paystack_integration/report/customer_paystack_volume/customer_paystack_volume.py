"""
Customer Paystack Volume — shows total payments collected per customer.
Groups by payer_email and shows Completed/Processed logs only.
"""

import frappe
from frappe import _


def execute(filters=None):
    filters = filters or {}
    return get_columns(), get_data(filters)


def get_columns():
    return [
        {"label": _("Email"), "fieldname": "payer_email", "fieldtype": "Data", "width": 200},
        {"label": _("Payer Name"), "fieldname": "payer_name", "fieldtype": "Data", "width": 160},
        {"label": _("Transactions"), "fieldname": "txn_count", "fieldtype": "Int", "width": 120},
        {"label": _("Total Paid"), "fieldname": "total_paid", "fieldtype": "Currency", "width": 140},
        {"label": _("Total Refunded"), "fieldname": "total_refunded", "fieldtype": "Currency", "width": 140},
        {"label": _("Net"), "fieldname": "net", "fieldtype": "Currency", "width": 140},
        {"label": _("Last Payment"), "fieldname": "last_payment", "fieldtype": "Date", "width": 120},
    ]


def get_data(filters):
    conditions = ["pl.status IN ('Completed','Processed')", "pl.payer_email IS NOT NULL",
                  "pl.payer_email != ''"]
    values = {}

    if filters.get("gateway_setting"):
        conditions.append("pl.gateway_setting = %(gateway_setting)s")
        values["gateway_setting"] = filters["gateway_setting"]
    if filters.get("from_date"):
        conditions.append("DATE(pl.creation) >= %(from_date)s")
        values["from_date"] = filters["from_date"]
    if filters.get("to_date"):
        conditions.append("DATE(pl.creation) <= %(to_date)s")
        values["to_date"] = filters["to_date"]

    where = "WHERE " + " AND ".join(conditions)

    return frappe.db.sql(
        f"""
        SELECT
            pl.payer_email,
            MAX(pl.payer_name)                          AS payer_name,
            COUNT(*)                                    AS txn_count,
            COALESCE(SUM(pl.amount_paid), 0)            AS total_paid,
            COALESCE(SUM(pl.total_refunded), 0)         AS total_refunded,
            COALESCE(SUM(pl.amount_paid), 0)
                - COALESCE(SUM(pl.total_refunded), 0)   AS net,
            DATE(MAX(pl.payment_date))                  AS last_payment
        FROM `tabPaystack Payment Log` pl
        {where}
        GROUP BY pl.payer_email
        ORDER BY total_paid DESC
        LIMIT 1000
        """,
        values,
        as_dict=True,
    )
