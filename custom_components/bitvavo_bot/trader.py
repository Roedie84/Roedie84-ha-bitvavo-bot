"""Handel: plan kiezen, paper- en live-boek aansturen, slot en grenzen bewaken."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

import yaml

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BitvavoError, BitvavoPrivateClient
from .brokers import LiveBroker, PaperBroker
from .engine import BrokerError, Engine, Limits, new_book
from .lab import DAY_MS, TIMEFRAMES, Context, closed_bars, select_params
from .strategies import compute_target, ema, param_specs, sma

_LOGGER = logging.getLogger(__name__)

RESELECT_DAYS = 60
SELECTION_VERSION = 2  # ophogen = parameters opnieuw kiezen na een regelwijziging
LOCK_PAPER_DAYS = 28
OVERRIDE_PHRASE = "IK ACCEPTEER HET RISICO"
SECRET_KEY, SECRET_SECRET = "bitvavo_api_key", "bitvavo_api_secret"

LOCK_CLOSED = "op slot"
LOCK_OPEN = "open"
LOCK_OVERRIDDEN = "overbrugd"


class Trader:
    def __init__(self, hass: HomeAssistant, manager) -> None:
        self.hass = hass
        self.m = manager
        self.prices: dict[str, float] = {}
        self.plan: dict | None = None
        self.limits = Limits()
        self.trading = True
        self.live = False
        self.override = False
        self.paper = Engine(new_book(), PaperBroker(self.prices), "paper")
        self.live_engine: Engine | None = None
        self._live_book: dict | None = None
        self.signals: dict[str, str] = {}
        self.changes: dict[str, float] = {}
        self.balance_eur: float | None = None
        self._sig = ""

    # ------------------------------------------------------------------ opslag

    async def async_load(self) -> None:
        data = await self.hass.async_add_executor_job(self.m.store.load_json, "trader_state.json")
        if not data:
            return
        self.plan = data.get("plan")
        self.limits = Limits(**{**Limits().__dict__, **data.get("limits", {})})
        self.trading = data.get("trading", True)
        self.override = data.get("override", False)
        if data.get("paper"):
            self.paper.book = {**new_book(), **data["paper"]}
        self._live_book = data.get("live_book")
        if data.get("live"):
            try:
                await self._start_live()
            except HomeAssistantError as err:
                _LOGGER.warning("Live niet hervat na herstart: %s", err)

    async def async_save(self) -> None:
        data = {
            "plan": self.plan,
            "limits": self.limits.__dict__,
            "trading": self.trading,
            "live": self.live,
            "override": self.override,
            "paper": self.paper.book,
            "live_book": self.live_engine.book if self.live_engine else self._live_book,
        }
        await self.hass.async_add_executor_job(self.m.store.save_json, "trader_state.json", data)

    def _signature(self) -> str:
        parts = []
        for e in self.engines():
            b = e.book
            parts.append(
                f"{len(b['positions'])}/{len(b['orders'])}/{b['trade_count']}/{b['day']}/{b['day_paused']}/{b['last_error']}"
            )
        return "|".join(parts)

    async def _save_if_changed(self) -> None:
        sig = self._signature()
        if sig != self._sig:
            self._sig = sig
            await self.async_save()

    def engines(self) -> list[Engine]:
        return [self.paper] + ([self.live_engine] if self.live and self.live_engine else [])

    @property
    def active(self) -> Engine:
        return self.live_engine if self.live and self.live_engine else self.paper

    # -------------------------------------------------------------------- plan

    async def async_ensure_plan(self, force: bool = False) -> None:
        """Kies strategie (Lab-kandidaat, anders de beste) en per markt de parameters."""
        report = self.m.report
        if not report:
            return
        now = int(time.time() * 1000)
        plan = self.plan
        # Een onbewezen plan maakt plaats zodra het Lab wél een kandidaat heeft.
        if plan and not force and report.get("kandidaten"):
            if f"{plan['strategie']} {plan['tijdschaal']}" not in report["kandidaten"]:
                for e in self.engines():
                    await e.flatten()
                    e.book["last_bar"] = {}
                plan = None
        if plan is None or force:
            pick = None
            for row in report.get("resultaten", []):
                if row.get("geslaagd"):
                    pick = row
                    break
            if pick is None and report.get("beste"):
                strat, tf = report["beste"].split(" ")
                pick = {"strategie": strat, "tijdschaal": tf}
            if pick is None:
                return
            plan = {"strategie": pick["strategie"], "tijdschaal": pick["tijdschaal"], "params": {}, "selected_at": 0}
        key = f"{plan['strategie']} {plan['tijdschaal']}"
        plan["bewezen"] = key in report.get("kandidaten", [])
        if now - plan.get("selected_at", 0) >= RESELECT_DAYS * DAY_MS or plan.get("sel_v") != SELECTION_VERSION:
            data = await self.hass.async_add_executor_job(self.m.load_all_candles)
            plan["params"] = await self.hass.async_add_executor_job(
                select_params, data, plan["strategie"], plan["tijdschaal"]
            )
            plan["selected_at"] = now
            plan["sel_v"] = SELECTION_VERSION
        self.plan = plan
        await self.async_save()

    async def async_pick_strategy(self) -> None:
        """Opnieuw kiezen uit het laatste Lab-rapport; open posities gaan eerst dicht."""
        for e in self.engines():
            await e.flatten()
            e.book["last_bar"] = {}
        self.plan = None
        await self.async_ensure_plan(force=True)
        self.m.notify()

    # ------------------------------------------------------------------ cycli

    async def async_on_data(self) -> None:
        """Na elke data-update: nieuw afgesloten bars verwerken."""
        await self.async_ensure_plan()
        plan = self.plan
        if not plan:
            return
        strat, factor = plan["strategie"], TIMEFRAMES[plan["tijdschaal"]]
        specs = param_specs()[strat]

        def _prepare() -> dict:
            data = self.m.load_all_candles()
            ctx = Context(data)
            out = {}
            for market in self.m.markets:
                hour = data.get(market)
                if not hour:
                    continue
                bars = closed_bars(hour, factor)
                kwargs = specs.get(plan["params"].get(market) or "")
                target = (
                    compute_target(strat, kwargs, market, bars, ctx, factor * 3_600_000)
                    if kwargs and strat != "grid"
                    else None
                )
                change = hour[-1][4] / hour[-25][4] - 1 if len(hour) > 25 else None
                out[market] = (bars, kwargs, target, change)
            return out

        prepared = await self.hass.async_add_executor_job(_prepare)
        await self.async_poll_prices()
        for market, (bars, kwargs, target, change) in prepared.items():
            if change is not None:
                self.changes[market] = change
            if kwargs is None:
                self.signals[market] = "geen"
            elif strat == "grid":
                self.signals[market] = "grid"
            else:
                self.signals[market] = "long" if target and target[-1] else "vlak"
            if strat != "grid":
                for e in self.engines():
                    await e.on_bar(market, bars, strat, kwargs, self.limits, self.trading, target)
        if strat == "grid":
            items = [(m, bars, kwargs) for m, (bars, kwargs, _t, _c) in prepared.items()]
            for e in self.engines():
                await e.on_grid_bars(items, self.limits, self.trading)
        now = int(time.time() * 1000)
        for e in self.engines():
            e.record_equity(now, self.prices)
        await self.async_save()
        self.m.notify()

    async def async_poll_prices(self) -> None:
        try:
            fresh = await self.m.client.ticker_prices()
        except BitvavoError as err:
            _LOGGER.debug("Koersen ophalen mislukt: %s", err)
            return
        for market in self.m.markets:
            if market in fresh:
                self.prices[market] = fresh[market]

    async def async_minute(self) -> None:
        if not self.m.markets:
            return
        await self.async_poll_prices()
        now = int(time.time() * 1000)
        for e in self.engines():
            e.roll_day(now, self.prices, self.limits)
            if e.book["orders"]:
                await e.poll()
        await self._save_if_changed()
        self.m.notify()

    # ------------------------------------------------------------- slot & live

    def lock_status(self) -> dict:
        days = (time.time() * 1000 - self.paper.book["started"]) / DAY_MS
        checks = {
            "lab_kandidaat": bool(self.plan and self.plan.get("bewezen")),
            "paper_28_dagen": days >= LOCK_PAPER_DAYS,
            "paper_positief": self.paper.pnl(self.prices) > 0,
        }
        state = LOCK_OPEN if all(checks.values()) else LOCK_OVERRIDDEN if self.override else LOCK_CLOSED
        return {"state": state, "checks": checks, "paper_dagen": int(days), "nodig_dagen": LOCK_PAPER_DAYS}

    async def _load_keys(self) -> tuple[str, str]:
        def _read() -> dict:
            try:
                with open(self.hass.config.path("secrets.yaml"), encoding="utf-8") as fh:
                    return yaml.safe_load(fh) or {}
            except OSError:
                return {}

        secrets = await self.hass.async_add_executor_job(_read)
        key, secret = secrets.get(SECRET_KEY), secrets.get(SECRET_SECRET)
        if not key or not secret:
            raise HomeAssistantError(
                f"Geen Bitvavo-sleutel gevonden: zet {SECRET_KEY} en {SECRET_SECRET} in secrets.yaml."
            )
        return str(key), str(secret)

    async def _start_live(self) -> None:
        key, secret = await self._load_keys()
        client = BitvavoPrivateClient(async_get_clientsession(self.hass), key, secret)
        try:
            markets = await self.m.client.market_info()
            broker = LiveBroker(client, self.prices, markets)
            self.balance_eur = await broker.eur_available()
        except (BitvavoError, BrokerError) as err:
            raise HomeAssistantError(f"Bitvavo weigert de sleutel of is onbereikbaar: {err}") from err
        book = self._live_book or new_book()
        self.live_engine = Engine(book, broker, "live")
        self.live = True

    async def async_set_live(self, on: bool) -> None:
        if not on:
            self.live = False  # posities blijven staan; noodstop verkoopt ze
            if self.live_engine:
                self._live_book = self.live_engine.book
            await self.async_save()
            self.m.notify()
            return
        lock = self.lock_status()
        if lock["state"] == LOCK_CLOSED:
            missing = ", ".join(k for k, ok in lock["checks"].items() if not ok)
            raise HomeAssistantError(f"Live staat op slot. Nog niet voldaan: {missing}.")
        if not self.plan:
            raise HomeAssistantError("Er is nog geen strategie gekozen (het Lab heeft nog niet gedraaid).")
        await self._start_live()
        # nooit halverwege een beweging instappen: eerst de huidige bar markeren
        if not self.live_engine.book["last_bar"]:
            self.live_engine.book["last_bar"] = dict(self.paper.book["last_bar"])
        await self.async_save()
        self.m.notify()

    async def async_override(self, phrase: str) -> None:
        if (phrase or "").strip().upper() != OVERRIDE_PHRASE:
            raise HomeAssistantError(f'Bevestiging onjuist. Typ exact: "{OVERRIDE_PHRASE}".')
        self.override = True
        await self.async_save()
        self.m.notify()

    async def async_restore_lock(self) -> None:
        self.override = False
        if self.live and self.lock_status()["state"] == LOCK_CLOSED:
            await self.async_set_live(False)
        await self.async_save()
        self.m.notify()

    async def async_set_trading(self, on: bool) -> None:
        self.trading = on
        await self.async_save()
        self.m.notify()

    async def async_set_limit(self, key: str, value: float) -> None:
        setattr(self.limits, key, float(value))
        await self.async_save()
        self.m.notify()

    async def async_emergency_stop(self) -> None:
        """Alles annuleren, alles verkopen, live uit, handel gepauzeerd."""
        self.trading = False
        for e in [self.paper] + ([self.live_engine] if self.live_engine else []):
            await e.flatten()
        self.live = False
        if self.live_engine:
            self._live_book = self.live_engine.book
        await self.async_save()
        self.m.notify()

    # ------------------------------------------------------------- voor het scherm

    def overview(self) -> dict:
        lock = self.lock_status()
        active = self.active
        held = {p["market"] for p in active.book["positions"]}
        plan = self.plan
        return {
            "mode": "live" if self.live else "paper",
            "trading": self.trading,
            "lock": lock,
            "limits": self.limits.__dict__,
            "balance_eur": self.balance_eur,
            "plan": plan
            and {
                "strategie": plan["strategie"],
                "tijdschaal": plan["tijdschaal"],
                "bewezen": plan.get("bewezen", False),
                "params": plan.get("params", {}),
                "herkeuze": datetime.fromtimestamp(
                    (plan.get("selected_at", 0) + RESELECT_DAYS * DAY_MS) / 1000, tz=timezone.utc
                ).isoformat(),
            },
            "book": active.summary(self.prices),
            "paper": self.paper.summary(self.prices),
            "orders": [
                {
                    "markt": o["market"],
                    "soort": "Kooplimiet" if o["side"] == "buy" else "Verkooplimiet",
                    "niveau": o.get("level"),
                    "limiet": o["price"],
                    "koers_nu": self.prices.get(o["market"]),
                    "afstand_pct": round(100 * (o["price"] / self.prices[o["market"]] - 1), 2)
                    if self.prices.get(o["market"])
                    else None,
                    "bedrag": round(o["price"] * o["amount"], 2),
                }
                for o in active.book["orders"]
            ],
            "markets": [
                {
                    "market": m,
                    "price": self.prices.get(m),
                    "change": self.changes.get(m),
                    "signal": self.signals.get(m, "–"),
                    "held": m in held,
                }
                for m in self.m.markets
            ],
        }

    def chart(self, market: str, timeframe: str, count: int = 600) -> dict:
        """Candles, hulplijnen en koop/verkoopmomenten voor de grafiek (in executor)."""
        hour = self.m.store.load_candles(market)
        bars = closed_bars(hour, TIMEFRAMES.get(timeframe, 1))
        closes = [b[4] for b in bars]
        lines = []
        plan = self.plan
        if plan and plan["tijdschaal"] == timeframe:
            kw = param_specs()[plan["strategie"]].get(plan["params"].get(market) or "")
            if kw and plan["strategie"] == "ema_cross":
                lines = [(f"EMA {kw['fast']}", ema(closes, kw["fast"])), (f"EMA {kw['slow']}", ema(closes, kw["slow"]))]
            elif kw and plan["strategie"] == "rsi_dip":
                lines = [(f"SMA {kw['trend_n']}", sma(closes, kw["trend_n"]))]
            elif kw and plan["strategie"] == "grid":
                lines = [(f"SMA {kw['ref_n']}", sma(closes, kw["ref_n"]))]
            elif kw and plan["strategie"] in ("donchian", "donchian_regime"):
                highs = [b[2] for b in bars]
                n = kw["entry_n"]
                lines = [(f"Hoogste {n}", [max(highs[i - n : i]) if i >= n else None for i in range(len(bars))])]
        book = self.active.book
        marks = [
            {"t": t["in"], "side": "buy"} for t in book["trades"] if t["markt"] == market
        ] + [{"t": t["uit"], "side": "sell"} for t in book["trades"] if t["markt"] == market]
        marks += [{"t": p["entry_ts"], "side": "buy"} for p in book["positions"] if p["market"] == market]
        return {
            "bars": [list(b) for b in bars[-count:]],
            "lines": [{"name": n, "values": v[-count:]} for n, v in lines],
            "marks": marks,
        }
