"""Regie: data ophalen, Lab draaien, status bijhouden."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .api import BitvavoError, BitvavoPublicClient
from .const import (
    CANDLE_MS,
    CONF_HISTORY_DAYS,
    CONF_MARKET_COUNT,
    CONF_MARKETS,
    DATA_DIR,
    DEFAULT_HISTORY_DAYS,
    DEFAULT_MARKET_COUNT,
    DOMAIN,
    SIGNAL_UPDATE,
    STATUS_DOWNLOADING,
    STATUS_ERROR,
    STATUS_IDLE,
    VERDICT_NONE,
    VERDICT_RUNNING,
)
from .lab import run_lab
from .store import Store

_LOGGER = logging.getLogger(__name__)


def _iso(ms: int | None) -> str | None:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


class BitvavoBotManager:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.client = BitvavoPublicClient(async_get_clientsession(hass))
        self.store = Store(hass.config.path(DATA_DIR))
        self.status = STATUS_IDLE
        self.progress = ""
        self.error: str | None = None
        self.markets: list[str] = list(entry.data.get(CONF_MARKETS, []))
        self.coverage: dict[str, dict] = {}
        self.report: dict | None = None
        self.lab_running = False
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------ eigenschappen

    @property
    def total_candles(self) -> int:
        return sum(c["candles"] for c in self.coverage.values())

    @property
    def verdict(self) -> str:
        if self.lab_running:
            return VERDICT_RUNNING
        if not self.report:
            return VERDICT_NONE
        return self.report.get("oordeel", VERDICT_NONE)

    def _notify(self) -> None:
        async_dispatcher_send(self.hass, f"{SIGNAL_UPDATE}_{self.entry.entry_id}")

    # ------------------------------------------------------------------ opstart

    async def async_start(self) -> None:
        """Laad wat er al is; haal ontbrekende data op en draai zo nodig het Lab."""
        self.report = await self.hass.async_add_executor_job(self.store.load_report)
        await self._refresh_coverage()
        self._notify()
        await self.async_update_data()
        if self.report is None and self.status == STATUS_IDLE and self.total_candles:
            await self.async_run_lab()

    async def _refresh_coverage(self) -> None:
        def _scan() -> dict[str, dict]:
            out = {}
            for market in self.markets:
                rows = self.store.load_candles(market)
                if rows:
                    out[market] = {
                        "candles": len(rows),
                        "van": _iso(int(rows[0][0])),
                        "tot": _iso(int(rows[-1][0])),
                    }
            return out

        self.coverage = await self.hass.async_add_executor_job(_scan)

    # --------------------------------------------------------------------- data

    async def async_update_data(self) -> None:
        async with self._lock:
            self.status, self.error = STATUS_DOWNLOADING, None
            self._notify()
            try:
                if not self.markets:
                    count = self.entry.options.get(
                        CONF_MARKET_COUNT, self.entry.data.get(CONF_MARKET_COUNT, DEFAULT_MARKET_COUNT)
                    )
                    self.markets = await self.client.top_eur_markets(int(count))
                    # Het universum ligt vanaf nu vast: anders verschuift de toets mee met de waan van de dag.
                    self.hass.config_entries.async_update_entry(
                        self.entry, data={**self.entry.data, CONF_MARKETS: self.markets}
                    )
                days = int(self.entry.data.get(CONF_HISTORY_DAYS, DEFAULT_HISTORY_DAYS))
                now_ms = int(time.time() * 1000)
                floor_ms = now_ms - days * 86_400_000
                for idx, market in enumerate(self.markets, 1):
                    self.progress = f"{market} ({idx}/{len(self.markets)})"
                    self._notify()
                    existing = await self.hass.async_add_executor_job(self.store.load_candles, market)
                    since = int(existing[-1][0]) + CANDLE_MS if existing else floor_ms
                    if since + CANDLE_MS > now_ms:
                        continue
                    rows = await self.client.candles(market, since, now_ms)
                    if rows:
                        await self.hass.async_add_executor_job(self.store.merge_candles, market, rows)
                self.status = STATUS_IDLE
            except BitvavoError as err:
                _LOGGER.warning("Bitvavo-data ophalen mislukt: %s", err)
                self.status, self.error = STATUS_ERROR, str(err)
            finally:
                self.progress = ""
                await self._refresh_coverage()
                self._notify()

    # ---------------------------------------------------------------------- lab

    async def async_run_lab(self) -> None:
        if self.lab_running:
            return
        self.lab_running = True
        self._notify()
        try:

            def _work() -> dict:
                data = {
                    m: [tuple(r) for r in self.store.load_candles(m)] for m in self.markets
                }
                report = run_lab(data)
                report["gedraaid_op"] = datetime.now(timezone.utc).isoformat()
                report["oos_van"] = _iso(report.get("oos_van"))
                report["oos_tot"] = _iso(report.get("oos_tot"))
                self.store.save_report(report)
                return report

            self.report = await self.hass.async_add_executor_job(_work)
            self._announce()
        except Exception:  # noqa: BLE001 - het Lab mag HA nooit onderuit halen
            _LOGGER.exception("Lab-run mislukt")
        finally:
            self.lab_running = False
            self._notify()

    def _announce(self) -> None:
        r = self.report or {}
        if r.get("kandidaten"):
            body = "Kandidaat/kandidaten door de toets: **" + ", ".join(r["kandidaten"]) + "**."
        else:
            body = (
                "Geen enkele strategie haalt de vooraf vastgelegde drempel. "
                f"Beste: {r.get('beste')} met {r.get('beste_netto_pct')}% netto per trade."
            )
        body += f"\n\nKopen-en-vasthouden over dezelfde periode: {r.get('buy_hold_pct')}%."
        persistent_notification.async_create(
            self.hass, body, title="Bitvavo Bot – Lab-uitslag", notification_id=f"{DOMAIN}_lab"
        )
