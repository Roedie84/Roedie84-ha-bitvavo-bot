import time
from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.bitvavo_bot.const import CANDLE_MS, DOMAIN

from .helpers import make_bars


async def test_flow_setup_download_lab_and_unload(hass: HomeAssistant, tmp_path):
    hass.config.config_dir = str(tmp_path)
    now = int(time.time() * 1000) // CANDLE_MS * CANDLE_MS
    n = 24 * 420
    series = {
        m: [list(b) for b in make_bars(n, seed=s, vol=0.012, start=now - n * CANDLE_MS)]
        for s, m in enumerate(("BTC-EUR", "ETH-EUR", "SOL-EUR"))
    }

    async def fake_top(self, count):
        return list(series)[:count]

    async def fake_candles(self, market, since, until):
        return [r for r in series[market] if since <= r[0] < until]

    with (
        patch("custom_components.bitvavo_bot.api.BitvavoPublicClient.top_eur_markets", fake_top),
        patch("custom_components.bitvavo_bot.api.BitvavoPublicClient.candles", fake_candles),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"market_count": 3, "history_days": 400}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        entry = result["result"]
        await hass.async_block_till_done(wait_background_tasks=True)

        data = hass.states.get("sensor.bitvavo_bot_data")
        assert data.state == "gereed"
        assert data.attributes["markten"] == ["BTC-EUR", "ETH-EUR", "SOL-EUR"]
        total = data.attributes["candles"]
        assert 3 * 24 * 420 - 3 <= total <= 3 * 24 * 420
        assert entry.data["markets"] == ["BTC-EUR", "ETH-EUR", "SOL-EUR"]

        verdict = hass.states.get("sensor.bitvavo_bot_lab_oordeel")
        assert verdict.state in ("geen edge", "kandidaat")
        assert len(verdict.attributes["resultaten"]) == 21
        assert (tmp_path / "bitvavo_bot" / "lab_report.json").exists()
        assert hass.states.get("sensor.bitvavo_bot_beste_netto_per_trade") is not None

        assert hass.services.has_service(DOMAIN, "update_data")
        assert hass.services.has_service(DOMAIN, "run_lab")
        await hass.services.async_call(DOMAIN, "update_data", blocking=True)
        await hass.services.async_call(DOMAIN, "run_lab", blocking=True)
        assert hass.states.get("sensor.bitvavo_bot_data").attributes["candles"] == total

        # tweede poging mag niet: single instance
        again = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        assert again["type"] is FlowResultType.ABORT

        assert await hass.config_entries.async_unload(entry.entry_id)
        assert not hass.services.has_service(DOMAIN, "run_lab")


async def test_api_failure_sets_error_state(hass: HomeAssistant, tmp_path):
    from custom_components.bitvavo_bot.api import BitvavoError

    hass.config.config_dir = str(tmp_path)

    async def boom(self, count):
        raise BitvavoError("geen verbinding")

    with patch("custom_components.bitvavo_bot.api.BitvavoPublicClient.top_eur_markets", boom):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {"market_count": 3, "history_days": 400}
        )
        await hass.async_block_till_done(wait_background_tasks=True)
    state = hass.states.get("sensor.bitvavo_bot_data")
    assert state.state == "fout" and "geen verbinding" in state.attributes["fout"]
    assert hass.states.get("sensor.bitvavo_bot_lab_oordeel").state == "nog niet gedraaid"
