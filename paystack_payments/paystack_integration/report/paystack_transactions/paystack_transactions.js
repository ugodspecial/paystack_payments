frappe.query_reports["Paystack Transactions"] = {
    filters: [
        {
            fieldname: "from_date",
            label: __("From Date"),
            fieldtype: "Date",
            default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
            reqd: 1,
        },
        {
            fieldname: "to_date",
            label: __("To Date"),
            fieldtype: "Date",
            default: frappe.datetime.get_today(),
            reqd: 1,
        },
        {
            fieldname: "gateway_setting",
            label: __("Gateway"),
            fieldtype: "Link",
            options: "Paystack Gateway Setting",
        },
        {
            fieldname: "company",
            label: __("Company"),
            fieldtype: "Link",
            options: "Company",
        },
        {
            fieldname: "status",
            label: __("Status"),
            fieldtype: "Select",
            options: "\nPending\nProcessed\nCompleted\nFailed\nPartially Refunded\nRefunded\nNeeds Attention",
        },
    ],
};
