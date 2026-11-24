"""Schakelaars: live (echt geld) en handel actief."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity

from .entity import BotEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    m = entry.runtime_data
    async_add_entities([LiveSwitch(m), TradingSwitch(m)])


class LiveSwitch(BotEntity, SwitchEntity):
    _key = "live"
    _platform = "switch"
    _attr_name = "Live (echt geld)"
    _attr_icon = "mdi:cash-lock"

    @property
    def is_on(self) -> bool:
        return self._m.trader.live

    async def async_turn_on(self, **kwargs) -> None:
        await self._m.trader.async_set_live(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._m.trader.async_set_live(False)


class TradingSwitch(BotEntity, SwitchEntity):
    _key = "handel_actief"
    _platform = "switch"
    _attr_name = "Handel actief"
    _attr_icon = "mdi:play-pause"

    @property
    def is_on(self) -> bool:
        return self._m.trader.trading

    async def async_turn_on(self, **kwargs) -> None:
        await self._m.trader.async_set_trading(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._m.trader.async_set_trading(False)
