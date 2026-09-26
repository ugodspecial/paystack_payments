frappe.query_reports["Paystack Unsettled Payments"] = {
    filters: [
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
    ],
};
