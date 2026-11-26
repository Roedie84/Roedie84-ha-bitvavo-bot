"""Handel: plan kiezen, paper- en live-boek aansturen, slot en grenzen bewaken."""

from __future__ import annotations

import asyncio
import logging
from bisect import bisect_right
import time
from datetime import datetime, timezone

import yaml
from typing import Callable

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BitvavoError, BitvavoPrivateClient
from .brokers import LiveBroker, PaperBroker
from .engine import BrokerError, Engine, Limits, new_book
from .lab import DAY_MS, TIMEFRAMES, Context, closed_bars, recent_performance, resample, select_params
from .strategies import Costs, compute_target, ema, param_specs, sma
from .stream import BitvavoStream

_LOGGER = logging.getLogger(__name__)

RESELECT_DAYS = 60
HEALTH_DAYS = 365  # houdbaarheid: out-of-sample over het laatste jaar
HEALTH_REFRESH_DAYS = 30
HEALTH_MIN_TRADES = 20
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
        self.stream = BitvavoStream(async_get_clientsession(hass), lambda: manager.markets, self.prices)
        self.paper = Engine(new_book(), PaperBroker(self.prices, extremes=self.stream.extremes, quotes=self.stream.quotes), "paper")
        self._listeners: set = set()
        # Eén slot voor alles wat orders of posities aanraakt: twee cycli tegelijk
        # (uurcyclus, minuutcontrole, handmatige service) mogen nooit dubbel handelen.
        self._lock = asyncio.Lock()
        self.resume_error: str | None = None
        self.health: dict | None = None  # recente prestaties van het gekozen plan
        self.fees: dict | None = None  # echte kosten van het Bitvavo-account
        self.bench: dict[str, float] = {}  # koers per markt bij de start van paper
        self._seen: dict[str, dict] = {}
        self._sent: dict[str, float] = {}
        self._pushed = -1
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
        self.health = data.get("health")
        self.fees = data.get("fees")
        self._apply_fees()
        if data.get("paper"):
            self.paper.book = {**new_book(), **data["paper"]}
        self._live_book = data.get("live_book")
        if data.get("live"):
            try:
                await self._start_live()
            except HomeAssistantError as err:
                _LOGGER.warning("Live niet hervat na herstart: %s", err)
                self.resume_error = str(err)

    async def async_save(self) -> None:
        data = {
            "plan": self.plan,
            "limits": self.limits.__dict__,
            "trading": self.trading,
            "live": self.live,
            "override": self.override,
            "health": self.health,
            "fees": self.fees,
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
        async with self._lock:
            for e in self.engines():
                await e.flatten()
                e.book["last_bar"] = {}
            self.plan = None
            await self.async_ensure_plan(force=True)
        self.m.notify()

    # ------------------------------------------------------------------ cycli

    async def async_on_data(self) -> None:
        """Na elke data-update: nieuw afgesloten bars verwerken."""
        async with self._lock:
            await self._on_data()

    async def _on_data(self) -> None:
        await self.async_ensure_plan()
        plan = self.plan
        if not plan:
            return
        for job in (self._refresh_health, self._refresh_fees):
            try:  # bijzaak: mag de handelscyclus nooit tegenhouden
                await job()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("%s mislukt", job.__name__)
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
                # koers bij de start van paper, voor de vergelijking met 'niets doen'
                i = bisect_right(hour, self.paper.book["started"], key=lambda r: r[0]) - 1
                if i >= 0:
                    self.bench[market] = hour[i][4]
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
        if self.live and self.live_engine:
            try:
                await self._reconcile_live()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Saldo-afstemming mislukt")
        now = int(time.time() * 1000)
        for e in self.engines():
            e.record_equity(now, self.prices)
        self.stream.reset_extremes()  # nieuwe orders zien alleen koersen van ná hun plaatsing
        await self._check_events()
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
        if not self.stream.fresh:
            await self.async_poll_prices()  # terugval: de koersstroom ligt eruit
        now = int(time.time() * 1000)
        async with self._lock:
            for e in self.engines():
                e.roll_day(now, self.prices, self.limits)
                if e.book["orders"]:
                    await e.poll()
            self.stream.reset_extremes()
            await self._check_events()
            await self._save_if_changed()
        self.m.notify()

    async def async_fast_tick(self) -> None:
        """Elke paar seconden: paper-limietorders toetsen aan de koersstroom."""
        if not self.stream.fresh or not self.paper.book["orders"] or self._lock.locked():
            return
        async with self._lock:
            before = self._signature()
            await self.paper.poll()
            self.stream.reset_extremes()
            changed = self._signature() != before
            if changed:
                await self._check_events()
                await self._save_if_changed()
        if changed:
            self.m.notify()

    # --------------------------------------------------- houdbaarheid & kosten

    def _plan_key(self) -> str | None:
        return f"{self.plan['strategie']} {self.plan['tijdschaal']}" if self.plan else None

    async def _refresh_health(self) -> None:
        """Eens per maand: deed het gekozen plan het ook het laatste jaar goed?"""
        key, now = self._plan_key(), time.time() * 1000
        h = self.health
        if h and h.get("plan") == key and now - h.get("checked_at", 0) < HEALTH_REFRESH_DAYS * DAY_MS:
            return
        data = await self.hass.async_add_executor_job(self.m.load_all_candles)
        result = await self.hass.async_add_executor_job(
            recent_performance, data, self.plan["strategie"], self.plan["tijdschaal"], HEALTH_DAYS
        )
        self.health = {**result, "plan": key, "checked_at": int(now)}

    def health_ok(self) -> bool:
        h = self.health
        return bool(
            h
            and h.get("plan") == self._plan_key()
            and (h.get("trades") or 0) >= HEALTH_MIN_TRADES
            and (h.get("netto_pct") or 0) > 0
        )

    def _apply_fees(self) -> None:
        f = self.fees
        costs = Costs(taker=f["taker"], maker=f["maker"]) if f else Costs()
        self.paper.broker.set_costs(costs)
        for e in [self.paper] + ([self.live_engine] if self.live_engine else []):
            e.exit_cost = costs.taker + costs.slippage
            if e is not self.paper:
                e.broker._costs = costs

    async def _refresh_fees(self) -> None:
        """Eens per dag de echte kosten van het account ophalen (alleen-lezen)."""
        if self.fees and time.time() * 1000 - self.fees.get("checked_at", 0) < DAY_MS:
            return
        try:
            key, secret = await self._load_keys()
            client = BitvavoPrivateClient(async_get_clientsession(self.hass), key, secret)
            data = await client.request("GET", "/account/fees")
            self.fees = {
                "taker": float(data["taker"]),
                "maker": float(data["maker"]),
                "tier": data.get("tier"),
                "checked_at": int(time.time() * 1000),
            }
        except Exception as err:  # noqa: BLE001 - zonder sleutel of verbinding blijft de aanname staan
            _LOGGER.debug("Kosten niet opgehaald, aanname blijft staan: %s", err)
            if not self.fees:
                return
        self._apply_fees()

    # ------------------------------------------------------- saldo-afstemming

    async def _reconcile_live(self) -> None:
        """Klopt het boek nog met wat er echt op Bitvavo staat?"""
        engine = self.live_engine
        if not engine.book["positions"]:
            return
        try:
            holdings = await engine.broker.holdings()
        except BrokerError as err:
            engine.book["last_error"] = f"saldo ophalen: {err}"
            return
        per_market: dict[str, float] = {}
        for p in engine.book["positions"]:
            per_market[p["market"]] = per_market.get(p["market"], 0.0) + p["amount"]
        for market, expected in per_market.items():
            have = holdings.get(market.split("-")[0], 0.0)
            if have < expected * 0.02:
                for p in [x for x in engine.book["positions"] if x["market"] == market]:
                    engine.drop_position(p, "buiten de bot om verkocht")
                await self.async_notify(
                    "Bitvavo Bot – positie verdwenen",
                    f"{market} staat niet meer op je Bitvavo-account. De positie is uit het boek gehaald; "
                    "de inzet is als verlies geboekt omdat de opbrengst onbekend is.",
                    key=f"gone-{market}",
                )
            elif have < expected * 0.98:
                await self.async_notify(
                    "Bitvavo Bot – saldo wijkt af",
                    f"{market}: de bot rekent op {expected:.6g}, op Bitvavo staat {have:.6g}. "
                    "Verkopen gebeuren met wat er werkelijk staat.",
                    key=f"diff-{market}",
                )

    # ---------------------------------------------------------------- meldingen

    async def async_notify(self, title: str, message: str, key: str | None = None, important: bool = True) -> None:
        """Naar de telefoon als die is ingesteld; belangrijke zaken anders als HA-melding."""
        if key:
            if time.time() - self._sent.get(key, 0) < 6 * 3600:
                return
            self._sent[key] = time.time()
        target = (self.m.entry.options.get("notify_service") or "").strip()
        if target and "." in target:
            domain, service = target.split(".", 1)
            try:
                await self.hass.services.async_call(domain, service, {"title": title, "message": message}, blocking=False)
                return
            except Exception as err:  # noqa: BLE001 - een melding mag de handel nooit raken
                _LOGGER.warning("Melding via %s mislukt: %s", target, err)
        if important:
            persistent_notification.async_create(self.hass, message, title=title)

    async def _check_events(self) -> None:
        for name, engine in (("paper", self.paper), ("live", self.live_engine)):
            if engine is None:
                continue
            b = engine.book
            cur = {"dagstop": b["day_paused"], "fout": b["last_error"], "trades": b["trade_count"], "pos": len(b["positions"])}
            prev = self._seen.get(name)
            self._seen[name] = cur
            if prev is None:
                continue
            if cur["dagstop"] and not prev["dagstop"]:
                await self.async_notify(
                    f"Bitvavo Bot – dagstop ({name})",
                    f"Het dagverlies van € {self.limits.max_day_loss:.0f} is bereikt. Vandaag geen nieuwe aankopen meer.",
                )
            if cur["fout"] and cur["fout"] != prev["fout"]:
                await self.async_notify(f"Bitvavo Bot – fout ({name})", cur["fout"], key=f"fout-{name}-{cur['fout'][12:60]}")
            if name == "live":
                if cur["trades"] > prev["trades"] and b["trades"]:
                    t = b["trades"][-1]
                    await self.async_notify(
                        "Bitvavo Bot – verkocht (live)",
                        f"{t['markt']}: resultaat € {t['resultaat']:+.2f} ({t['pct']:+.2f}%) · {t['reden']}",
                        important=False,
                    )
                elif cur["pos"] > prev["pos"] and b["positions"]:
                    p = b["positions"][-1]
                    await self.async_notify(
                        "Bitvavo Bot – gekocht (live)", f"{p['market']}: € {p['cost']:.2f} op {p['entry_px']:.6g}", important=False
                    )

    def _trades_today(self, engine: Engine) -> int:
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000
        return sum(1 for t in engine.book["trades"] if t["uit"] >= start)

    async def async_daily_summary(self) -> None:
        """Dagoverzicht (18:00), alleen als er een telefoon is ingesteld."""
        opts = self.m.entry.options
        if not opts.get("notify_service") or not opts.get("daily_summary", True) or not self.plan:
            return
        lines = []
        for name, engine in (("Paper", self.paper), ("Live", self.live_engine if self.live else None)):
            if engine is None:
                continue
            sm = engine.summary(self.prices, True)
            lines.append(
                f"{name}: € {sm['resultaat']:+.2f} (vandaag € {sm['vandaag']:+.2f}) · "
                f"{self._trades_today(engine)} trades vandaag, {sm['open_posities']} open, € {sm['ingezet']:.0f} ingezet"
            )
        lock = self.lock_status()
        lines.append(f"Plan: {self._plan_key()} · slot: {lock['state']} (paper dag {lock['paper_dagen']} van {lock['nodig_dagen']})")
        await self.async_notify("Bitvavo Bot – dagoverzicht", "\n".join(lines), important=False)

    def all_trades(self, book: str) -> list[dict]:
        engine = self.paper if book == "paper" else self.live_engine
        return list(engine.book["trades"]) if engine else []

    # ------------------------------------------------------- live naar het scherm

    def stream_subscribe(self, listener) -> Callable[[], None]:
        self._listeners.add(listener)
        self._pushed = -1
        return lambda: self._listeners.discard(listener)

    def push_live(self) -> None:
        """Stuur de actuele stand naar open schermen (alleen als er iets veranderde)."""
        if not self._listeners:
            return
        marker = (self.stream.version, self._signature(), self.stream.fresh)
        if marker == self._pushed:
            return
        self._pushed = marker
        payload = {
            "live": self.overview(light=True),
            "candles": self.stream.forming,
            "stream": self.stream.fresh,
        }
        for listener in list(self._listeners):
            listener(payload)

    # ------------------------------------------------------------- slot & live

    def lock_status(self) -> dict:
        days = (time.time() * 1000 - self.paper.book["started"]) / DAY_MS
        checks = {
            "lab_kandidaat": bool(self.plan and self.plan.get("bewezen")),
            "paper_28_dagen": days >= LOCK_PAPER_DAYS,
            "paper_positief": self.paper.pnl(self.prices) > 0,
            "recent_positief": self.health_ok(),
        }
        state = LOCK_OPEN if all(checks.values()) else LOCK_OVERRIDDEN if self.override else LOCK_CLOSED
        return {
            "state": state,
            "checks": checks,
            "paper_dagen": int(days),
            "nodig_dagen": LOCK_PAPER_DAYS,
            "houdbaarheid": self.health,
        }

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
        async with self._lock:
            await self._start_live()
            # nooit halverwege een beweging instappen: eerst de huidige bar markeren
            if not self.live_engine.book["last_bar"]:
                self.live_engine.book["last_bar"] = dict(self.paper.book["last_bar"])
            self.resume_error = None
            self._apply_fees()
            await self.async_save()
        await self.async_notify(
            "Bitvavo Bot – LIVE aan",
            f"Er wordt nu met echt geld gehandeld: max € {self.limits.max_per_buy:.0f} per koop, "
            f"€ {self.limits.max_total:.0f} totaal. Saldo: € {self.balance_eur or 0:.2f}.",
        )
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
        self.trading = False  # direct, ook als er net een cyclus loopt
        async with self._lock:
            for e in [self.paper] + ([self.live_engine] if self.live_engine else []):
                await e.flatten()
            self.live = False
            if self.live_engine:
                self._live_book = self.live_engine.book
            await self.async_save()
        await self.async_notify(
            "Bitvavo Bot – noodstop", "Alle orders zijn geannuleerd en alle posities verkocht. Live is uit, de handel is gepauzeerd."
        )
        self.m.notify()

    # ------------------------------------------------------------- voor het scherm

    def overview(self, light: bool = False) -> dict:
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
            "resume_error": self.resume_error,
            "fees": self.fees,
            "markt_sinds_start_pct": self._basket_pct(),
            "notify": bool(self.m.entry.options.get("notify_service")),
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
            "book": active.summary(self.prices, light),
            "paper": self.paper.summary(self.prices, True),
            "stream": self.stream.fresh,
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
                    "spread_pct": self._spread_pct(m),
                }
                for m in self.m.markets
            ],
        }

    def _basket_pct(self) -> float | None:
        """Wat een gelijk verdeelde korf van alle markten deed sinds de start van paper."""
        moves = [self.prices[m] / px - 1 for m, px in self.bench.items() if px and self.prices.get(m)]
        return round(100 * sum(moves) / len(moves), 2) if moves else None

    def _spread_pct(self, market: str) -> float | None:
        q = self.stream.quotes.get(market)
        if not q or q.get("bid", 0) <= 0 or time.time() - q.get("t", 0) > 120:
            return None
        return round(100 * (q["ask"] / q["bid"] - 1), 3)

    def chart(self, market: str, timeframe: str, count: int = 600) -> dict:
        """Candles, hulplijnen en koop/verkoopmomenten voor de grafiek (in executor)."""
        hour = self.m.store.load_candles(market)
        bars = resample(hour, TIMEFRAMES.get(timeframe, 1))  # laatste periode mag nog onvolledig zijn
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
            "last_hour": int(hour[-1][0]) if hour else 0,
            "tf_ms": TIMEFRAMES.get(timeframe, 1) * 3_600_000,
        }
