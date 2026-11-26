"""Gedeelde basis voor alle entiteiten."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, SIGNAL_UPDATE, VERSION


class BotEntity(Entity):
    _attr_should_poll = False
    _attr_has_entity_name = True
    _key = ""
    _platform = "sensor"

    def __init__(self, manager) -> None:
        self._m = manager
        self._attr_unique_id = f"{manager.entry.entry_id}_{self._key}"
        self.entity_id = f"{self._platform}.{DOMAIN}_{self._key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, manager.entry.entry_id)},
            name="Bitvavo Bot",
            manufacturer="Roedie84",
            model="Handelsbot",
            sw_version=VERSION,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def available(self) -> bool:
        # tot de opgeslagen stand is ingelezen is er niets zinnigs te tonen (geen valse nullen)
        return self._m.ready

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, f"{SIGNAL_UPDATE}_{self._m.entry.entry_id}", self._refresh)
        )

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()
