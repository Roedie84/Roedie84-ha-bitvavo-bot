"""Config flow voor Bitvavo Bot."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import (
    CONF_HISTORY_DAYS,
    CONF_MARKET_COUNT,
    DEFAULT_HISTORY_DAYS,
    DEFAULT_MARKET_COUNT,
    DOMAIN,
)


class BitvavoBotConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(title="Bitvavo Bot", data=user_input)
        schema = vol.Schema(
            {
                vol.Required(CONF_MARKET_COUNT, default=DEFAULT_MARKET_COUNT): vol.All(
                    vol.Coerce(int), vol.Range(min=3, max=25)
                ),
                vol.Required(CONF_HISTORY_DAYS, default=DEFAULT_HISTORY_DAYS): vol.All(
                    vol.Coerce(int), vol.Range(min=365, max=2190)
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)
