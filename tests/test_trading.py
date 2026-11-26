import json
import time
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.bitvavo_bot.const import CANDLE_MS, DOMAIN

from .helpers import make_bars
from .test_live_broker import MARKETS, FakeExchange

NAMES = ("BTC-EUR", "ETH-EUR", "SOL-EUR")


@contextmanager
def fake_bitvavo(now):
    n = 24 * 420
    series = {
        m: [list(b) for b in make_bars(n, seed=40 + s, vol=0.012, start=now - n * CANDLE_MS)]
        for s, m in enumerate(NAMES)
    }

    async def top(self, count):
        return list(series)[:count]

    async def candles(self, market, since, until):
        return [r for r in series[market] if since <= r[0] < until]

    async def prices(self):
        return {m: rows[-1][4] for m, rows in series.items()}

    async def info(self):
        return {m: {**MARKETS["BTC-EUR"], "market": m, "base": m.split("-")[0]} for m in series}

    base = "custom_components.bitvavo_bot.api.BitvavoPublicClient."
    with (
        patch(base + "top_eur_markets", top),
        patch(base + "candles", candles),
        patch(base + "ticker_prices", prices),
        patch(base + "market_info", info),
    ):
        yield series


async def _setup(hass, tmp_path):
    hass.config.config_dir = str(tmp_path)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"market_count": 3, "history_days": 400}
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    return result["result"]


async def test_paper_starts_and_live_is_locked(hass: HomeAssistant, tmp_path):
    now = int(time.time() * 1000) // CANDLE_MS * CANDLE_MS
    with fake_bitvavo(now):
        entry = await _setup(hass, tmp_path)
        trader = entry.runtime_data.trader
        for eid in (
            "switch.bitvavo_bot_live", "switch.bitvavo_bot_handel_actief", "button.bitvavo_bot_noodstop",
            "number.bitvavo_bot_max_per_koop", "number.bitvavo_bot_max_totaal", "number.bitvavo_bot_max_dagverlies",
            "sensor.bitvavo_bot_strategie", "sensor.bitvavo_bot_live_slot", "sensor.bitvavo_bot_paper_resultaat",
            "sensor.bitvavo_bot_live_resultaat",
        ):
            assert hass.states.get(eid) is not None, eid
        assert hass.states.get("switch.bitvavo_bot_live").state == "off"
        assert hass.states.get("switch.bitvavo_bot_handel_actief").state == "on"
        assert hass.states.get("number.bitvavo_bot_max_per_koop").state == "25.0"
        assert hass.states.get("sensor.bitvavo_bot_live_slot").state == "op slot"
        # het plan is gekozen en de paper-motor heeft elke markt verwerkt
        assert trader.plan and hass.states.get("sensor.bitvavo_bot_strategie").state != "nog niet gekozen"
        assert set(trader.paper.book["last_bar"]) == set(NAMES)
        if trader.plan["strategie"] != "grid":  # signaalstrategie: nooit halverwege instappen bij de start
            assert trader.paper.book["positions"] == []
        assert trader.paper.invested() <= trader.limits.max_total
        assert set(trader.plan["params"]) == set(NAMES)

        # slot: live aanzetten wordt geweigerd
        with pytest.raises(HomeAssistantError, match="op slot"):
            await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.bitvavo_bot_live"}, blocking=True)
        assert not trader.live
        with pytest.raises(HomeAssistantError, match="Bevestiging onjuist"):
            await hass.services.async_call(DOMAIN, "override_lock", {"bevestiging": "ja"}, blocking=True)
        await hass.services.async_call(DOMAIN, "override_lock", {"bevestiging": "ik accepteer het risico"}, blocking=True)
        assert hass.states.get("sensor.bitvavo_bot_live_slot").state == "overbrugd"
        # zonder sleutel in secrets.yaml kan live niet aan
        with pytest.raises(HomeAssistantError, match="secrets.yaml"):
            await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.bitvavo_bot_live"}, blocking=True)
        assert not trader.live

        # met sleutel en een (nagebootste) beurs
        (tmp_path / "secrets.yaml").write_text("bitvavo_api_key: SLEUTEL-GEHEIM-1\nbitvavo_api_secret: GEHEIM-WOORD-2\n")
        ex = FakeExchange(eur=412.6)
        with patch("custom_components.bitvavo_bot.api.BitvavoPrivateClient.request", lambda self, *a, **k: ex.request(*a, **k)):
            await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.bitvavo_bot_live"}, blocking=True)
            assert trader.live and trader.balance_eur == 412.6
            assert hass.states.get("switch.bitvavo_bot_live").state == "on"
            assert trader.live_engine.book["last_bar"] == trader.paper.book["last_bar"]
            assert trader.live_engine.book["positions"] == []
            assert not [c for c in ex.calls if c[0] == "POST"]  # aanzetten plaatst zelf geen order
            assert trader.live_engine.book is not trader.paper.book

            await hass.services.async_call(
                "number", "set_value", {"entity_id": "number.bitvavo_bot_max_per_koop", "value": 50}, blocking=True
            )
            assert trader.limits.max_per_buy == 50.0
            await hass.services.async_call(DOMAIN, "update_data", blocking=True)
            await trader.async_minute()

            saved = json.loads((tmp_path / "bitvavo_bot" / "trader_state.json").read_text())
            assert saved["live"] is True and saved["limits"]["max_per_buy"] == 50.0 and saved["override"] is True
            assert "SLEUTEL-GEHEIM-1" not in json.dumps(saved) and "GEHEIM-WOORD-2" not in json.dumps(saved)

            await hass.services.async_call("button", "press", {"entity_id": "button.bitvavo_bot_noodstop"}, blocking=True)
        assert not trader.live and not trader.trading
        assert hass.states.get("switch.bitvavo_bot_live").state == "off"
        assert hass.states.get("switch.bitvavo_bot_handel_actief").state == "off"
        await hass.services.async_call(DOMAIN, "restore_lock", blocking=True)
        assert hass.states.get("sensor.bitvavo_bot_live_slot").state == "op slot"

        assert await hass.config_entries.async_unload(entry.entry_id)
        for svc in ("override_lock", "restore_lock", "kies_strategie", "run_lab", "update_data"):
            assert not hass.services.has_service(DOMAIN, svc)


async def test_state_survives_restart_and_screen_api(hass: HomeAssistant, tmp_path, hass_ws_client):
    now = int(time.time() * 1000) // CANDLE_MS * CANDLE_MS
    with fake_bitvavo(now):
        entry = await _setup(hass, tmp_path)
        trader = entry.runtime_data.trader
        trader.paper.book["realized"] = 3.21
        trader.paper.book["trade_count"] = 4
        await trader.async_set_limit("max_total", 500)
        started = trader.paper.book["started"]
        plan = dict(trader.plan)

        ws = await hass_ws_client(hass)
        await ws.send_json({"id": 1, "type": "bitvavo_bot/overview"})
        o = (await ws.receive_json())["result"]
        assert o["mode"] == "paper" and o["lock"]["state"] == "op slot" and o["version"]
        assert [m["market"] for m in o["markets"]] == list(NAMES)
        assert all(m["price"] and m["change"] is not None for m in o["markets"])
        assert o["book"]["gerealiseerd"] == 3.21 and o["limits"]["max_total"] == 500
        assert len(o["lab"]["resultaten"]) == 17 and o["plan"]["strategie"] == plan["strategie"]
        await ws.send_json({"id": 2, "type": "bitvavo_bot/chart", "market": "BTC-EUR", "timeframe": plan["tijdschaal"]})
        c = (await ws.receive_json())["result"]
        assert 100 < len(c["bars"]) <= 600 and len(c["bars"][0]) == 6
        for line in c["lines"]:
            assert len(line["values"]) == len(c["bars"])
        await ws.send_json({"id": 3, "type": "bitvavo_bot/chart", "market": "NOPE-EUR"})
        assert (await ws.receive_json())["success"] is False

        assert await hass.config_entries.async_unload(entry.entry_id)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        t2 = entry.runtime_data.trader
        assert t2 is not trader
        assert t2.paper.book["realized"] == 3.21 and t2.paper.book["trade_count"] == 4
        assert t2.paper.book["started"] == started and t2.limits.max_total == 500
        assert t2.plan["strategie"] == plan["strategie"] and t2.plan["selected_at"] == plan["selected_at"]


async def test_upgrade_backfills_history_reruns_lab_and_keeps_old_verdict(hass: HomeAssistant, tmp_path):
    """Situatie na een update: 100 dagen data op schijf en een v1-rapport."""
    import gzip

    now = int(time.time() * 1000) // CANDLE_MS * CANDLE_MS
    with fake_bitvavo(now) as series:
        base = tmp_path / "bitvavo_bot" / "candles"
        base.mkdir(parents=True)
        for m, rows in series.items():
            with gzip.open(base / f"{m}_1h.json.gz", "wt") as fh:
                json.dump(rows[-24 * 100 : -5], fh)
        (tmp_path / "bitvavo_bot" / "lab_report.json").write_text(
            json.dumps({"preregistratie": "v1", "oordeel": "geen edge", "beste": "grid 1h", "kandidaten": [], "resultaten": []})
        )
        hass.config.config_dir = str(tmp_path)
        from pytest_homeassistant_custom_component.common import MockConfigEntry

        entry = MockConfigEntry(domain=DOMAIN, data={"market_count": 3, "history_days": 730, "markets": list(NAMES)})
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        m = entry.runtime_data
        for market in NAMES:
            cov = m.coverage[market]
            assert cov["candles"] >= 24 * 420 - 1  # oudere historie én de laatste uren bijgehaald
        assert m.report["preregistratie"] == "v2" and len(m.report["resultaten"]) == 17
        old = json.loads((tmp_path / "bitvavo_bot" / "lab_report_v1.json").read_text())
        assert old["beste"] == "grid 1h"
        meta = json.loads((tmp_path / "bitvavo_bot" / "meta.json").read_text())
        assert meta["historie_dagen"] == {n: 1460 for n in NAMES}


async def test_unproven_plan_yields_to_lab_candidate(hass: HomeAssistant, tmp_path):
    now = int(time.time() * 1000) // CANDLE_MS * CANDLE_MS
    with fake_bitvavo(now):
        entry = await _setup(hass, tmp_path)
        m = entry.runtime_data
        trader = m.trader
        before = f"{trader.plan['strategie']} {trader.plan['tijdschaal']}"
        other = "vol_breakout 4h" if before != "vol_breakout 4h" else "ema_cross 4h"
        strat, tf = other.split(" ")
        m.report = {**m.report, "kandidaten": [other], "oordeel": "kandidaat"}
        for row in m.report["resultaten"]:
            row["geslaagd"] = row["strategie"] == strat and row["tijdschaal"] == tf
        m.report["resultaten"].sort(key=lambda r: not r["geslaagd"])
        await trader.async_on_data()
        assert f"{trader.plan['strategie']} {trader.plan['tijdschaal']}" == other
        assert trader.plan["bewezen"] is True
        assert trader.paper.book["positions"] == [] and trader.paper.book["orders"] == []
        assert hass.states.get("sensor.bitvavo_bot_strategie").state == other


async def test_live_push_to_screen_and_fast_paper_fills(hass: HomeAssistant, tmp_path, hass_ws_client):
    now = int(time.time() * 1000) // CANDLE_MS * CANDLE_MS
    with fake_bitvavo(now):
        entry = await _setup(hass, tmp_path)
        trader = entry.runtime_data.trader
        ws = await hass_ws_client(hass)
        await ws.send_json({"id": 5, "type": "bitvavo_bot/stream"})
        assert (await ws.receive_json())["success"]
        first = (await ws.receive_json())["event"]  # direct de actuele stand
        assert first["live"]["mode"] == "paper" and "verloop" not in first["live"]["book"]
        assert first["stream"] is False  # in de test is er geen koersstroom

        # koersstroom levert een transactie en een lopende candle
        trader.stream.connected = True
        trader.stream.handle({"event": "ticker", "market": "BTC-EUR", "lastPrice": "12345.5"})
        trader.stream.handle({"event": "candles", "market": "BTC-EUR", "interval": "1h", "candle": [[str(now), "1", "2", "0.5", "1.5", "3"]]})
        trader.push_live()
        ev = (await ws.receive_json())["event"]
        assert ev["stream"] is True and ev["candles"]["BTC-EUR"][0] == now
        assert next(m for m in ev["live"]["markets"] if m["market"] == "BTC-EUR")["price"] == 1.5
        trader.push_live()  # niets veranderd: geen nieuw bericht
        trader.stream.handle({"event": "ticker", "market": "ETH-EUR", "lastPrice": "2222"})
        trader.push_live()
        ev = (await ws.receive_json())["event"]
        assert next(m for m in ev["live"]["markets"] if m["market"] == "ETH-EUR")["price"] == 2222.0

        # snelle controle van paper-orders via de koersstroom
        trader.paper.book["orders"] = [{"id": "x", "market": "ETH-EUR", "side": "buy", "price": 2000.0, "amount": 0.0125, "level": 1, "bar": now, "step": 0.02}]
        trader.paper.book["positions"] = []
        trader.stream.reset_extremes()
        await trader.async_fast_tick()
        assert trader.paper.book["positions"] == []
        trader.stream.handle({"event": "ticker", "market": "ETH-EUR", "lastPrice": "1995"})
        trader.stream.handle({"event": "ticker", "market": "ETH-EUR", "lastPrice": "2010"})
        await trader.async_fast_tick()
        (pos,) = trader.paper.book["positions"]
        assert pos["market"] == "ETH-EUR" and pos["entry_px"] == 2000.0
        assert [o["side"] for o in trader.paper.book["orders"]] == ["sell"]

        # met een verse koersstroom haalt de minuutcyclus geen koersen meer via REST
        with patch("custom_components.bitvavo_bot.api.BitvavoPublicClient.ticker_prices", side_effect=AssertionError("REST niet nodig")):
            await trader.async_minute()
        trader.stream.connected = False
        await trader.async_minute()  # terugval op REST werkt

        await ws.send_json({"id": 6, "type": "bitvavo_bot/chart", "market": "BTC-EUR", "timeframe": "4h"})
        c = (await ws.receive_json())["result"]
        assert c["tf_ms"] == 4 * 3_600_000 and c["last_hour"] > 0


async def test_cycles_never_overlap_and_admin_only_services(hass: HomeAssistant, tmp_path, hass_read_only_user):
    import asyncio

    from homeassistant.core import Context
    from homeassistant.exceptions import Unauthorized

    now = int(time.time() * 1000) // CANDLE_MS * CANDLE_MS
    with fake_bitvavo(now):
        entry = await _setup(hass, tmp_path)
        trader = entry.runtime_data.trader
        running, peak = 0, 0

        async def slow(*args, **kwargs):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.01)
            running -= 1

        with (
            patch.object(trader.paper, "on_grid_bars", slow),
            patch.object(trader.paper, "on_bar", slow),
            patch.object(trader.paper, "poll", slow),
        ):
            trader.paper.book["orders"] = [{"id": "x", "market": "BTC-EUR", "side": "buy", "price": 1.0, "amount": 1.0}]
            trader.stream.connected, trader.stream.last_msg = True, time.time()
            await asyncio.gather(
                trader.async_on_data(), trader.async_on_data(), trader.async_minute(),
                trader.async_fast_tick(), trader.async_minute(),
            )
        assert peak == 1  # nooit twee tegelijk aan de orders

        ctx = Context(user_id=hass_read_only_user.id)
        for svc, data in (("override_lock", {"bevestiging": "IK ACCEPTEER HET RISICO"}), ("kies_strategie", {}), ("restore_lock", {})):
            with pytest.raises(Unauthorized):
                await hass.services.async_call(DOMAIN, svc, data, blocking=True, context=ctx)
        assert trader.override is False


async def test_entities_unavailable_until_state_is_loaded(hass: HomeAssistant, tmp_path):
    now = int(time.time() * 1000) // CANDLE_MS * CANDLE_MS
    with fake_bitvavo(now):
        entry = await _setup(hass, tmp_path)
        m = entry.runtime_data
        assert m.ready and hass.states.get("sensor.bitvavo_bot_paper_resultaat").state != "unavailable"
        m.ready = False
        m.notify()
        await hass.async_block_till_done()
        for eid in ("sensor.bitvavo_bot_paper_resultaat", "switch.bitvavo_bot_live", "number.bitvavo_bot_max_totaal"):
            assert hass.states.get(eid).state == "unavailable"


async def test_health_fees_notifications_reconcile_and_export(hass: HomeAssistant, tmp_path, hass_ws_client):
    from homeassistant.data_entry_flow import FlowResultType

    now = int(time.time() * 1000) // CANDLE_MS * CANDLE_MS
    sent = []

    async def fake_notify(call):
        sent.append((call.data["title"], call.data["message"]))

    hass.services.async_register("notify", "telefoon", fake_notify)
    with fake_bitvavo(now):
        entry = await _setup(hass, tmp_path)
        trader = entry.runtime_data.trader

        # houdbaarheid is berekend voor het gekozen plan en telt mee in het slot
        key = f"{trader.plan['strategie']} {trader.plan['tijdschaal']}"
        assert trader.health["plan"] == key and trader.health["dagen"] == 365 and "checked_at" in trader.health
        lock = trader.lock_status()
        assert "recent_positief" in lock["checks"] and lock["houdbaarheid"] == trader.health
        trader.health = {**trader.health, "trades": 50, "netto_pct": 0.4}
        assert trader.health_ok()
        trader.health["netto_pct"] = -0.1
        assert not trader.health_ok()
        trader.health = {**trader.health, "netto_pct": 0.4, "trades": 5}
        assert not trader.health_ok()  # te weinig trades om iets te zeggen
        trader.health = {**trader.health, "trades": 50, "plan": "iets anders"}
        assert not trader.health_ok()
        assert hass.states.get("sensor.bitvavo_bot_live_slot").attributes["recent_positief"] in (True, False)

        # zonder sleutel: aanname blijft staan
        assert trader.fees is None and trader.paper.exit_cost == pytest.approx(0.003)

        # opties: meldingsdienst kiezen
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        assert flow["type"] is FlowResultType.FORM
        done = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"notify_service": "notify.telefoon", "daily_summary": True}
        )
        assert done["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done(wait_background_tasks=True)
        trader = entry.runtime_data.trader
        assert entry.options["notify_service"] == "notify.telefoon"

        # dagstop -> direct een melding, één keer
        await trader._check_events()
        trader.paper.book["day_paused"] = True
        await trader._check_events()
        await trader._check_events()
        await hass.async_block_till_done()
        assert [t for t, _ in sent] == ["Bitvavo Bot – dagstop (paper)"]
        trader.paper.book["last_error"] = "24-11 10:00 BTC-EUR: iets mis"
        await trader._check_events()
        await hass.async_block_till_done()
        assert sent[-1][0] == "Bitvavo Bot – fout (paper)"

        # dagoverzicht
        await trader.async_daily_summary()
        await hass.async_block_till_done()
        title, body = sent[-1]
        assert title == "Bitvavo Bot – dagoverzicht" and body.startswith("Paper: € ") and "slot: op slot" in body
        hass.config_entries.async_update_entry(entry, options={"notify_service": "notify.telefoon", "daily_summary": False})
        n = len(sent)
        await trader.async_daily_summary()
        await hass.async_block_till_done()
        assert len(sent) == n

        # echte kosten + saldo-afstemming met een (nagebootste) beurs
        (tmp_path / "secrets.yaml").write_text("bitvavo_api_key: SLEUTEL-GEHEIM-1\nbitvavo_api_secret: GEHEIM-WOORD-2\n")
        ex = FakeExchange(eur=900)
        with patch("custom_components.bitvavo_bot.api.BitvavoPrivateClient.request", lambda self, *a, **k: ex.request(*a, **k)):
            trader.fees = None
            await trader._refresh_fees()
            assert trader.fees["taker"] == 0.002 and trader.fees["tier"] == "1"
            assert trader.paper.exit_cost == pytest.approx(0.0025)
            buy = await trader.paper.broker.market_buy("BTC-EUR", 100.2)
            assert buy["fee"] == pytest.approx(0.2)

            trader.override = True
            await trader.async_set_live(True)
            await hass.async_block_till_done()
            assert sent[-1][0] == "Bitvavo Bot – LIVE aan"
            book = trader.live_engine.book
            mk = lambda m, amount: {"id": m, "market": m, "amount": amount, "cost": 25.0, "entry_px": 1.0, "entry_ts": 1, "entry_bar": 0, "level": None}
            book["positions"] = [mk("BTC-EUR", 1.0), mk("ETH-EUR", 2.0), mk("SOL-EUR", 4.0)]
            ex.coins = {"BTC": 1.0, "ETH": 1.5}  # ETH deels weg, SOL helemaal weg
            await trader._reconcile_live()
            await hass.async_block_till_done()
            assert [p["market"] for p in book["positions"]] == ["BTC-EUR", "ETH-EUR"]
            assert book["trades"][-1]["reden"] == "buiten de bot om verkocht" and book["realized"] == -25.0
            titles = [t for t, _ in sent[-2:]]
            assert set(titles) == {"Bitvavo Bot – saldo wijkt af", "Bitvavo Bot – positie verdwenen"}
            n = len(sent)
            await trader._reconcile_live()  # dezelfde afwijking niet elk uur opnieuw melden
            await hass.async_block_till_done()
            assert len(sent) == n
            book["positions"] = []

        # vergelijking met de markt en export
        trader.bench = {"BTC-EUR": 100.0, "ETH-EUR": 50.0}
        trader.prices.update({"BTC-EUR": 110.0, "ETH-EUR": 45.0})
        assert trader.overview()["markt_sinds_start_pct"] == 0.0  # +10% en -10%
        trader.paper.book["trades"] = [{"markt": "BTC-EUR", "in": 1, "uit": 2, "inzet": 25.0, "resultaat": 1.0, "pct": 4.0, "reden": "signaal"}] * 12
        ws = await hass_ws_client(hass)
        await ws.send_json({"id": 9, "type": "bitvavo_bot/trades", "book": "paper"})
        res = (await ws.receive_json())["result"]
        assert res["book"] == "paper" and len(res["trades"]) == 12  # alle trades, niet alleen de laatste tien
        await ws.send_json({"id": 10, "type": "bitvavo_bot/overview"})
        o = (await ws.receive_json())["result"]
        assert o["notify"] is True and o["fees"]["taker"] == 0.002 and "spread_pct" in o["markets"][0]
        assert "gem_in_markt" in o["book"] and o["lock"]["houdbaarheid"]["dagen"] == 365
