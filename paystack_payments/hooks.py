app_name = "paystack_payments"
app_title = "Paystack Payments"
app_publisher = "YoungAndCode LTD"
app_description = (
    "Paystack payment gateway for Frappe. Depends on the `payments` app only — "
    "works with ERPNext, HRMS, or any other Frappe app that has `payments` installed."
)
app_email = "info@youngandcodeltd.com"
app_license = "MIT"
app_version = "1.0.0"
app_icon = "octicon octicon-credit-card"
app_color = "#00c3f7"

# ── Dependencies ──────────────────────────────────────────────────────────────
# payments is the only non-frappe requirement; ERPNext is NOT required.
required_apps = ["payments"]

# ── Assets ───────────────────────────────────────────────────────────────────
app_include_js = "/assets/paystack_payments/js/paystack_payments.js"
app_include_css = "/assets/paystack_payments/css/paystack_payments.css"

# ── DocType JS overrides ─────────────────────────────────────────────────────
# Inject Paystack buttons into standard Payment Request (from payments app)
doctype_js = {
    "Payment Request": "public/js/payment_request_ext.js",
}

# ── Web routes ────────────────────────────────────────────────────────────────
website_route_rules = [
    {
        "from_route": "/paystack-checkout/<reference>",
        "to_route": "paystack-checkout",
    },
    {
        "from_route": "/my-payments",
        "to_route": "my-payments",
    },
]

# ── Document events ───────────────────────────────────────────────────────────
doc_events = {
    # Hook into Payment Request (payments app) — book payment when settled
    "Payment Request": {
        "on_submit": "paystack_payments.events.payment_request.on_submit",
        "on_cancel": "paystack_payments.events.payment_request.on_cancel",
    },
}

# ── Scheduler ────────────────────────────────────────────────────────────────
scheduler_events = {
    # Re-drive captures whose Payment Request wasn't settled yet
    "cron": {
        "*/10 * * * *": [
            "paystack_payments.tasks.retry_pending_captures"
        ],
        "0 * * * *": [
            "paystack_payments.tasks.retry_pending_settlements"
        ],
        "0 2 * * *": [
            "paystack_payments.tasks.daily_reconciliation",
            "paystack_payments.tasks.collect_subscription_invoices",
        ],
    }
}

# ── Install / uninstall lifecycle ────────────────────────────────────────────
after_install = "paystack_payments.setup.install.after_install"
before_uninstall = "paystack_payments.setup.uninstall.before_uninstall"

# ── Fixtures ─────────────────────────────────────────────────────────────────
fixtures = [
    {
        "dt": "Payment Gateway",
        "filters": [["gateway", "=", "Paystack"]],
    },
    {
        "dt": "Mode of Payment",
        "filters": [["mode_of_payment", "=", "Paystack"]],
    },
    {
        "dt": "Role",
        "filters": [["role_name", "like", "Paystack%"]],
    },
]

# ── Jinja globals (available in print formats / web templates) ────────────────
jinja = {
    "methods": [
        "paystack_payments.utils.jinja.paystack_payment_link",
        "paystack_payments.utils.jinja.paystack_payment_qr",
    ]
}

# NOTE: Payment Gateway registration is handled at runtime in
# PaystackGatewaySetting.on_update() — it creates/updates the Payment Gateway
# and Payment Gateway Account doctypes directly.  There is no hooks.py key for
# this in the payments app; the doctype records are the authoritative registry.
