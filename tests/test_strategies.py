import math

import pytest

from custom_components.bitvavo_bot.strategies import (
    Costs,
    ema,
    param_specs,
    run_strategy,
    target_vol_breakout,
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
