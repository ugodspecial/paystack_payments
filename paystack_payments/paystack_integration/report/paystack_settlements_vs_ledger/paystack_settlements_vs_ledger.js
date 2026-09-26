frappe.query_reports["Paystack Settlements vs Ledger"] = {
    filters: [
        {
            fieldname: "from_date",
            label: __("From Date"),
            fieldtype: "Date",
            default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
        },
        {
            fieldname: "to_date",
            label: __("To Date"),
            fieldtype: "Date",
            default: frappe.datetime.get_today(),
        },
        {
            fieldname: "gateway_setting",
            label: __("Gateway"),
            fieldtype: "Link",
            options: "Paystack Gateway Setting",
        },
    ],
};
