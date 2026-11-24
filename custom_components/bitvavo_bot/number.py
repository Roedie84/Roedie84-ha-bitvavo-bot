"""Risicogrenzen."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import CURRENCY_EURO

from .entity import BotEntity

LIMITS = (
    ("max_per_koop", "max_per_buy", "Max per koop", 10, 1000, 5, "mdi:cash"),
    ("max_totaal", "max_total", "Max totaal ingezet", 25, 10000, 25, "mdi:cash-multiple"),
    ("max_dagverlies", "max_day_loss", "Max dagverlies", 5, 1000, 5, "mdi:shield-alert"),
)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([LimitNumber(entry.runtime_data, *row) for row in LIMITS])


class LimitNumber(BotEntity, NumberEntity):
    _platform = "number"
    _attr_native_unit_of_measurement = CURRENCY_EURO
    _attr_mode = NumberMode.BOX

    def __init__(self, manager, key, field, name, lo, hi, step, icon) -> None:
        self._key = key
        super().__init__(manager)
        self._field = field
        self._attr_name = name
        self._attr_native_min_value = lo
        self._attr_native_max_value = hi
        self._attr_native_step = step
        self._attr_icon = icon

    @property
    def native_value(self) -> float:
        return getattr(self._m.trader.limits, self._field)

    async def async_set_native_value(self, value: float) -> None:
        await self._m.trader.async_set_limit(self._field, value)
