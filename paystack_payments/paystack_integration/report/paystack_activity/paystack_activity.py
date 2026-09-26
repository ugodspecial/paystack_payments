"""
Paystack Activity report.
Shows daily charge counts and volumes for a date range and gateway.
"""

import frappe
from frappe import _


def execute(filters=None):
    filters = filters or {}
    columns = get_columns()
    data = get_data(filters)
    chart = get_chart(data)
    return columns, data, None, chart


def get_columns():
    return [
        {"label": _("Date"), "fieldname": "date", "fieldtype": "Date", "width": 110},
        {"label": _("Gateway"), "fieldname": "gateway_setting", "fieldtype": "Link",
         "options": "Paystack Gateway Setting", "width": 180},
        {"label": _("Total Charges"), "fieldname": "total_charges", "fieldtype": "Int", "width": 120},
        {"label": _("Successful"), "fieldname": "successful", "fieldtype": "Int", "width": 110},
        {"label": _("Failed"), "fieldname": "failed", "fieldtype": "Int", "width": 90},
        {"label": _("Volume"), "fieldname": "volume", "fieldtype": "Currency", "width": 140},
        {"label": _("Refunds"), "fieldname": "refunds", "fieldtype": "Currency", "width": 120},
        {"label": _("Net"), "fieldname": "net", "fieldtype": "Currency", "width": 140},
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

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    return frappe.db.sql(
        f"""
        SELECT
            DATE(pl.creation)                                   AS date,
            pl.gateway_setting,
            COUNT(*)                                            AS total_charges,
            SUM(pl.status IN ('Completed','Processed'))         AS successful,
            SUM(pl.status = 'Failed')                           AS failed,
            COALESCE(SUM(CASE WHEN pl.status IN ('Completed','Processed')
                              THEN pl.amount_paid END), 0)      AS volume,
            COALESCE(SUM(pl.total_refunded), 0)                 AS refunds,
            COALESCE(SUM(CASE WHEN pl.status IN ('Completed','Processed')
                              THEN pl.amount_paid END), 0)
            - COALESCE(SUM(pl.total_refunded), 0)               AS net
        FROM `tabPaystack Payment Log` pl
        {where}
        GROUP BY DATE(pl.creation), pl.gateway_setting
        ORDER BY DATE(pl.creation) DESC
        """,
        values,
        as_dict=True,
    )


def get_chart(data):
    if not data:
        return None

    dates = [str(row.date) for row in reversed(data)]
    volumes = [row.volume for row in reversed(data)]

    return {
        "data": {
            "labels": dates,
            "datasets": [{"name": _("Volume"), "values": volumes}],
        },
        "type": "bar",
        "colors": ["#00c3f7"],
    }
