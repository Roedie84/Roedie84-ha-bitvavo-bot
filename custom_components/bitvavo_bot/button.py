"""Noodstop."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity

from .entity import BotEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([EmergencyStop(entry.runtime_data)])


class EmergencyStop(BotEntity, ButtonEntity):
    _key = "noodstop"
    _platform = "button"
    _attr_name = "Noodstop"
    _attr_icon = "mdi:alert-octagon"

    async def async_press(self) -> None:
        await self._m.trader.async_emergency_stop()
