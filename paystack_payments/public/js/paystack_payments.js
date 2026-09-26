// paystack_payments.js
// Loaded on every page via hooks.py > app_include_js

frappe.provide("paystack_payments");

/**
 * Copy a payment link to clipboard and show a toast.
 * Callable from anywhere: paystack_payments.copy_link("LOG-xxxx")
 */
paystack_payments.copy_link = function (log_name) {
    const url = window.location.origin + "/paystack-checkout/" + log_name;
    if (navigator.clipboard) {
        navigator.clipboard.writeText(url).then(() => {
            frappe.show_alert({ message: __("Payment link copied to clipboard"), indicator: "green" });
        });
    } else {
        const el = document.createElement("textarea");
        el.value = url;
        document.body.appendChild(el);
        el.select();
        document.execCommand("copy");
        document.body.removeChild(el);
        frappe.show_alert({ message: __("Payment link copied"), indicator: "green" });
    }
};

/**
 * Open the Paystack inline popup for an existing Payment Log.
 * Useful for "Pay Now" buttons in portal or custom forms.
 */
paystack_payments.open_popup = function ({ public_key, email, amount, currency, reference, onSuccess }) {
    if (!window.PaystackPop) {
        frappe.require("https://js.paystack.co/v1/inline.js", function () {
            paystack_payments._launch_popup({ public_key, email, amount, currency, reference, onSuccess });
        });
    } else {
        paystack_payments._launch_popup({ public_key, email, amount, currency, reference, onSuccess });
    }
};

paystack_payments._launch_popup = function ({ public_key, email, amount, currency, reference, onSuccess }) {
    const handler = PaystackPop.setup({
        key: public_key,
        email: email,
        amount: Math.round(amount * 100),
        currency: currency,
        ref: reference,
        callback: function (response) {
            frappe.show_alert({ message: __("Payment successful!"), indicator: "green" });
            if (typeof onSuccess === "function") onSuccess(response);
        },
        onClose: function () {
            frappe.show_alert({ message: __("Payment window closed"), indicator: "yellow" });
        }
    });
    handler.openIframe();
};
