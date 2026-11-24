import pytest

from custom_components.bitvavo_bot.brokers import PaperBroker
from custom_components.bitvavo_bot.engine import Engine, Limits, new_book
from custom_components.bitvavo_bot.strategies import Costs

from .helpers import H

LIM = Limits(max_per_buy=25.0, max_total=60.0, max_day_loss=5.0)
GRID = {"ref_n": 3, "step": 0.02, "levels": 2, "max_hold": 5}


def flat(n, px=100.0, start=0):
    return [((start + i) * H, px, px, px, px, 1.0) for i in range(n)]


def make(prices=None):
    prices = prices if prices is not None else {"A-EUR": 100.0, "B-EUR": 100.0, "C-EUR": 100.0}
    return Engine(new_book(0), PaperBroker(prices), "test"), prices


async def test_signal_round_trip_costs_and_no_midway_entry():
    e, prices = make()
    bars = flat(5)
    await e.on_bar("A-EUR", bars, "ema_cross", {}, LIM, True, target=[0, 0, 1, 1, 1])
    assert e.book["positions"] == []  # eerste bar na de start: niet halverwege instappen
    bars = flat(6)
    await e.on_bar("A-EUR", bars, "ema_cross", {}, LIM, True, target=[0, 0, 1, 1, 1, 1])
    assert e.book["positions"] == []  # al long vóór de start, geen verse omslag
    bars = flat(8)
    await e.on_bar("A-EUR", bars, "ema_cross", {}, LIM, True, target=[0, 0, 1, 1, 1, 1, 0, 1])
    (pos,) = e.book["positions"]
    assert pos["cost"] == pytest.approx(25.0)
    assert e.invested() == pytest.approx(25.0)
    # zelfde bar nogmaals: niets dubbel
    await e.on_bar("A-EUR", bars, "ema_cross", {}, LIM, True, target=[0, 0, 1, 1, 1, 1, 0, 1])
    assert len(e.book["positions"]) == 1
    bars = flat(9)
    await e.on_bar("A-EUR", bars, "ema_cross", {}, LIM, True, target=[0, 0, 1, 1, 1, 1, 0, 1, 0])
    assert e.book["positions"] == []
    assert e.book["trade_count"] == 1 and e.book["win_count"] == 0
    assert e.book["realized"] == pytest.approx(-25.0 * 0.006, abs=0.005)  # ≈ 0,6% per rondje
    assert e.book["trades"][-1]["reden"] == "signaal"


async def test_total_cap_trading_switch_and_missing_params():
    e, _ = make()
    for m in ("A-EUR", "B-EUR", "C-EUR"):
        await e.on_bar(m, flat(2), "ema_cross", {}, LIM, True, target=[0, 0])
    await e.on_bar("A-EUR", flat(3), "ema_cross", {}, LIM, True, target=[0, 0, 1])
    await e.on_bar("B-EUR", flat(3), "ema_cross", {}, LIM, True, target=[0, 0, 1])
    await e.on_bar("C-EUR", flat(3), "ema_cross", {}, LIM, True, target=[0, 0, 1])
    assert len(e.book["positions"]) == 2  # 3 x 25 past niet binnen max 60
    e2, _ = make()
    await e2.on_bar("A-EUR", flat(2), "ema_cross", {}, LIM, True, target=[0, 0])
    await e2.on_bar("A-EUR", flat(3), "ema_cross", {}, LIM, False, target=[0, 0, 1])
    assert e2.book["positions"] == []  # handel gepauzeerd
    await e2.on_bar("A-EUR", flat(4), "ema_cross", None, LIM, True, target=[0, 0, 1, 1])
    assert e2.book["positions"] == []  # geen parameterset: niet instappen


async def test_day_loss_blocks_new_buys_until_next_day():
    e, prices = make()
    await e.on_bar("A-EUR", flat(2), "ema_cross", {}, LIM, True, target=[0, 0])
    await e.on_bar("A-EUR", flat(3), "ema_cross", {}, LIM, True, target=[0, 0, 1])
    e.roll_day(1_000, prices, LIM)
    assert not e.book["day_paused"]
    prices["A-EUR"] = 70.0  # -30% op 25 euro = ruim boven de 5 euro dagverlies
    e.roll_day(2_000, prices, LIM)
    assert e.book["day_paused"]
    await e.on_bar("B-EUR", flat(2), "ema_cross", {}, LIM, True, target=[0, 0])
    await e.on_bar("B-EUR", flat(3), "ema_cross", {}, LIM, True, target=[0, 0, 1])
    assert [p["market"] for p in e.book["positions"]] == ["A-EUR"]
    e.roll_day(2_000 + 2 * 86_400_000, prices, LIM)
    assert not e.book["day_paused"]


async def test_grid_fill_take_profit_and_no_rebuy_same_bar():
    e, prices = make()
    await e.on_bar("A-EUR", flat(4), "grid", GRID, LIM, True)
    buys = sorted(o["price"] for o in e.book["orders"])
    assert buys == pytest.approx([96.0, 98.0])
    assert e.invested() == pytest.approx(2 * 25 / 1.0015, rel=1e-6)
    prices["A-EUR"] = 98.0
    await e.poll()
    assert e.book["positions"] == []  # exact raken is geen vulling
    prices["A-EUR"] = 97.5
    await e.poll()
    (pos,) = e.book["positions"]
    assert pos["level"] == 1 and pos["entry_px"] == pytest.approx(98.0)
    (sell,) = [o for o in e.book["orders"] if o["side"] == "sell"]
    assert sell["price"] == pytest.approx(98.0 * 1.02)
    prices["A-EUR"] = 100.2
    await e.poll()
    assert e.book["positions"] == [] and e.book["trade_count"] == 1
    expected = 25 / 1.0015 * 1.02 * (1 - 0.0015) - 25
    assert e.book["realized"] == pytest.approx(expected, abs=1e-6)
    assert e.book["trades"][-1]["reden"] == "grid-doel"
    # volgende bar: niveau 1 is deze bar verkocht en wordt niet meteen herkocht
    await e.on_bar("A-EUR", flat(5), "grid", GRID, LIM, True)
    assert sorted(o["level"] for o in e.book["orders"]) == [2]
    await e.on_bar("A-EUR", flat(6), "grid", GRID, LIM, True)
    assert sorted(o["level"] for o in e.book["orders"]) == [1, 2]


async def test_grid_time_stop_and_gap_buys_at_market():
    e, prices = make({"A-EUR": 97.0})
    await e.on_bar("A-EUR", flat(4), "grid", GRID, LIM, True)
    # koers staat al onder niveau 1 (98): direct tegen de markt, niveau 2 (96) als limiet
    (pos,) = e.book["positions"]
    assert pos["level"] == 1
    assert {o["side"] for o in e.book["orders"]} == {"buy", "sell"}
    for n in range(5, 10):
        await e.on_bar("A-EUR", flat(n), "grid", GRID, LIM, True)
    assert all(p["level"] != 1 or p["id"] != pos["id"] for p in e.book["positions"])
    assert any(t["reden"] == "tijdstop" for t in e.book["trades"])


async def test_flatten_sells_everything_and_cancels_orders():
    e, prices = make({"A-EUR": 97.0})
    await e.on_bar("A-EUR", flat(4), "grid", GRID, LIM, True)
    assert e.book["positions"] and e.book["orders"]
    await e.flatten()
    assert e.book["positions"] == [] and e.book["orders"] == []
    assert e.book["trades"][-1]["reden"] == "noodstop"
    s = e.summary(prices)
    assert s["open_posities"] == 0 and s["ingezet"] == 0


async def test_summary_values_open_position():
    e, prices = make()
    await e.on_bar("A-EUR", flat(2), "ema_cross", {}, LIM, True, target=[0, 0])
    await e.on_bar("A-EUR", flat(3), "ema_cross", {}, LIM, True, target=[0, 0, 1])
    prices["A-EUR"] = 110.0
    s = e.summary(prices)
    assert s["open_posities"] == 1 and s["resultaat"] > 2.0
    assert s["posities"][0]["markt"] == "A-EUR"


async def test_grid_round_robin_spreads_the_cap_over_markets():
    e, prices = make({"A-EUR": 100.0, "B-EUR": 100.0, "C-EUR": 100.0})
    lim = Limits(max_per_buy=25.0, max_total=100.0, max_day_loss=50.0)
    grid3 = {**GRID, "levels": 3}
    items = [(m, flat(4), grid3) for m in ("A-EUR", "B-EUR", "C-EUR")]
    await e.on_grid_bars(items, lim, True)
    placed = sorted((o["market"], o["level"]) for o in e.book["orders"])
    # 4 orders passen: eerst niveau 1 voor alle drie, dan pas niveau 2
    assert placed == [("A-EUR", 1), ("A-EUR", 2), ("B-EUR", 1), ("C-EUR", 1)]
    assert set(e.book["last_bar"]) == {"A-EUR", "B-EUR", "C-EUR"}
    await e.on_grid_bars(items, lim, True)  # zelfde bar: niets dubbel
    assert len(e.book["orders"]) == 4
    # een markt zonder parameters plaatst niets, maar blokkeert de rest niet
    e2, _ = make({"A-EUR": 100.0, "B-EUR": 100.0})
    await e2.on_grid_bars([("A-EUR", flat(4), None), ("B-EUR", flat(4), GRID)], lim, True)
    assert {o["market"] for o in e2.book["orders"]} == {"B-EUR"}


async def test_equity_is_recorded_hourly_and_thinned():
    e, prices = make()
    e.record_equity(0, prices)
    e.record_equity(60_000, prices)  # zelfde uur: geen nieuw punt
    e.record_equity(3_600_000, prices)
    assert [p[0] for p in e.book["equity"]] == [0, 3_600_000]
    for i in range(2, 3200):
        e.record_equity(i * 3_600_000, prices)
    assert len(e.book["equity"]) < 3000 and e.book["equity"][-1][0] == 3199 * 3_600_000
    assert len(e.summary(prices)["verloop"]) == 720


async def test_position_row_explains_itself():
    e, prices = make({"A-EUR": 97.0})
    await e.on_bar("A-EUR", flat(4), "grid", GRID, LIM, True)
    (row,) = e.summary(prices)["posities"]
    assert row["markt"] == "A-EUR" and row["niveau"] == 1 and row["inzet"] == 25.0
    assert row["koers_nu"] == 97.0 and row["doel"] == pytest.approx(row["koers_in"] * 1.02)
    assert row["aantal"] > 0 and row["sinds"] > 0
    # verse positie: koers vrijwel gelijk, resultaat licht negatief door de kosten
    assert abs(row["koers_pct"]) < 0.1 and -0.25 < row["resultaat"] < 0 and row["pct"] < 0
    prices["A-EUR"] = 99.0
    (row,) = e.summary(prices)["posities"]
    assert row["koers_pct"] > 1.9 and row["resultaat"] > 0.3
