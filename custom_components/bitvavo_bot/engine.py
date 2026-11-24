"""Handelsmotor: voert het plan uit op een 'boek' via een broker (paper of live).

Geen Home Assistant-afhankelijkheden. De regels zijn dezelfde als in de backtest:
- signaalstrategieën: beslissen op een afgesloten bar, marktorder direct daarna;
- grid: limietorders op basis van het gemiddelde t/m de laatste afgesloten bar.

Het boek is een gewone dict, zodat het als JSON op schijf kan.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, Sequence

from .strategies import Bar, live_target, sma

_LOGGER = logging.getLogger(__name__)

MAX_TRADES_KEPT = 500
EXIT_COST = 0.003  # taker + slippage, voor de waardering van open posities


@dataclass
class Limits:
    max_per_buy: float = 25.0
    max_total: float = 250.0
    max_day_loss: float = 25.0


class BrokerError(Exception):
    """Order kon niet worden uitgevoerd."""


class Broker(Protocol):
    async def price(self, market: str) -> float | None: ...
    async def market_buy(self, market: str, quote: float) -> dict | None: ...
    async def market_sell(self, market: str, amount: float) -> dict | None: ...
    async def limit_buy(self, market: str, price: float, quote: float) -> dict | None: ...
    async def limit_sell(self, market: str, price: float, amount: float) -> dict | None: ...
    async def poll(self, order: dict) -> dict: ...
    async def cancel(self, order: dict) -> dict: ...


def new_book(now_ms: int | None = None) -> dict:
    return {
        "started": now_ms or int(time.time() * 1000),
        "positions": [],
        "orders": [],
        "trades": [],
        "realized": 0.0,
        "trade_count": 0,
        "win_count": 0,
        "last_bar": {},
        "sold_levels": {},
        "day": "",
        "day_start_pnl": 0.0,
        "day_paused": False,
        "last_error": None,
    }


def _day(now_ms: int) -> str:
    return datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).astimezone().strftime("%Y-%m-%d")


class Engine:
    def __init__(self, book: dict, broker: Broker, name: str = "paper") -> None:
        self.book = book
        self.broker = broker
        self.name = name

    # ----------------------------------------------------------------- inzicht

    def invested(self) -> float:
        b = self.book
        return sum(p["cost"] for p in b["positions"]) + sum(
            o["price"] * o["amount"] for o in b["orders"] if o["side"] == "buy"
        )

    def unrealized(self, prices: dict[str, float]) -> float:
        total = 0.0
        for p in self.book["positions"]:
            px = prices.get(p["market"])
            if px:
                total += p["amount"] * px * (1 - EXIT_COST) - p["cost"]
        return total

    def pnl(self, prices: dict[str, float]) -> float:
        return self.book["realized"] + self.unrealized(prices)

    def summary(self, prices: dict[str, float]) -> dict:
        b = self.book
        pnl = self.pnl(prices)
        return {
            "resultaat": round(pnl, 2),
            "gerealiseerd": round(b["realized"], 2),
            "ongerealiseerd": round(self.unrealized(prices), 2),
            "vandaag": round(pnl - b["day_start_pnl"], 2),
            "ingezet": round(self.invested(), 2),
            "trades": b["trade_count"],
            "winst_pct_trades": round(100 * b["win_count"] / b["trade_count"], 1) if b["trade_count"] else None,
            "open_posities": len(b["positions"]),
            "open_orders": len(b["orders"]),
            "dagstop": b["day_paused"],
            "gestart": datetime.fromtimestamp(b["started"] / 1000, tz=timezone.utc).isoformat(),
            "laatste_fout": b["last_error"],
            "posities": [
                {
                    "markt": p["market"],
                    "inzet": round(p["cost"], 2),
                    "koers_in": p["entry_px"],
                    "waarde": round(p["amount"] * prices[p["market"]], 2) if prices.get(p["market"]) else None,
                }
                for p in b["positions"]
            ],
            "laatste_trades": b["trades"][-10:][::-1],
        }

    # ------------------------------------------------------------- dagbewaking

    def roll_day(self, now_ms: int, prices: dict[str, float], limits: Limits) -> None:
        """Nieuwe dag = nieuwe dagstand; bij te groot dagverlies geen nieuwe aankopen."""
        b = self.book
        today = _day(now_ms)
        pnl = self.pnl(prices)
        if b["day"] != today:
            b["day"], b["day_start_pnl"], b["day_paused"] = today, pnl, False
        if not b["day_paused"] and pnl - b["day_start_pnl"] <= -abs(limits.max_day_loss):
            b["day_paused"] = True
            _LOGGER.warning("[%s] dagverlies-limiet bereikt: geen nieuwe aankopen vandaag", self.name)

    def _may_buy(self, limits: Limits, allow: bool) -> bool:
        return (
            allow
            and not self.book["day_paused"]
            and self.invested() + limits.max_per_buy <= limits.max_total + 1e-9
        )

    # ------------------------------------------------------------ boekhouding

    def _open_position(self, market: str, fill: dict, level: int | None, bar_ts: int) -> dict:
        pos = {
            "id": uuid.uuid4().hex[:12],
            "market": market,
            "amount": fill["amount"],
            "cost": fill["quote"] + fill.get("fee", 0.0),
            "entry_px": round((fill["quote"]) / fill["amount"], 10) if fill["amount"] else 0.0,
            "entry_ts": int(time.time() * 1000),
            "entry_bar": bar_ts,
            "level": level,
        }
        self.book["positions"].append(pos)
        return pos

    def _apply_sell(self, pos: dict, fill: dict, reason: str) -> None:
        b = self.book
        amount = min(fill["amount"], pos["amount"])
        if amount <= 0:
            return
        share = amount / pos["amount"]
        # Afronding op de beurs laat soms een restje achter dat te klein is om te
        # verkopen: dat telt als verloren en de positie is dan gewoon dicht.
        dust = (pos["amount"] - amount) * pos["entry_px"] < 1.0
        closed = share >= 0.999 or dust
        cost_part = pos["cost"] if closed else pos["cost"] * share
        proceeds = fill["quote"] - fill.get("fee", 0.0)
        pnl = proceeds - cost_part
        b["realized"] += pnl
        pos["amount"] -= amount
        pos["cost"] -= cost_part
        if closed:
            b["positions"] = [p for p in b["positions"] if p["id"] != pos["id"]]
            if pos.get("level") is not None:
                b["sold_levels"].setdefault(pos["market"], []).append(pos["level"])
        b["trade_count"] += 1
        b["win_count"] += 1 if pnl > 0 else 0
        b["trades"].append(
            {
                "markt": pos["market"],
                "in": pos["entry_ts"],
                "uit": int(time.time() * 1000),
                "inzet": round(cost_part, 2),
                "resultaat": round(pnl, 2),
                "pct": round(100 * pnl / cost_part, 2) if cost_part else 0.0,
                "reden": reason,
            }
        )
        del b["trades"][:-MAX_TRADES_KEPT]

    async def _sell_market(self, pos: dict, reason: str) -> None:
        try:
            fill = await self.broker.market_sell(pos["market"], pos["amount"])
        except BrokerError as err:
            self._error(f"verkoop {pos['market']}: {err}")
            return
        if fill:
            self._apply_sell(pos, fill, reason)

    def _error(self, msg: str) -> None:
        self.book["last_error"] = f"{datetime.now().strftime('%d-%m %H:%M')} {msg}"
        _LOGGER.warning("[%s] %s", self.name, msg)

    # -------------------------------------------------------------- per bar

    async def on_bar(
        self,
        market: str,
        bars: Sequence[Bar],
        strategy: str,
        kwargs: dict | None,
        limits: Limits,
        allow_buy: bool,
        target: Sequence[int] | None = None,
    ) -> None:
        """Verwerk nieuw afgesloten bars voor één markt. kwargs=None: niet instappen
        (de selectie vond niets dat werkte), wel bestaande posities afbouwen."""
        if not bars:
            return
        b = self.book
        last_seen = b["last_bar"].get(market)
        bar_ts = bars[-1][0]
        if last_seen is not None and bar_ts <= last_seen:
            return
        try:
            if strategy == "grid":
                await self._grid_bar(market, bars, kwargs, limits, allow_buy)
            else:
                await self._signal_bar(market, bars, strategy, kwargs, limits, allow_buy, last_seen, target)
        except BrokerError as err:
            self._error(f"{market}: {err}")
        b["last_bar"][market] = bar_ts

    async def _signal_bar(self, market, bars, strategy, kwargs, limits, allow_buy, last_seen, target=None) -> None:
        held = [p for p in self.book["positions"] if p["market"] == market]
        if kwargs is None:
            return  # geen parameterset: bestaande positie wacht op een nieuwe selectie
        if target is None:
            target = live_target(strategy, kwargs, bars)
        if held and not target[-1]:
            for pos in held:
                await self._sell_market(pos, "signaal")
            return
        if held or not target[-1] or last_seen is None:
            return  # eerste bar na de start: nooit halverwege een beweging instappen
        # alleen instappen op een verse omslag 0 -> 1 in de nieuw afgesloten bars
        fresh = any(
            target[i] and not target[i - 1]
            for i in range(1, len(bars))
            if bars[i][0] > last_seen
        )
        if fresh and self._may_buy(limits, allow_buy):
            fill = await self.broker.market_buy(market, limits.max_per_buy)
            if fill:
                self._open_position(market, fill, None, bars[-1][0])

    async def _grid_bar(self, market, bars, kwargs, limits, allow_buy) -> None:
        b = self.book
        bar_ts = bars[-1][0]
        tf_ms = bars[-1][0] - bars[-2][0] if len(bars) > 1 else 3_600_000
        max_hold = (kwargs or {}).get("max_hold", 200)
        # 1. tijdstop
        for pos in [p for p in b["positions"] if p["market"] == market]:
            if (bar_ts - pos["entry_bar"]) / tf_ms >= max_hold:
                await self._cancel_orders(market, pos_id=pos["id"])
                if any(p["id"] == pos["id"] for p in b["positions"]):
                    await self._sell_market(pos, "tijdstop")
        # 2. oude kooplimieten weg (de prijzen schuiven mee met het gemiddelde)
        await self._cancel_orders(market, side="buy", bar_ts=bar_ts)
        sold = set(b["sold_levels"].pop(market, []))
        if kwargs is None:
            return
        ref = sma([x[4] for x in bars], kwargs["ref_n"])[-1]
        if ref is None:
            return
        taken = {p["level"] for p in b["positions"] if p["market"] == market}
        price_now = await self.broker.price(market)
        for k in range(1, kwargs["levels"] + 1):
            if k in taken or k in sold or not self._may_buy(limits, allow_buy):
                continue
            level_px = ref * (1 - k * kwargs["step"])
            if price_now is not None and price_now <= level_px:
                fill = await self.broker.market_buy(market, limits.max_per_buy)
                if fill:
                    pos = self._open_position(market, fill, k, bar_ts)
                    await self._place_take_profit(pos, kwargs["step"])
                continue
            order = await self.broker.limit_buy(market, level_px, limits.max_per_buy)
            if order:
                order.update({"market": market, "side": "buy", "level": k, "bar": bar_ts, "step": kwargs["step"]})
                b["orders"].append(order)

    async def _place_take_profit(self, pos: dict, step: float) -> None:
        try:
            order = await self.broker.limit_sell(pos["market"], pos["entry_px"] * (1 + step), pos["amount"])
        except BrokerError as err:
            self._error(f"verkooplimiet {pos['market']}: {err}")
            return
        if order:
            order.update({"market": pos["market"], "side": "sell", "pos_id": pos["id"], "level": pos["level"]})
            self.book["orders"].append(order)

    async def _cancel_orders(self, market=None, side=None, pos_id=None, bar_ts=None) -> None:
        b = self.book
        for order in list(b["orders"]):
            if market and order["market"] != market:
                continue
            if side and order["side"] != side:
                continue
            if pos_id and order.get("pos_id") != pos_id:
                continue
            try:
                result = await self.broker.cancel(order)
            except BrokerError as err:
                self._error(f"annuleren {order['market']}: {err}")
                continue
            b["orders"].remove(order)
            self._settle(order, result, bar_ts or order.get("bar") or 0)

    def _settle(self, order: dict, result: dict, bar_ts: int) -> dict | None:
        """Verwerk wat er (deels) van een order is uitgevoerd."""
        if result.get("amount", 0) <= 0:
            return None
        if order["side"] == "buy":
            return self._open_position(order["market"], result, order.get("level"), bar_ts)
        pos = next((p for p in self.book["positions"] if p["id"] == order.get("pos_id")), None)
        if pos:
            self._apply_sell(pos, result, "grid-doel")
        return None

    # ------------------------------------------------------------ per minuut

    async def poll(self) -> None:
        """Kijk of rustende limietorders zijn uitgevoerd."""
        b = self.book
        for order in list(b["orders"]):
            try:
                result = await self.broker.poll(order)
            except BrokerError as err:
                self._error(f"orderstatus {order['market']}: {err}")
                continue
            if result["status"] == "open":
                continue
            b["orders"].remove(order)
            pos = self._settle(order, result, order.get("bar") or 0)
            if pos is not None and order["side"] == "buy":
                await self._place_take_profit(pos, order["step"])

    # -------------------------------------------------------------- noodstop

    async def flatten(self) -> None:
        """Alles annuleren en alles verkopen."""
        await self._cancel_orders()
        for pos in list(self.book["positions"]):
            await self._sell_market(pos, "noodstop")
