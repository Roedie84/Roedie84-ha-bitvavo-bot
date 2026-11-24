"""Sensoren: datastatus en Lab-uitslag."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_UPDATE, VERSION
from .manager import BitvavoBotManager


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    manager: BitvavoBotManager = entry.runtime_data
    async_add_entities([DataSensor(manager), VerdictSensor(manager), BestNetSensor(manager)])


class _Base(SensorEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True
    _key = ""

    def __init__(self, manager: BitvavoBotManager) -> None:
        self._m = manager
        self._attr_unique_id = f"{manager.entry.entry_id}_{self._key}"
        self.entity_id = f"sensor.{DOMAIN}_{self._key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, manager.entry.entry_id)},
            name="Bitvavo Bot",
            manufacturer="Roedie84",
            model="Lab (geen orders)",
            sw_version=VERSION,
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, f"{SIGNAL_UPDATE}_{self._m.entry.entry_id}", self._refresh
            )
        )

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()


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
