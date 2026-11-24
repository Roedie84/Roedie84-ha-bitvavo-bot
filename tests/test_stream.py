import asyncio
import json

import aiohttp
import pytest

from custom_components.bitvavo_bot.brokers import PaperBroker
from custom_components.bitvavo_bot.stream import BitvavoStream

REAL_RUN = BitvavoStream.run  # de tests vervangen run() standaard door een lege functie


def make():
    prices = {}
    return BitvavoStream(None, lambda: ["BTC-EUR", "ETH-EUR"], prices), prices


def test_ticker_and_candle_events_update_prices_and_forming_candle():
    s, prices = make()
    s.handle({"event": "subscribed", "subscriptions": {"ticker": ["BTC-EUR"]}})
    assert prices == {} and s.version == 0
    s.handle({"event": "ticker", "market": "BTC-EUR", "bestBid": "80000", "bestAsk": "80010"})
    assert prices == {}  # geen transactie, dus geen nieuwe koers
    s.handle({"event": "ticker", "market": "BTC-EUR", "lastPrice": "80005.5"})
    assert prices == {"BTC-EUR": 80005.5} and s.version == 1
    s.handle({"event": "ticker", "market": "BTC-EUR", "lastPrice": "80005.5"})
    assert s.version == 1  # ongewijzigd telt niet
    s.handle({"event": "candles", "market": "ETH-EUR", "interval": "1h", "candle": [["3600000", "2600", "2610", "2590", "2605", "12.5"]]})
    assert s.forming["ETH-EUR"] == [3600000, 2600.0, 2610.0, 2590.0, 2605.0, 12.5]
    assert prices["ETH-EUR"] == 2605.0
    s.handle({"event": "candle", "market": "ETH-EUR", "interval": "1m", "candle": [["1", "1", "1", "1", "1", "1"]]})
    assert s.forming["ETH-EUR"][4] == 2605.0  # andere interval genegeerd
    for junk in ({"event": "ticker", "market": "BTC-EUR", "lastPrice": "x"}, {"event": "candles", "market": "A", "interval": "1h", "candle": [["1"]]}, {}, {"errorCode": 1, "error": "nope"}):
        s.handle(junk)
    assert prices["BTC-EUR"] == 80005.5


def test_extremes_catch_a_spike_between_checks():
    s, prices = make()
    for px in ("100", "97.5", "100.4"):
        s.handle({"event": "ticker", "market": "BTC-EUR", "lastPrice": px})
    assert s.extremes["BTC-EUR"] == [97.5, 100.4] and prices["BTC-EUR"] == 100.4
    s.reset_extremes()
    assert s.extremes == {}


async def test_paper_fill_uses_extremes_but_not_stale_ones():
    s, prices = make()
    broker = PaperBroker(prices, extremes=s.extremes)
    s.handle({"event": "ticker", "market": "BTC-EUR", "lastPrice": "100"})
    order = {**await broker.limit_buy("BTC-EUR", 98.0, 25), "market": "BTC-EUR", "side": "buy"}
    s.reset_extremes()  # order is net geplaatst: eerdere koersen tellen niet
    assert (await broker.poll(order))["status"] == "open"
    for px in ("97.9", "100.2"):  # korte dip tussen twee controles
        s.handle({"event": "ticker", "market": "BTC-EUR", "lastPrice": px})
    assert (await broker.poll(order))["status"] == "filled"
    s.reset_extremes()
    sell = {**await broker.limit_sell("BTC-EUR", 101.0, 0.25), "market": "BTC-EUR", "side": "sell"}
    assert (await broker.poll(sell))["status"] == "open"


class FakeWS:
    def __init__(self, messages):
        self.sent, self._messages = [], messages

    async def send_json(self, data):
        self.sent.append(data)

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for m in self._messages:
            yield m


class FakeSession:
    def __init__(self, sockets):
        self.sockets, self.connects = sockets, 0

    def ws_connect(self, url, heartbeat=None):
        session = self

        class Ctx:
            async def __aenter__(self):
                session.connects += 1
                if not session.sockets:
                    raise asyncio.CancelledError
                item = session.sockets.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item

            async def __aexit__(self, *exc):
                return False

        return Ctx()


async def test_run_subscribes_reads_and_reconnects(monkeypatch):
    async def nosleep(_):
        return None

    monkeypatch.setattr("custom_components.bitvavo_bot.stream.asyncio.sleep", nosleep)
    text = lambda d: aiohttp.WSMessage(aiohttp.WSMsgType.TEXT, json.dumps(d), None)
    ws1 = FakeWS([text({"event": "ticker", "market": "BTC-EUR", "lastPrice": "1"}), aiohttp.WSMessage(aiohttp.WSMsgType.TEXT, "geen json", None), aiohttp.WSMessage(aiohttp.WSMsgType.CLOSED, None, None)])
    ws2 = FakeWS([text({"event": "ticker", "market": "BTC-EUR", "lastPrice": "2"})])
    session = FakeSession([ws1, aiohttp.ClientError("weg"), ws2])
    prices = {}
    s = BitvavoStream(session, lambda: ["BTC-EUR"], prices)
    with pytest.raises(asyncio.CancelledError):
        await REAL_RUN(s)
    assert session.connects == 4  # 2 gelukt, 1 mislukt, daarna gestopt door de test
    assert prices == {"BTC-EUR": 2.0} and not s.connected
    names = [c["name"] for c in ws1.sent[0]["channels"]]
    assert ws1.sent[0]["action"] == "subscribe" and names == ["ticker", "candles"]
    assert ws1.sent[0]["channels"][1]["interval"] == ["1h"]
