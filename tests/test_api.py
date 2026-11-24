import pytest

from custom_components.bitvavo_bot.api import BitvavoPublicClient, parse_candles, select_markets
from custom_components.bitvavo_bot.const import CANDLE_LIMIT, CANDLE_MS


def test_parse_candles_strings_and_garbage():
    rows = parse_candles([[3600000, "1.5", "2", "1", "1.8", "10"], ["x"], None])
    assert rows == [[3600000, 1.5, 2.0, 1.0, 1.8, 10.0]]
    assert parse_candles(
        [{"timestamp": 1, "open": "1", "high": "2", "low": "0.5", "close": "1.5", "volume": "3"}]
    ) == [[1, 1.0, 2.0, 0.5, 1.5, 3.0]]


def test_select_markets_ranks_and_filters():
    markets = [
        {"market": "BTC-EUR", "base": "BTC", "quote": "EUR", "status": "trading"},
        {"market": "ETH-EUR", "base": "ETH", "quote": "EUR", "status": "trading"},
        {"market": "USDC-EUR", "base": "USDC", "quote": "EUR", "status": "trading"},
        {"market": "OLD-EUR", "base": "OLD", "quote": "EUR", "status": "halted"},
        {"market": "BTC-USDC", "base": "BTC", "quote": "USDC", "status": "trading"},
    ]
    tickers = [
        {"market": "ETH-EUR", "volumeQuote": "500"},
        {"market": "BTC-EUR", "volumeQuote": "900"},
        {"market": "USDC-EUR", "volumeQuote": "99999"},
        {"market": "OLD-EUR", "volumeQuote": "99999"},
        {"market": "BTC-USDC", "volumeQuote": "99999"},
        {"market": "ETH-EUR2", "volumeQuote": None},
    ]
    assert select_markets(markets, tickers, 10) == ["BTC-EUR", "ETH-EUR"]
    assert select_markets(markets, tickers, 1) == ["BTC-EUR"]


class FakeClient(BitvavoPublicClient):
    def __init__(self, have):
        self.have = have  # set van candle-starttijden die bestaan
        self.calls = 0

    async def _get(self, path, params=None):
        self.calls += 1
        ts = sorted((t for t in self.have if params["start"] <= t <= params["end"]), reverse=True)
        return [[t, "1", "2", "0.5", "1.5", "3"] for t in ts[: params["limit"]]]


@pytest.mark.asyncio
async def test_candles_paginates_dedupes_and_drops_forming_candle(monkeypatch):
    async def _nosleep(_):
        return None

    monkeypatch.setattr("custom_components.bitvavo_bot.api.asyncio.sleep", _nosleep)
    n = CANDLE_LIMIT * 2 + 100
    have = {i * CANDLE_MS for i in range(n + 1)}  # laatste is de nog lopende candle
    client = FakeClient(have)
    until = n * CANDLE_MS + 1234
    rows = await client.candles("BTC-EUR", 0, until)
    assert [r[0] for r in rows] == [i * CANDLE_MS for i in range(n)]
    assert client.calls == 3


@pytest.mark.asyncio
async def test_candles_with_gaps_and_empty(monkeypatch):
    client = FakeClient({0, 5 * CANDLE_MS, 6 * CANDLE_MS})
    rows = await client.candles("X-EUR", 0, 10 * CANDLE_MS)
    assert [r[0] for r in rows] == [0, 5 * CANDLE_MS, 6 * CANDLE_MS]
    assert await FakeClient(set()).candles("X-EUR", 0, 10 * CANDLE_MS) == []
