"""Bitvavo Bot – data, Lab en handel (paper standaard; live achter een slot)."""

from __future__ import annotations

from pathlib import Path

import voluptuous as vol

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from datetime import timedelta

from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.event import async_track_time_change, async_track_time_interval

from .const import DOMAIN, SERVICE_RUN_LAB, SERVICE_UPDATE_DATA, VERSION
from .manager import BitvavoBotManager
from .websocket import async_register_websocket

PLATFORMS = [Platform.SENSOR, Platform.SWITCH, Platform.NUMBER, Platform.BUTTON]
CARD_URL = f"/{DOMAIN}/bitvavo-bot-card.js"
SERVICES = (SERVICE_UPDATE_DATA, SERVICE_RUN_LAB, "override_lock", "restore_lock", "kies_strategie")


async def _async_register_frontend(hass: HomeAssistant) -> None:
    if hass.data.get(f"{DOMAIN}_frontend"):
        return
    hass.data[f"{DOMAIN}_frontend"] = True
    card = Path(__file__).parent / "frontend" / "bitvavo-bot-card.js"
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(card), False)])
    if "frontend" in hass.config.components:
        add_extra_js_url(hass, f"{CARD_URL}?v={VERSION}")
    async_register_websocket(hass)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    manager = BitvavoBotManager(hass, entry)
    entry.runtime_data = manager
    await hass.async_add_executor_job(manager.store.ensure)
    await _async_register_frontend(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Eerste download duurt minuten: op de achtergrond, HA start gewoon door.
    entry.async_create_background_task(hass, manager.async_start(), f"{DOMAIN}_start")

    async def _hourly(_now) -> None:
        await manager.async_hourly()

    async def _minute(_now) -> None:
        await manager.trader.async_minute()

    # elk uur vlak na het sluiten van de uurcandle; elke minuut koersen en orders
    entry.async_on_unload(async_track_time_change(hass, _hourly, minute=1, second=15))
    entry.async_on_unload(async_track_time_change(hass, _minute, second=40))

    # directe koersstroom: per seconde naar het scherm, elke 5 s paper-orders toetsen
    entry.async_create_background_task(hass, manager.trader.stream.run(), f"{DOMAIN}_stream")

    async def _fast(_now) -> None:
        await manager.trader.async_fast_tick()

    def _push(_now) -> None:
        manager.trader.push_live()

    entry.async_on_unload(async_track_time_interval(hass, _fast, timedelta(seconds=5)))
    entry.async_on_unload(async_track_time_interval(hass, _push, timedelta(seconds=1)))

    async def _svc_update(_call: ServiceCall) -> None:
        await manager.async_hourly()

    async def _svc_lab(_call: ServiceCall) -> None:
        await manager.async_run_lab()
        await manager.async_trade_cycle()

    async def _svc_override(call: ServiceCall) -> None:
        await manager.trader.async_override(call.data.get("bevestiging", ""))

    async def _svc_restore(_call: ServiceCall) -> None:
        await manager.trader.async_restore_lock()

    async def _svc_pick(_call: ServiceCall) -> None:
        await manager.trader.async_pick_strategy()

    hass.services.async_register(DOMAIN, SERVICE_UPDATE_DATA, _svc_update)
    hass.services.async_register(DOMAIN, SERVICE_RUN_LAB, _svc_lab)
    # alleen beheerders mogen het slot overbruggen of de strategie wisselen
    async_register_admin_service(
        hass, DOMAIN, "override_lock", _svc_override, schema=vol.Schema({vol.Required("bevestiging"): str})
    )
    async_register_admin_service(hass, DOMAIN, "restore_lock", _svc_restore)
    async_register_admin_service(hass, DOMAIN, "kies_strategie", _svc_pick)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.trader.async_save()
        for name in SERVICES:
            hass.services.async_remove(DOMAIN, name)
    return ok
