import frappe


def before_uninstall():
    """
    Deregister all Payment Gateways created by this app.
    Leaves the Mode of Payment in place — the user may want to keep their data.
    """
    gateways = frappe.get_all(
        "Paystack Gateway Setting",
        filters={"enabled": 1},
        pluck="name",
    )
    for name in gateways:
        gw = frappe.get_doc("Paystack Gateway Setting", name)
        gw._deregister_payment_gateway()

    frappe.db.commit()
