"""WebSocket-API voor het handelsscherm."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, VERSION


def _manager(hass: HomeAssistant):
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    return entries[0].runtime_data if entries else None


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_overview)
    websocket_api.async_register_command(hass, ws_chart)
    websocket_api.async_register_command(hass, ws_stream)
    websocket_api.async_register_command(hass, ws_trades)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/overview"})
@callback
def ws_overview(hass, connection, msg) -> None:
    m = _manager(hass)
    if m is None:
        connection.send_error(msg["id"], "not_loaded", "Bitvavo Bot is niet geladen")
        return
    r = m.report or {}
    data = m.trader.overview()
    data.update(
        {
            "version": VERSION,
            "data": {"status": m.status, "progress": m.progress, "candles": m.total_candles, "error": m.error},
            "lab": {
                "oordeel": m.verdict,
                "beste": r.get("beste"),
                "kandidaten": r.get("kandidaten", []),
                "alpha": r.get("alpha_gecorrigeerd"),
                "buy_hold_pct": r.get("buy_hold_pct"),
                "hefboom": r.get("hefboom"),
                "resultaten": [
                    {k: v for k, v in row.items() if k not in ("checks", "gekozen")}
                    for row in r.get("resultaten", [])
                ],
            },
        }
    )
    connection.send_result(msg["id"], data)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/chart",
        vol.Required("market"): str,
        vol.Optional("timeframe", default="1h"): vol.In(["1h", "4h", "1d"]),
    }
)
@websocket_api.async_response
async def ws_chart(hass, connection, msg) -> None:
    m = _manager(hass)
    if m is None or msg["market"] not in m.markets:
        connection.send_error(msg["id"], "not_found", "Onbekende markt")
        return
    data = await hass.async_add_executor_job(m.trader.chart, msg["market"], msg["timeframe"])
    connection.send_result(msg["id"], data)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/stream"})
@callback
def ws_stream(hass, connection, msg) -> None:
    """Abonnement: het scherm krijgt elke seconde de actuele stand toegestuurd."""
    m = _manager(hass)
    if m is None:
        connection.send_error(msg["id"], "not_loaded", "Bitvavo Bot is niet geladen")
        return

    @callback
    def forward(payload: dict) -> None:
        connection.send_message(websocket_api.event_message(msg["id"], payload))

    connection.subscriptions[msg["id"]] = m.trader.stream_subscribe(forward)
    connection.send_result(msg["id"])
    m.trader.push_live()


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/trades", vol.Optional("book", default="paper"): vol.In(["paper", "live"])}
)
@callback
def ws_trades(hass, connection, msg) -> None:
    """Alle bewaarde trades van een boek (voor de CSV-export)."""
    m = _manager(hass)
    if m is None:
        connection.send_error(msg["id"], "not_loaded", "Bitvavo Bot is niet geladen")
        return
    connection.send_result(msg["id"], {"book": msg["book"], "trades": m.trader.all_trades(msg["book"])})
