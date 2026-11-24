"""Brokers: paper (gesimuleerd op live koersen) en live (echte Bitvavo-orders)."""

from __future__ import annotations

import asyncio
import uuid
from decimal import ROUND_DOWN, ROUND_UP, Decimal

from .api import BitvavoApiError, BitvavoError, BitvavoNetworkError, BitvavoPrivateClient
from .engine import BrokerError
from .strategies import Costs

OPERATOR_ID = 8401  # vaste bot-id die Bitvavo bij elke order verlangt


class PaperBroker:
    """Vult orders tegen de laatst bekende koers, met dezelfde kosten als de backtest."""

    def __init__(
        self, prices: dict[str, float], costs: Costs | None = None, extremes: dict[str, list[float]] | None = None
    ) -> None:
        self._prices = prices
        self._costs = costs or Costs()
        # laagste/hoogste koers sinds de vorige controle (uit de koersstroom), zodat
        # een korte uitschieter tussen twee controles niet gemist wordt
        self._extremes = extremes if extremes is not None else {}

    async def price(self, market: str) -> float | None:
        return self._prices.get(market)

    def _px(self, market: str) -> float:
        px = self._prices.get(market)
        if not px:
            raise BrokerError(f"geen koers voor {market}")
        return px

    async def market_buy(self, market: str, quote: float) -> dict:
        c = self._costs
        gross = quote / (1 + c.taker)
        return {"amount": gross / (self._px(market) * (1 + c.slippage)), "quote": gross, "fee": gross * c.taker}

    async def market_sell(self, market: str, amount: float) -> dict:
        c = self._costs
        gross = amount * self._px(market) * (1 - c.slippage)
        return {"amount": amount, "quote": gross, "fee": gross * c.taker}

    async def limit_buy(self, market: str, price: float, quote: float) -> dict:
        return {"id": uuid.uuid4().hex, "price": price, "amount": quote / (1 + self._costs.maker) / price}

    async def limit_sell(self, market: str, price: float, amount: float) -> dict:
        return {"id": uuid.uuid4().hex, "price": price, "amount": amount}

    async def poll(self, order: dict) -> dict:
        px = self._prices.get(order["market"])
        low, high = self._extremes.get(order["market"]) or (px, px)
        if px is not None:
            low, high = min(low, px), max(high, px)
        # alleen gevuld als de koers strikt dóór de limiet is gegaan (zoals in de backtest)
        hit = px is not None and (low < order["price"] if order["side"] == "buy" else high > order["price"])
        if not hit:
            return {"status": "open", "amount": 0.0}
        quote = order["amount"] * order["price"]
        return {"status": "filled", "amount": order["amount"], "quote": quote, "fee": quote * self._costs.maker}

    async def cancel(self, order: dict) -> dict:
        return {"status": "canceled", "amount": 0.0}


def _floor(value: float, decimals: int) -> Decimal:
    return Decimal(str(value)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_DOWN)


def _to_tick(price: float, tick: str, up: bool) -> Decimal:
    t = Decimal(tick)
    return (Decimal(str(price)) / t).to_integral_value(rounding=ROUND_UP if up else ROUND_DOWN) * t


def _s(d: Decimal) -> str:
    return format(d.normalize(), "f")


class LiveBroker:
    """Echte orders op Bitvavo."""

    def __init__(
        self,
        client: BitvavoPrivateClient,
        prices: dict[str, float],
        markets: dict[str, dict],
        costs: Costs | None = None,
    ) -> None:
        self._c = client
        self._prices = prices
        self._markets = markets
        self._costs = costs or Costs()

    async def price(self, market: str) -> float | None:
        return self._prices.get(market)

    def _info(self, market: str) -> dict:
        info = self._markets.get(market)
        if not info or info.get("status") != "trading":
            raise BrokerError(f"{market} is nu niet verhandelbaar")
        return info

    async def eur_available(self) -> float:
        try:
            rows = await self._c.request("GET", "/balance", {"symbol": "EUR"})
        except BitvavoError as err:
            raise BrokerError(str(err)) from err
        return float(rows[0]["available"]) if rows else 0.0

    async def _base_available(self, market: str) -> float:
        rows = await self._c.request("GET", "/balance", {"symbol": self._info(market)["base"]})
        return float(rows[0]["available"]) if rows else 0.0

    async def _place(self, body: dict) -> dict:
        """Plaats een order. Bij een netwerkfout eerst nagaan of hij tóch is aangekomen."""
        body = {**body, "operatorId": OPERATOR_ID, "clientOrderId": str(uuid.uuid4())}
        try:
            return await self._c.request("POST", "/order", body=body)
        except BitvavoNetworkError as err:
            await asyncio.sleep(2)
            try:
                return await self._c.request(
                    "GET", "/order", {"market": body["market"], "clientOrderId": body["clientOrderId"]}
                )
            except BitvavoApiError:
                raise BrokerError(f"order niet aangekomen: {err}") from err
            except BitvavoNetworkError as err2:
                raise BrokerError(f"orderstatus onbekend, controleer Bitvavo: {err2}") from err2
        except BitvavoApiError as err:
            raise BrokerError(str(err)) from err

    async def _get(self, market: str, order_id: str) -> dict:
        try:
            return await self._c.request("GET", "/order", {"market": market, "orderId": order_id})
        except BitvavoError as err:
            raise BrokerError(str(err)) from err

    def _result(self, market: str, o: dict) -> dict:
        info = self._markets.get(market, {})
        amount = float(o.get("filledAmount") or 0)
        quote = float(o.get("filledAmountQuote") or 0)
        fee = float(o.get("feePaid") or 0)
        if o.get("feeCurrency") and o["feeCurrency"] == info.get("base"):
            if o.get("side") == "buy":
                amount -= fee  # kosten in de munt zelf: je ontvangt minder
            fee = 0.0
        status = o.get("status")
        mapped = "filled" if status == "filled" else "canceled" if status in ("canceled", "expired", "rejected") else "open"
        return {"status": mapped, "amount": amount, "quote": quote, "fee": fee}

    async def _await_done(self, market: str, order: dict) -> dict:
        for _ in range(6):
            res = self._result(market, order)
            if res["status"] != "open":
                return res
            await asyncio.sleep(1)
            order = await self._get(market, order["orderId"])
        return self._result(market, order)

    async def market_buy(self, market: str, quote: float) -> dict | None:
        info = self._info(market)
        amount_quote = _floor(quote, int(info.get("notionalDecimals", 2)))
        if amount_quote < Decimal(str(info.get("minOrderInQuoteAsset", "5"))):
            raise BrokerError(f"bedrag onder het Bitvavo-minimum voor {market}")
        order = await self._place(
            {"market": market, "side": "buy", "orderType": "market", "amountQuote": _s(amount_quote)}
        )
        res = await self._await_done(market, order)
        if res["amount"] <= 0:
            raise BrokerError(f"koop {market} niet uitgevoerd ({order.get('status')})")
        return res

    async def market_sell(self, market: str, amount: float) -> dict | None:
        info = self._info(market)
        try:
            amount = min(amount, await self._base_available(market))
        except BitvavoError as err:
            raise BrokerError(str(err)) from err
        qty = _floor(amount, int(info.get("quantityDecimals", 8)))
        px = self._prices.get(market) or 0
        if qty < Decimal(str(info.get("minOrderInBaseAsset", "0"))) or float(qty) * px < float(
            info.get("minOrderInQuoteAsset", "5")
        ):
            raise BrokerError(f"positie {market} te klein om te verkopen")
        order = await self._place({"market": market, "side": "sell", "orderType": "market", "amount": _s(qty)})
        res = await self._await_done(market, order)
        if res["amount"] <= 0:
            raise BrokerError(f"verkoop {market} niet uitgevoerd ({order.get('status')})")
        return res

    async def _limit(self, market: str, side: str, price: Decimal, qty: Decimal) -> dict | None:
        info = self._info(market)
        if qty < Decimal(str(info.get("minOrderInBaseAsset", "0"))) or qty * price < Decimal(
            str(info.get("minOrderInQuoteAsset", "5"))
        ):
            raise BrokerError(f"limietorder {market} onder het Bitvavo-minimum")
        order = await self._place(
            {
                "market": market,
                "side": side,
                "orderType": "limit",
                "amount": _s(qty),
                "price": _s(price),
                "postOnly": True,
            }
        )
        if order.get("status") in ("canceled", "expired", "rejected"):
            return None  # postOnly: zou direct als taker zijn uitgevoerd
        return {"id": order["orderId"], "price": float(price), "amount": float(qty)}

    async def limit_buy(self, market: str, price: float, quote: float) -> dict | None:
        info = self._info(market)
        px = _to_tick(price, info.get("tickSize", "0.01"), up=False)
        if px <= 0:
            return None
        qty = _floor(quote / (1 + self._costs.maker) / float(px), int(info.get("quantityDecimals", 8)))
        return await self._limit(market, "buy", px, qty)

    async def limit_sell(self, market: str, price: float, amount: float) -> dict | None:
        info = self._info(market)
        px = _to_tick(price, info.get("tickSize", "0.01"), up=True)
        return await self._limit(market, "sell", px, _floor(amount, int(info.get("quantityDecimals", 8))))

    async def poll(self, order: dict) -> dict:
        return self._result(order["market"], await self._get(order["market"], order["id"]))

    async def cancel(self, order: dict) -> dict:
        try:
            await self._c.request(
                "DELETE", "/order", {"market": order["market"], "orderId": order["id"], "operatorId": OPERATOR_ID}
            )
        except BitvavoApiError:
            pass  # al gevuld of al weg: de status hieronder is leidend
        except BitvavoNetworkError as err:
            raise BrokerError(str(err)) from err
        res = self._result(order["market"], await self._get(order["market"], order["id"]))
        if res["status"] == "open":
            raise BrokerError(f"order {order['market']} staat nog open na annuleren")
        return res
