"""Offline Stripe client with awaitable SDK methods and synchronous responses."""
from unittest.mock import AsyncMock, Mock


def stripe_client():
    client = Mock()
    for service in (client.v1.checkout.sessions, client.v1.payment_intents):
        for method in ('create', 'retrieve', 'confirm', 'cancel', 'expire'):
            setattr(service, method + '_async', AsyncMock(return_value=Mock()))
    return client
