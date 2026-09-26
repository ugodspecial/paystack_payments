// paystack_payment_log.js
frappe.ui.form.on("Paystack Payment Log", {

    refresh(frm) {
        frm.disable_save();

        const canAct = frappe.user.has_role(["System Manager", "Accounts Manager"]);

        // ── Complete Payment button ───────────────────────────────────────────
        if (
            canAct &&
            ["Processed", "Needs Attention"].includes(frm.doc.status) &&
            !frm.doc.payment_entry
        ) {
            frm.add_custom_button(__("Complete Payment"), () => {
                frappe.confirm(
                    __("Verify and book this payment against Paystack?"),
                    () => {
                        frappe.call({
                            method: "paystack_payments.api.complete_payment",
                            args: { log_name: frm.doc.name },
                            freeze: true,
                            freeze_message: __("Verifying with Paystack…"),
                            callback(r) {
                                if (r.message) {
                                    frappe.msgprint(r.message);
                                    frm.reload_doc();
                                }
                            }
                        });
                    }
                );
            }, __("Actions"));
        }

        // ── Refund button ────────────────────────────────────────────────────
        if (
            canAct &&
            ["Completed", "Partially Refunded"].includes(frm.doc.status)
        ) {
            frm.add_custom_button(__("Refund"), () => {
                const max = (frm.doc.amount_paid || 0) - (frm.doc.total_refunded || 0);
                const d = new frappe.ui.Dialog({
                    title: __("Refund Payment"),
                    fields: [
                        {
                            fieldname: "amount",
                            fieldtype: "Currency",
                            label: __("Refund Amount"),
                            reqd: 1,
                            default: max,
                            description: __("Maximum: {0} {1}", [max, frm.doc.currency])
                        },
                        {
                            fieldname: "reason",
                            fieldtype: "Data",
                            label: __("Reason (optional)")
                        }
                    ],
                    primary_action_label: __("Refund"),
                    primary_action(values) {
                        d.hide();
                        frappe.call({
                            method: "paystack_payments.api.refund_payment",
                            args: {
                                log_name: frm.doc.name,
                                amount: values.amount,
                                reason: values.reason || ""
                            },
                            freeze: true,
                            callback(r) {
                                if (r.message) {
                                    frappe.msgprint(r.message);
                                    frm.reload_doc();
                                }
                            }
                        });
                    }
                });
                d.show();
            }, __("Actions"));
        }

        // ── Verify Transaction ───────────────────────────────────────────────
        if (frm.doc.paystack_txn_id) {
            frm.add_custom_button(__("Verify Transaction"), () => {
                frappe.call({
                    method: "paystack_payments.api.verify_transaction",
                    args: { log_name: frm.doc.name },
                    callback(r) {
                        if (r.message) {
                            frappe.msgprint(
                                `<pre>${JSON.stringify(r.message, null, 2)}</pre>`,
                                __("Paystack Transaction")
                            );
                        }
                    }
                });
            }, __("Actions"));

            frm.add_custom_button(__("Open in Paystack"), () => {
                const base = frm.doc.gateway_setting
                    ? (frappe.db.get_value("Paystack Gateway Setting", frm.doc.gateway_setting, "test_mode")
                        ? "https://dashboard.paystack.com/#/transactions/"
                        : "https://dashboard.paystack.com/#/transactions/")
                    : "https://dashboard.paystack.com/#/transactions/";
                window.open(base + frm.doc.paystack_txn_id, "_blank");
            }, __("Actions"));
        }

        // ── Status badge ─────────────────────────────────────────────────────
        const colors = {
            "Pending": "yellow",
            "Processed": "blue",
            "Completed": "green",
            "Failed": "red",
            "Partially Refunded": "orange",
            "Refunded": "grey",
            "Needs Attention": "red"
        };
        frm.set_indicator_formatter(
            "status",
            () => colors[frm.doc.status] || "grey"
        );
    }
});
