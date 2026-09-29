"""
utils/paystack_client.py
Compatibility shim — canonical implementation is in gateway/client.py.
"""
from paystack_payments.gateway.client import (
    PaystackClient,
    PaystackError,
    get_client_for_gateway,
)
