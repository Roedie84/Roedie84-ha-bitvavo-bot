import hashlib
import hmac
import uuid

import pytest

from custom_components.bitvavo_bot.api import BitvavoApiError, BitvavoNetworkError, BitvavoPrivateClient
from custom_components.bitvavo_bot.brokers import OPERATOR_ID, LiveBroker
from custom_components.bitvavo_bot.engine import BrokerError, Engine, Limits, new_book

from .helpers import H

MARKETS = {
    "BTC-EUR": {
        "market": "BTC-EUR", "status": "trading", "base": "BTC", "quote": "EUR",
        "quantityDecimals": 6, "notionalDecimals": 2, "tickSize": "1",
        "minOrderInBaseAsset": "0.0001", "minOrderInQuoteAsset": "5",
    },
    "OFF-EUR": {"market": "OFF-EUR", "status": "halted", "base": "OFF", "quote": "EUR"},
}


class FakeExchange:
    """Bootst het relevante gedrag van de Bitvavo REST-API na."""

    def __init__(self, price=80000.0, eur=1000.0):
        self.price, self.eur, self.btc = price, eur, 0.0
        self.orders, self.calls = {}, []
        self.coins = {}  # voor het volledige saldo-overzicht
        self.drop_next_post = False  # order komt aan, antwoord gaat verloren
        self.fee_in_base = False

    async def request(self, method, path, params=None, body=None):
        self.calls.append((method, path, params, body))
        if path == "/account/fees":
            return {"tier": "1", "volume": "150000", "taker": "0.002", "maker": "0.001"}
        if path == "/balance":
            if not params:
                return [{"symbol": "EUR", "available": str(self.eur), "inOrder": "0"}] + [
                    {"symbol": sym, "available": str(v), "inOrder": "0"} for sym, v in self.coins.items()
                ]
            sym = params["symbol"]
            return [{"symbol": sym, "available": str(self.eur if sym == "EUR" else self.btc), "inOrder": "0"}]
        if method == "POST":
            assert body["operatorId"] == OPERATOR_ID
            uuid.UUID(body["clientOrderId"])
            for field in ("amount", "amountQuote", "price"):
                assert isinstance(body.get(field, ""), str)
            order = self._create(body)
            self.orders[order["orderId"]] = order
            if self.drop_next_post:
                self.drop_next_post = False
                raise BitvavoNetworkError("timeout")
            return dict(order)
        if method == "GET":
            for o in self.orders.values():
                if o["orderId"] == params.get("orderId") or o["clientOrderId"] == params.get("clientOrderId"):
                    return dict(o)
            raise BitvavoApiError(404, 240, "No order found")
        if method == "DELETE":
            assert params["operatorId"] == OPERATOR_ID
            o = self.orders[params["orderId"]]
            if o["status"] in ("filled", "canceled"):
                raise BitvavoApiError(400, 240, "No active order found")
            o["status"] = "canceled"
            return {"orderId": o["orderId"]}
        raise AssertionError(path)

    def _create(self, b):
        o = {
            "orderId": str(uuid.uuid4()), "clientOrderId": b["clientOrderId"], "market": b["market"],
            "side": b["side"], "orderType": b["orderType"], "status": "new",
            "filledAmount": "0", "filledAmountQuote": "0", "feePaid": "0", "feeCurrency": "EUR",
        }
        if b["orderType"] == "market":
            if b["side"] == "buy":
                quote = float(b["amountQuote"])
                assert round(quote, 2) == quote
                amount = quote / self.price
                self.eur -= quote * 1.0025
                self.btc += amount
            else:
                amount = float(b["amount"])
                assert amount <= self.btc + 1e-12, "meer verkopen dan er is"
                assert round(amount, 6) == amount
                quote = amount * self.price
                self.btc -= amount
                self.eur += quote * 0.9975
            o.update(status="filled", filledAmount=str(amount), filledAmountQuote=str(quote), feePaid=str(quote * 0.0025))
            if self.fee_in_base and b["side"] == "buy":
                o.update(feePaid=str(amount * 0.0025), feeCurrency="BTC")
        else:
            assert b["postOnly"] is True
            assert float(b["price"]) == int(float(b["price"])), "prijs niet op tick"
            o.update(price=b["price"], amount=b["amount"])
        return o

    def fill(self, order_id):
        o = self.orders[order_id]
        amount, px = float(o["amount"]), float(o["price"])
        o.update(status="filled", filledAmount=str(amount), filledAmountQuote=str(amount * px), feePaid=str(amount * px * 0.0015))
        self.btc += amount if o["side"] == "buy" else -amount


def broker(ex, prices=None):
    return LiveBroker(ex, prices if prices is not None else {"BTC-EUR": ex.price}, MARKETS)


def test_signature_matches_documented_scheme(monkeypatch):
    monkeypatch.setattr("custom_components.bitvavo_bot.api.time.time", lambda: 1548172481.125)
    c = BitvavoPrivateClient(None, "key", "bitvavo")
    body = '{"market":"BTC-EUR","side":"buy","price":"5000","amount":"1.23","orderType":"limit"}'
    h = c._headers("POST", "/order", body)
    expected = hmac.new(b"bitvavo", ("1548172481125POST/v2/order" + body).encode(), hashlib.sha256).hexdigest()
    assert h["Bitvavo-Access-Signature"] == expected and len(expected) == 64
    assert h["Bitvavo-Access-Timestamp"] == "1548172481125" and h["Bitvavo-Access-Key"] == "key"
    g = c._headers("GET", "/order?market=BTC-EUR&orderId=x", "")
    assert g["Bitvavo-Access-Signature"] != expected


async def test_market_buy_and_sell_use_real_fills():
    ex = FakeExchange()
    b = broker(ex)
    assert await b.eur_available() == 1000.0
    fill = await b.market_buy("BTC-EUR", 25.004)
    assert ex.calls[-1][3]["amountQuote"] == "25"
    assert fill["quote"] == pytest.approx(25.0) and fill["fee"] == pytest.approx(0.0625)
    sold = await b.market_sell("BTC-EUR", fill["amount"])
    assert sold["amount"] <= fill["amount"]  # naar beneden afgerond op 6 decimalen
    assert sold["quote"] - sold["fee"] == pytest.approx(25 * 0.9975, abs=0.1)


async def test_fee_in_base_reduces_received_amount():
    ex = FakeExchange()
    ex.fee_in_base = True
    fill = await broker(ex).market_buy("BTC-EUR", 25)
    assert fill["fee"] == 0.0 and fill["amount"] == pytest.approx(25 / 80000 * 0.9975)


async def test_minimums_and_halted_market_are_refused_before_ordering():
    ex = FakeExchange()
    b = broker(ex)
    with pytest.raises(BrokerError, match="minimum"):
        await b.market_buy("BTC-EUR", 3)
    with pytest.raises(BrokerError, match="niet verhandelbaar"):
        await b.market_buy("OFF-EUR", 25)
    ex.btc = 0.00001
    with pytest.raises(BrokerError, match="te klein"):
        await b.market_sell("BTC-EUR", 0.00001)
    assert not [c for c in ex.calls if c[0] == "POST"]


async def test_lost_response_never_double_orders():
    ex = FakeExchange()
    ex.drop_next_post = True
    b = broker(ex)
    import custom_components.bitvavo_bot.brokers as mod

    async def nosleep(_):
        return None

    mod.asyncio.sleep, keep = nosleep, mod.asyncio.sleep
    try:
        fill = await b.market_buy("BTC-EUR", 25)
    finally:
        mod.asyncio.sleep = keep
    assert fill["quote"] == pytest.approx(25.0)
    assert len([c for c in ex.calls if c[0] == "POST"]) == 1
    assert len(ex.orders) == 1


async def test_limit_orders_tick_postonly_poll_cancel():
    ex = FakeExchange()
    b = broker(ex)
    buy = await b.limit_buy("BTC-EUR", 78400.7, 25)
    assert buy["price"] == 78400.0  # koop naar beneden op de tick
    order = {**buy, "market": "BTC-EUR", "side": "buy"}
    assert (await b.poll(order))["status"] == "open"
    ex.fill(buy["id"])
    res = await b.poll(order)
    assert res["status"] == "filled" and res["amount"] == pytest.approx(buy["amount"])
    sell = await b.limit_sell("BTC-EUR", 79968.2, res["amount"])
    assert sell["price"] == 79969.0  # verkoop naar boven op de tick
    cancelled = await b.cancel({**sell, "market": "BTC-EUR", "side": "sell"})
    assert cancelled == {"status": "canceled", "amount": 0.0, "quote": 0.0, "fee": 0.0}
    # annuleren van een order die net gevuld is: de vulling telt
    buy2 = await b.limit_buy("BTC-EUR", 78000, 25)
    ex.fill(buy2["id"])
    late = await b.cancel({**buy2, "market": "BTC-EUR", "side": "buy"})
    assert late["status"] == "filled" and late["amount"] > 0


async def test_engine_end_to_end_on_live_broker():
    ex = FakeExchange()
    prices = {"BTC-EUR": 80000.0}
    e = Engine(new_book(0), broker(ex, prices), "live")
    lim = Limits(25, 100, 25)
    bars = lambda n: [(i * H, 80000.0, 80000.0, 80000.0, 80000.0, 1.0) for i in range(n)]
    await e.on_bar("BTC-EUR", bars(2), "ema_cross", {}, lim, True, target=[0, 0])
    await e.on_bar("BTC-EUR", bars(3), "ema_cross", {}, lim, True, target=[0, 0, 1])
    (pos,) = e.book["positions"]
    assert pos["cost"] == pytest.approx(25 * 1.0025)
    assert ex.btc == pytest.approx(pos["amount"])
    ex.price = prices["BTC-EUR"] = 84000.0
    await e.on_bar("BTC-EUR", bars(4), "ema_cross", {}, lim, True, target=[0, 0, 1, 0])
    assert e.book["positions"] == [] and e.book["realized"] > 1.0
    assert ex.btc < 1e-6  # hooguit afrondingsstof
    assert e.book["realized"] == pytest.approx(ex.eur - 1000.0, abs=0.01)  # boekhouding = echte saldo


async def test_exchange_rejection_is_recorded_not_raised():
    ex = FakeExchange(eur=0)

    async def refuse(method, path, params=None, body=None):
        if method == "POST":
            raise BitvavoApiError(400, 216, "Insufficient balance")
        return await FakeExchange.request(ex, method, path, params, body)

    ex.request = refuse
    e = Engine(new_book(0), broker(ex), "live")
    bars = lambda n: [(i * H, 80000.0, 80000.0, 80000.0, 80000.0, 1.0) for i in range(n)]
    await e.on_bar("BTC-EUR", bars(2), "ema_cross", {}, Limits(), True, target=[0, 0])
    await e.on_bar("BTC-EUR", bars(3), "ema_cross", {}, Limits(), True, target=[0, 0, 1])
    assert e.book["positions"] == []
    assert "Insufficient balance" in e.book["last_error"]


async def test_paper_market_orders_use_real_bid_ask_and_depth():
    import time as _t

    from custom_components.bitvavo_bot.brokers import DEPTH_PENALTY, PaperBroker
    from custom_components.bitvavo_bot.strategies import Costs

    prices, quotes = {"A-EUR": 100.0}, {}
    b = PaperBroker(prices, Costs(), quotes=quotes)
    plain = await b.market_buy("A-EUR", 100.25)
    assert plain["amount"] == pytest.approx(100 / (100 * 1.0005))  # geen stand bekend: vaste slippage
    quotes["A-EUR"] = {"bid": 99.8, "ask": 100.3, "bid_size": 50.0, "ask_size": 50.0, "t": _t.time()}
    buy = await b.market_buy("A-EUR", 100.25)
    assert buy["amount"] == pytest.approx(100 / 100.3)  # koopt tegen de laatprijs
    sell = await b.market_sell("A-EUR", 1.0)
    assert sell["quote"] == pytest.approx(99.8)  # verkoopt tegen de biedprijs
    quotes["A-EUR"]["ask_size"] = 0.2  # te weinig aanbod tegen de beste prijs
    thin = await b.market_buy("A-EUR", 100.25)
    assert thin["amount"] == pytest.approx(100 / (100.3 * (1 + DEPTH_PENALTY)))
    quotes["A-EUR"]["t"] = _t.time() - 600  # verouderde stand telt niet
    old = await b.market_buy("A-EUR", 100.25)
    assert old["amount"] == pytest.approx(plain["amount"])
    b.set_costs(Costs(taker=0.002, maker=0.001))
    cheap = await b.market_buy("A-EUR", 100.2)
    assert cheap["fee"] == pytest.approx(0.2)


async def test_holdings_fees_and_unsellable_dust_is_written_off():
    ex = FakeExchange()
    ex.coins = {"BTC": 0.5, "ETH": 2.0}
    b = broker(ex)
    assert await b.holdings() == {"EUR": 1000.0, "BTC": 0.5, "ETH": 2.0}
    assert await b.fees() == {"taker": 0.002, "maker": 0.001, "tier": "1"}
    e = Engine(new_book(0), b, "live")
    e.book["positions"] = [{"id": "p1", "market": "BTC-EUR", "amount": 0.00001, "cost": 0.8, "entry_px": 80000.0, "entry_ts": 1, "entry_bar": 0, "level": None}]
    ex.btc = 0.00001
    await e._sell_market(e.book["positions"][0], "signaal")
    assert e.book["positions"] == [] and e.book["last_error"] is None
    assert e.book["trades"][-1]["reden"] == "restant te klein om te verkopen"
    assert e.book["realized"] == pytest.approx(-0.8)
