"""Sensoren: data, Lab, strategie, slot en resultaat."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CURRENCY_EURO, PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import BotEntity
from .manager import BitvavoBotManager


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    manager: BitvavoBotManager = entry.runtime_data
    async_add_entities(
        [
            DataSensor(manager),
            VerdictSensor(manager),
            BestNetSensor(manager),
            StrategySensor(manager),
            LockSensor(manager),
            ResultSensor(manager, "paper"),
            ResultSensor(manager, "live"),
        ]
    )


class _Base(BotEntity, SensorEntity):
    pass


class DataSensor(_Base):
    _key = "data"
    _attr_name = "Data"
    _attr_icon = "mdi:database-clock"

    @property
    def native_value(self) -> str:
        return self._m.status

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "voortgang": self._m.progress,
            "fout": self._m.error,
            "candles": self._m.total_candles,
            "markten": self._m.markets,
            "dekking": self._m.coverage,
        }


class VerdictSensor(_Base):
    _key = "lab_oordeel"
    _attr_name = "Lab-oordeel"
    _attr_icon = "mdi:flask"

    @property
    def native_value(self) -> str:
        return self._m.verdict

    @property
    def extra_state_attributes(self) -> dict:
        r = self._m.report or {}
        rows = [
            {k: v for k, v in row.items() if k not in ("checks", "gekozen")}
            | {"gezakt_op": [k for k, ok in row["checks"].items() if not ok]}
            for row in r.get("resultaten", [])
        ]
        return {
            "gedraaid_op": r.get("gedraaid_op"),
            "preregistratie": r.get("preregistratie"),
            "kandidaten": r.get("kandidaten", []),
            "beste": r.get("beste"),
            "buy_hold_pct": r.get("buy_hold_pct"),
            "oos_van": r.get("oos_van"),
            "oos_tot": r.get("oos_tot"),
            "hypotheses": r.get("hypotheses"),
            "alpha_gecorrigeerd": r.get("alpha_gecorrigeerd"),
            "kosten": r.get("kosten"),
            "resultaten": rows,
        }


class BestNetSensor(_Base):
    _key = "beste_netto_per_trade"
    _attr_name = "Beste netto per trade (OOS)"
    _attr_icon = "mdi:percent"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 3

    @property
    def native_value(self) -> float | None:
        return (self._m.report or {}).get("beste_netto_pct")


class StrategySensor(_Base):
    _key = "strategie"
    _attr_name = "Strategie"
    _attr_icon = "mdi:chart-timeline-variant"

    @property
    def native_value(self) -> str:
        p = self._m.trader.plan
        return f"{p['strategie']} {p['tijdschaal']}" if p else "nog niet gekozen"

    @property
    def extra_state_attributes(self) -> dict:
        p = self._m.trader.plan or {}
        return {"bewezen": p.get("bewezen", False), "parameters": p.get("params", {})}


class LockSensor(_Base):
    _key = "live_slot"
    _attr_name = "Live-slot"
    _attr_icon = "mdi:lock"

    @property
    def native_value(self) -> str:
        return self._m.trader.lock_status()["state"]

    @property
    def extra_state_attributes(self) -> dict:
        s = self._m.trader.lock_status()
        return {
            **s["checks"],
            "paper_dagen": s["paper_dagen"],
            "nodig_dagen": s["nodig_dagen"],
            "houdbaarheid": s["houdbaarheid"],
        }


class ResultSensor(_Base):
    _attr_native_unit_of_measurement = CURRENCY_EURO
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:cash-register"

    def __init__(self, manager, book: str) -> None:
        self._key = f"{book}_resultaat"
        self._book = book
        self._attr_name = "Resultaat paper" if book == "paper" else "Resultaat live"
        super().__init__(manager)

    def _engine(self):
        t = self._m.trader
        return t.paper if self._book == "paper" else t.live_engine

    @property
    def native_value(self) -> float | None:
        e = self._engine()
        return round(e.pnl(self._m.trader.prices), 2) if e else None

    @property
    def extra_state_attributes(self) -> dict:
        e = self._engine()
        if not e:
            return {}
        s = e.summary(self._m.trader.prices)
        return {k: s[k] for k in ("vandaag", "ingezet", "trades", "winst_pct_trades", "open_posities", "open_orders", "dagstop", "laatste_fout")}
