import math

import pytest

from custom_components.bitvavo_bot.strategies import (
    Costs,
    ema,
    param_specs,
    run_strategy,
    target_vol_breakout,
    leverage_table,
    brake_series,
    run_strategy as _run,
    leveraged_return,
    Trade,
    rsi,
    simulate_grid,
    simulate_signals,
    sma,
    target_donchian,
    target_ema_cross,
    target_rsi_dip,
)

from .helpers import H, make_bars

FREE = Costs(taker=0.0, maker=0.0, slippage=0.0)


def test_sma_ema_rsi_basics():
    assert sma([1, 2, 3, 4], 2) == [None, 1.5, 2.5, 3.5]
    e = ema([1, 2, 3, 4, 5], 3)
    assert e[:2] == [None, None] and e[2] == 2.0 and e[4] == pytest.approx(4.0)
    assert rsi(list(range(1, 30)), 14)[-1] == 100.0
    assert rsi(list(range(30, 1, -1)), 14)[-1] == 0.0


def test_signal_executes_on_next_open_with_costs():
    bars = [(i * H, 100 + i, 101 + i, 99 + i, 100.5 + i, 1.0) for i in range(6)]
    target = [0, 1, 1, 0, 0, 0]
    (t,) = simulate_signals(bars, target, FREE)
    assert t.entry_ts == 2 * H and t.exit_ts == 4 * H
    assert t.net == pytest.approx(104 / 102 - 1)
    (c,) = simulate_signals(bars, target, Costs(taker=0.0025, maker=0.0015, slippage=0.0005))
    expected = (104 * 0.9995 * 0.9975) / (102 * 1.0005 * 1.0025) - 1
    assert c.net == pytest.approx(expected)
    assert c.net < t.net


def test_round_trip_cost_is_about_0_6_pct():
    bars = [(i * H, 100.0, 100.0, 100.0, 100.0, 1.0) for i in range(5)]
    (t,) = simulate_signals(bars, [1, 0, 0, 0, 0], Costs())
    assert t.net == pytest.approx(-0.006, abs=2e-4)


def test_open_position_closed_at_last_close():
    bars = [(i * H, 100.0, 100.0, 100.0, 100.0 + i, 1.0) for i in range(4)]
    (t,) = simulate_signals(bars, [1, 1, 1, 1], FREE)
    assert t.exit_ts == 3 * H and t.net == pytest.approx(0.03)


@pytest.mark.parametrize(
    "fn",
    [
        lambda b: target_donchian(b, 20, 10),
        lambda b: target_ema_cross(b, 10, 30),
        lambda b: target_rsi_dip(b, 2, 10.0, 60.0, 100, 10),
    ],
)
def test_no_lookahead_in_targets(fn):
    bars = make_bars(600, seed=3)
    full = fn(bars)
    cut = 400
    assert fn(bars[:cut]) == full[:cut]


def test_no_lookahead_in_grid():
    bars = make_bars(800, seed=5, vol=0.02)
    cut_ts = bars[500][0]
    full = [t for t in simulate_grid(bars, 50, 0.02, 3, 200, Costs()) if t.exit_ts < cut_ts]
    part = [t for t in simulate_grid(bars[:501], 50, 0.02, 3, 200, Costs()) if t.exit_ts < cut_ts]
    assert full == part


def test_grid_fill_and_take_profit():
    flat = [(i * H, 100.0, 100.0, 100.0, 100.0, 1.0) for i in range(50)]
    dip = (50 * H, 100.0, 100.0, 97.0, 97.5, 1.0)  # door niveau 98
    up = (51 * H, 97.5, 100.5, 97.5, 100.0, 1.0)  # door 98*1.02=99.96
    trades = simulate_grid(flat + [dip, up], 50, 0.02, 1, 200, Costs())
    assert len(trades) == 1
    t = trades[0]
    assert t.entry_ts == 50 * H and t.exit_ts == 51 * H
    assert t.net == pytest.approx(1.02 * (1 - 0.0015) / (1 + 0.0015) - 1)


def test_grid_needs_strict_penetration():
    flat = [(i * H, 100.0, 100.0, 100.0, 100.0, 1.0) for i in range(50)]
    touch = (50 * H, 100.0, 100.0, 98.0, 99.0, 1.0)  # raakt 98 exact: geen vulling
    assert simulate_grid(flat + [touch], 50, 0.02, 1, 200, Costs()) == []


def test_param_specs_are_the_preregistered_ones():
    from custom_components.bitvavo_bot.lab import Context

    g = param_specs()
    assert {k: len(v) for k, v in g.items()} == {
        "donchian": 6, "ema_cross": 4, "rsi_dip": 6, "grid": 6,
        "rel_strength": 3, "donchian_regime": 6, "vol_breakout": 6,
        "donchian_short": 6, "ema_cross_short": 4,
        "grid_trend": 6, "grid_brake": 6,
    }
    bars = make_bars(1500, seed=9, vol=0.015)
    ctx = Context({"BTC-EUR": bars, "X-EUR": bars})
    for strat, params in g.items():
        for kw in params.values():
            for t in run_strategy(strat, kw, "X-EUR", bars, ctx, H, Costs()):
                assert t.exit_ts >= t.entry_ts and math.isfinite(t.net) and t.market == "X-EUR"


def test_vol_breakout_enters_on_big_move_and_trails():
    bars = [(i * H, 100.0, 100.5, 99.5, 100.0 + (i % 2) * 0.2, 1.0) for i in range(40)]
    bars.append((40 * H, 100.0, 104.0, 100.0, 104.0, 1.0))  # sprong ver boven de normale beweging
    bars += [(i * H, 104.0, 104.5, 103.5, 104.0, 1.0) for i in range(41, 45)]
    bars.append((45 * H, 104.0, 104.0, 96.0, 96.0, 1.0))  # val onder de meelopende stop
    t = target_vol_breakout(bars, n=20, k=2.0, m=2.0)
    assert t[39] == 0 and t[40] == 1 and t[44] == 1 and t[45] == 0
    assert target_vol_breakout(bars[:43], n=20, k=2.0, m=2.0) == t[:43]  # geen vooruitkijken


def test_long_trade_records_gross_cost_and_worst_move():
    bars = [(0, 100, 100, 100, 100, 1), (H, 100, 101, 100, 100, 1), (2 * H, 100, 103, 92, 101, 1), (3 * H, 110, 110, 110, 110, 1), (4 * H, 110, 110, 110, 110, 1)]
    (t,) = simulate_signals(bars, [1, 1, 0, 0, 0], Costs())
    assert t.side == 1 and t.gross == pytest.approx(0.10)
    assert t.cost == pytest.approx(t.gross - t.net) and 0.005 < t.cost < 0.008
    assert t.mae == pytest.approx(0.08)  # laagste koers 92 bij instap op 100


def test_short_trade_profits_from_a_fall_and_pays_borrow():
    day = 24 * H
    bars = [(i * day, 100.0, 100.0, 100.0, 100.0, 1.0) for i in range(3)] + [(3 * day, 100.0, 106.0, 90.0, 90.0, 1.0), (4 * day, 90.0, 90.0, 90.0, 90.0, 1.0), (5 * day, 90.0, 90.0, 90.0, 90.0, 1.0)]
    (t,) = simulate_signals(bars, [1, 1, 1, 0, 0, 0], Costs(), side=-1)
    assert t.side == -1 and t.entry_ts == day and t.exit_ts == 4 * day
    assert t.gross == pytest.approx(0.10)  # van 100 naar 90
    assert t.mae == pytest.approx(0.06)  # onderweg even 6% tegen
    traded = (100 * 0.9995 * 0.9975 - 90 * 1.0005 * 1.0025) / 100
    assert t.net == pytest.approx(traded - 3 * 0.0005)  # 3 dagen leenkosten
    up = [(i * day, 100.0 + 5 * i, 100.0 + 5 * i, 100.0 + 5 * i, 100.0 + 5 * i, 1.0) for i in range(6)]
    (loss,) = simulate_signals(up, [1, 1, 1, 0, 0, 0], Costs(), side=-1)
    assert loss.net < -0.14  # short in een stijgende markt verliest


def test_leverage_scales_result_costs_and_can_liquidate():
    c = Costs()
    day = 24 * H
    calm = Trade(0, 2 * day, 0.014, 1.0, "X", 1, 0.02, 0.006, 0.03)
    assert leveraged_return(calm, 1, c) == (pytest.approx(0.014), False)
    r3, liq = leveraged_return(calm, 3, c)
    assert not liq and r3 == pytest.approx(3 * 0.014 - 0.0005 * 2 * 2)  # 2 dagen rente over 2x geleend
    # 30% tegen: bij 3x (grens 28,3%) en 5x weg, bij 2x (grens 45%) nog niet
    deep = Trade(0, day, 0.01, 1.0, "X", 1, 0.016, 0.006, 0.30)
    assert leveraged_return(deep, 2, c)[1] is False
    r, liq = leveraged_return(deep, 3, c)
    assert liq and r < -0.9
    assert leveraged_return(deep, 5, c) == (pytest.approx(-0.865), True)  # 5 x (15% + 2% + halve kosten)
    assert leveraged_return(Trade(0, day, -0.5, 1.0, "X", 1, -0.5, 0.006, 0.9), 5, c)[0] >= -1.0
    rows = leverage_table([calm, deep], c)
    assert [r["hefboom"] for r in rows] == [1, 2, 3, 5]
    assert [r["liquidaties"] for r in rows] == [0, 0, 1, 1]
    assert rows[0]["gem_pct"] == pytest.approx(1.2) and rows[3]["slechtste_pct"] < -70
    assert rows[2]["max_terugval_pct"] > 80
    assert leverage_table([], c)[0]["trades"] == 0


def test_grid_allow_blocks_new_lots_without_touching_open_ones():
    flat_ = [(i * H, 100.0, 100.0, 100.0, 100.0, 1.0) for i in range(50)]
    dip = (50 * H, 100.0, 100.0, 97.0, 97.5, 1.0)
    up = (51 * H, 97.5, 100.5, 97.5, 100.0, 1.0)
    bars = flat_ + [dip, up]
    assert len(simulate_grid(bars, 50, 0.02, 1, 200, Costs())) == 1
    assert simulate_grid(bars, 50, 0.02, 1, 200, Costs(), allow=lambda i: False) == []
    # lot gekocht in bar 50, daarna alles geblokkeerd: verkoop gebeurt toch
    sold = simulate_grid(bars, 50, 0.02, 1, 200, Costs(), allow=lambda i: i <= 50)
    assert len(sold) == 1 and sold[0].exit_ts == 51 * H


def test_brake_turns_on_deep_below_grid_and_off_on_recovery():
    # ref ~100, stap 2%, 3 niveaus: laagste niveau 94, rem bij < 90, uit bij > 94
    prices_ = [100.0] * 50 + [93.0, 89.0, 92.0, 95.0, 96.0]
    bars = [(i * H, p, p, p, p, 1.0) for i, p in enumerate(prices_)]
    b = brake_series(bars, 50, 0.02, 3)
    assert b[49] is False and b[50] is False  # 93: onder het grid, maar niet diep genoeg
    assert b[51] is True and b[52] is True  # 89 zet hem aan, 92 is nog geen herstel
    assert b[53] is False  # 95 > laagste niveau (ref zakt mee, ~93,9)
    assert brake_series(bars[:52], 50, 0.02, 3) == b[:52]  # geen vooruitkijken


def test_grid_variants_run_and_trend_filter_respects_btc_regime():
    from custom_components.bitvavo_bot.lab import Context

    up = make_bars(24 * 400, seed=3, vol=0.012, drift=0.0004)
    down = make_bars(24 * 400, seed=3, vol=0.012, drift=-0.0004)
    kw = {"ref_n": 50, "step": 0.02, "levels": 3, "max_hold": 200}
    plain = _run("grid", kw, "X-EUR", up, Context({"BTC-EUR": up}), H, Costs())
    in_bull = _run("grid_trend", kw, "X-EUR", up, Context({"BTC-EUR": up}), H, Costs())
    in_bear = _run("grid_trend", kw, "X-EUR", up, Context({"BTC-EUR": down}), H, Costs())
    assert 0 < len(in_bull) <= len(plain)
    # BTC 200 dagen in een daling: na de opbouwperiode geen nieuwe lots meer
    assert all(t.entry_ts < up[0][0] + 200 * 24 * H for t in in_bear)
    braked = _run("grid_brake", kw, "X-EUR", down, Context({}), H, Costs())
    rem = brake_series(down, 50, 0.02, 3)
    idx = {b[0]: i for i, b in enumerate(down)}
    assert any(rem) and braked
    # geen enkel lot gekocht in een bar waarvoor de rem (na de vorige bar) aan stond
    assert all(not rem[idx[t.entry_ts] - 1] for t in braked)
