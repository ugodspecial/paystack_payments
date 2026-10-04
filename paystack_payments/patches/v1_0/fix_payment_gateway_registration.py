"""
patches/v1_0/fix_payment_gateway_registration.py

Fixes Payment Gateway records that may have been created with wrong names
or missing fields in earlier versions of paystack_payments.

Problems fixed:
  1. Payment Gateway name used "Paystack - <currency>" instead of
     "Paystack - <gateway_setting_name>"
  2. gateway_settings field was blank
  3. gateway_controller field was blank or wrong
  4. Missing Payment Gateway Account records
"""

import frappe


def execute():
    """Re-register all enabled Paystack Gateway Settings."""
    from paystack_payments.setup.install import _reregister_all_enabled_gateways
    _reregister_all_enabled_gateways()
    frappe.db.commit()
