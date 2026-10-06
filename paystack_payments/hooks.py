"""hooks.py — paystack_payments Frappe app hooks."""

app_name = "paystack_payments"
app_title = "Paystack Payments"
app_publisher = "YoungAndCode LTD"
app_description = (
    "Paystack payment gateway for Frappe. "
    "Requires only the payments app — ERPNext is optional."
)
app_email = "info@youngandcodeltd.com"
app_license = "MIT"
app_version = "1.0.0"
app_icon = "💳"
app_color = "#00c3f7"

# Frappe v16's Apps screen uses this hook to register an app card. Without it,
# the app can be installed and its workspaces can exist while the app is absent
# from the Apps screen/dock.
add_to_apps_screen = [
    {
        "name": app_name,
        "logo": "/assets/paystack_payments/images/paystack_payments.svg",
        "title": app_title,
        "route": "/app/paystack-dashboard",
    }
]

# ── Required apps ─────────────────────────────────────────────────────────────
# payments is the only non-frappe requirement.
# erpnext is deliberately NOT listed here.
required_apps = ["payments"]

# ── Assets ────────────────────────────────────────────────────────────────────
app_include_js = "/assets/paystack_payments/js/paystack_payments.js"
app_include_css = "/assets/paystack_payments/css/paystack_payments.css"

# ── DocType JS overrides ──────────────────────────────────────────────────────
# Extend the payments-app Payment Request form.
doctype_js = {
    "Payment Request": "public/js/payment_request_ext.js",
}

# ── Website routes ────────────────────────────────────────────────────────────
website_route_rules = [
    {"from_route": "/paystack-checkout/<reference>", "to_route": "paystack-checkout"},
    {"from_route": "/my-payments", "to_route": "my-payments"},
]

# ── Document events ───────────────────────────────────────────────────────────
# Only generic, ERPNext-independent events are registered here.
# ERPNext-specific events (Sales Invoice, Sales Order, etc.) are handled
# through the lifecycle._notify_reference() routing, not hooks.
doc_events = {
    "Payment Request": {
        "on_submit": "paystack_payments.events.payment_request.on_submit",
        "on_cancel": "paystack_payments.events.payment_request.on_cancel",
    },
}

# ── Scheduler ─────────────────────────────────────────────────────────────────
scheduler_events = {
    "cron": {
        # Retry Processed logs with no Payment Entry every 10 minutes.
        "*/10 * * * *": ["paystack_payments.tasks.retry_pending_captures"],
        # Retry failed settlements hourly.
        "0 * * * *": ["paystack_payments.tasks.retry_pending_settlements"],
        # Daily: reconciliation + ERPNext subscription auto-charge.
        "0 2 * * *": [
            "paystack_payments.tasks.daily_reconciliation",
            "paystack_payments.tasks.collect_subscription_invoices",
        ],
    }
}

# ── Install / uninstall ───────────────────────────────────────────────────────
after_install = "paystack_payments.setup.install.after_install"
after_migrate = "paystack_payments.setup.install.after_install"
before_uninstall = "paystack_payments.setup.uninstall.before_uninstall"

# ── Fixtures ──────────────────────────────────────────────────────────────────
fixtures = [
    {"dt": "Payment Gateway", "filters": [["gateway", "=", "Paystack"]]},
]

# ── Jinja globals ─────────────────────────────────────────────────────────────
jinja = {
    "methods": [
        "paystack_payments.utils.jinja.paystack_payment_link",
        "paystack_payments.utils.jinja.paystack_payment_qr",
    ]
}

# NOTE: Payment Gateway registration is handled at runtime in
# PaystackGatewaySetting.on_update() by directly creating/updating the
# Payment Gateway and Payment Gateway Account doctypes.
# There is no framework-level hook for this in the payments app.
