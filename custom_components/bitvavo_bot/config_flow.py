"""Config flow voor Bitvavo Bot."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_HISTORY_DAYS,
    CONF_MARKET_COUNT,
    DEFAULT_HISTORY_DAYS,
    DEFAULT_MARKET_COUNT,
    DOMAIN,
)


class BitvavoBotConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return BitvavoBotOptionsFlow()

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


class BitvavoBotOptionsFlow(OptionsFlow):
    """Instellingen: waar meldingen heen gaan."""

    async def async_step_init(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = self.config_entry.options
        services = sorted(f"notify.{name}" for name in self.hass.services.async_services().get("notify", {}))
        schema = vol.Schema(
            {
                vol.Optional(
                    "notify_service", description={"suggested_value": current.get("notify_service", "")}
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=services, custom_value=True, mode=selector.SelectSelectorMode.DROPDOWN
                    )
                ),
                vol.Required("daily_summary", default=current.get("daily_summary", True)): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
