// paystack_gateway_setting.js
frappe.ui.form.on("Paystack Gateway Setting", {

    refresh(frm) {
        if (!frm.is_new()) {
            frm.add_custom_button(__("View Payment Logs"), () => {
                frappe.set_route("List", "Paystack Payment Log", {
                    gateway_setting: frm.doc.name
                });
            });

            frm.add_custom_button(__("Paystack Dashboard"), () => {
                frappe.set_route("Workspaces", "Paystack Dashboard");
            }, __("Links"));

            frm.add_custom_button(__("Run Reconciliation"), () => {
                frappe.call({
                    method: "paystack_payments.api.run_reconciliation",
                    args: { gateway_setting: frm.doc.name },
                    callback(r) {
                        if (r.message) {
                            frappe.msgprint(r.message);
                        }
                    }
                });
            }, __("Actions"));
        }

        if (frm.doc.test_mode) {
            frm.dashboard.add_comment(
                __("Test mode is ON. Use Paystack test keys only."),
                "yellow",
                true
            );
        }
    },

    enabled(frm) {
        if (frm.doc.enabled && !frm.doc.suspense_account) {
            frappe.msgprint(__("Please set a Suspense Account before enabling the gateway."));
        }
    },

});
