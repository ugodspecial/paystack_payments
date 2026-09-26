"""
Paystack Settlements vs Ledger — compares Paystack Settlement records
against the Journal Entries they should have produced.
Highlights any settlement that failed to post or whose JE amount differs.
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
        {"label": _("Settlement ID"), "fieldname": "paystack_settlement_id",
         "fieldtype": "Data", "width": 160},
        {"label": _("Date"), "fieldname": "settlement_date", "fieldtype": "Date", "width": 110},
        {"label": _("Gateway"), "fieldname": "gateway_setting", "fieldtype": "Link",
         "options": "Paystack Gateway Setting", "width": 160},
        {"label": _("Gross"), "fieldname": "gross", "fieldtype": "Currency", "width": 130},
        {"label": _("Fees"), "fieldname": "fees", "fieldtype": "Currency", "width": 110},
        {"label": _("Net"), "fieldname": "net", "fieldtype": "Currency", "width": 130},
        {"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 100},
        {"label": _("Journal Entry"), "fieldname": "journal_entry", "fieldtype": "Link",
         "options": "Journal Entry", "width": 160},
        {"label": _("JE Amount"), "fieldname": "je_amount", "fieldtype": "Currency", "width": 130},
        {"label": _("Variance"), "fieldname": "variance", "fieldtype": "Currency", "width": 120},
        {"label": _("Errors"), "fieldname": "errors", "fieldtype": "Data", "width": 200},
    ]


def get_data(filters):
    conditions = []
    values = {}

    if filters.get("gateway_setting"):
        conditions.append("s.gateway_setting = %(gateway_setting)s")
        values["gateway_setting"] = filters["gateway_setting"]
    if filters.get("from_date"):
        conditions.append("s.settlement_date >= %(from_date)s")
        values["from_date"] = filters["from_date"]
    if filters.get("to_date"):
        conditions.append("s.settlement_date <= %(to_date)s")
        values["to_date"] = filters["to_date"]

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    rows = frappe.db.sql(
        f"""
        SELECT
            s.paystack_settlement_id, s.settlement_date, s.gateway_setting,
            s.gross, s.fees, s.net, s.status, s.journal_entry, s.errors
        FROM `tabPaystack Settlement` s
        {where}
        ORDER BY s.settlement_date DESC
        """,
        values,
        as_dict=True,
    )

    for row in rows:
        row.je_amount = 0
        row.variance = 0

        if row.journal_entry:
            je_total = frappe.db.sql(
                """
                SELECT SUM(credit_in_account_currency)
                FROM `tabJournal Entry Account`
                WHERE parent = %s AND docstatus = 1
                """,
                row.journal_entry,
            )
            row.je_amount = (je_total[0][0] or 0) if je_total else 0
            row.variance = (row.gross or 0) - row.je_amount

    return rows


def get_summary(data):
    if not data:
        return []

    mismatches = [r for r in data if abs(r.variance or 0) > 0.01]
    unbooked = [r for r in data if not r.journal_entry and r.status != "Pending"]

    return [
        {"label": _("Total Settlements"), "value": len(data), "datatype": "Int"},
        {"label": _("Unbooked"), "value": len(unbooked), "datatype": "Int",
         "color": "red" if unbooked else "green"},
        {"label": _("Variances"), "value": len(mismatches), "datatype": "Int",
         "color": "red" if mismatches else "green"},
    ]
