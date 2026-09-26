// public/js/payment_request_ext.js
// Extends the Payment Request form (from the payments app) with a
// "Pay with Paystack" button when the selected gateway is Paystack.

frappe.ui.form.on("Payment Request", {
    refresh(frm) {
        if (
            frm.doc.docstatus === 1 &&
            frm.doc.status === "Initiated" &&
            frm.doc.payment_gateway &&
            frm.doc.payment_gateway.toLowerCase().includes("paystack")
        ) {
            frm.add_custom_button(__("Pay with Paystack"), function () {
                frappe.call({
                    method: "paystack_payments.api.get_payment_log",
                    args: { reference: frm.doc.name },
                    callback(r) {
                        if (!r.message) return;
                        const d = r.message;
                        if (d.checkout_mode === "Hosted" && d.hosted_url) {
                            window.open(d.hosted_url, "_blank");
                        } else {
                            paystack_payments.open_popup({
                                public_key: d.public_key,
                                email: frm.doc.email_to || frappe.session.user,
                                amount: d.amount,
                                currency: d.currency,
                                reference: d.name,
                                onSuccess() { frm.reload_doc(); }
                            });
                        }
                    }
                });
            }, __("Actions"));

            frm.add_custom_button(__("Copy Payment Link"), function () {
                const log_name = frappe.db.get_value(
                    "Paystack Payment Log",
                    { payment_request: frm.doc.name },
                    "name"
                ).then(r => {
                    if (r && r.message) {
                        paystack_payments.copy_link(r.message);
                    }
                });
            }, __("Actions"));
        }
    }
});
