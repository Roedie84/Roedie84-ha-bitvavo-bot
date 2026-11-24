import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def no_real_stream():
    """Geen echte verbinding met Bitvavo vanuit de tests."""
    from unittest.mock import patch

    async def idle(self):
        return None

    async def no_prices(self):
        return {}

    with (
        patch("custom_components.bitvavo_bot.stream.BitvavoStream.run", idle),
        patch("custom_components.bitvavo_bot.api.BitvavoPublicClient.ticker_prices", no_prices),
    ):
        yield
