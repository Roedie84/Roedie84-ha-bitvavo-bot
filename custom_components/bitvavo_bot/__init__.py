"""Bitvavo Bot – v0.1: data verzamelen en strategieën toetsen. Plaatst geen orders."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.event import async_track_time_change

from .const import DOMAIN, SERVICE_RUN_LAB, SERVICE_UPDATE_DATA
from .manager import BitvavoBotManager

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    manager = BitvavoBotManager(hass, entry)
    entry.runtime_data = manager
    await hass.async_add_executor_job(manager.store.ensure)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Eerste download duurt minuten: op de achtergrond, HA start gewoon door.
    entry.async_create_background_task(hass, manager.async_start(), f"{DOMAIN}_start")

    async def _daily(_now) -> None:
        await manager.async_update_data()

    entry.async_on_unload(async_track_time_change(hass, _daily, hour=3, minute=17, second=0))

    async def _svc_update(_call: ServiceCall) -> None:
        await manager.async_update_data()

    async def _svc_lab(_call: ServiceCall) -> None:
        await manager.async_run_lab()

    hass.services.async_register(DOMAIN, SERVICE_UPDATE_DATA, _svc_update)
    hass.services.async_register(DOMAIN, SERVICE_RUN_LAB, _svc_lab)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.services.async_remove(DOMAIN, SERVICE_UPDATE_DATA)
        hass.services.async_remove(DOMAIN, SERVICE_RUN_LAB)
    return ok
