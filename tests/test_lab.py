import pytest

from custom_components.bitvavo_bot.lab import (
    DAY_MS,
    LabConfig,
    buy_hold,
    excess,
    market_drift,
    folds,
    p_value_positive,
    resample,
    run_lab,
    walk_forward,
    weekly_means,
)
from custom_components.bitvavo_bot.strategies import Costs, Trade

from .helpers import H, make_bars


def test_resample_4h():
    bars = [(i * H, 10.0 + i, 11.0 + i, 9.0 + i, 10.5 + i, 1.0) for i in range(8)]
    out = resample(bars, 4)
    assert len(out) == 2
    assert out[0] == (0, 10.0, 14.0, 9.0, 13.5, 4.0)
    assert out[1][0] == 4 * H and out[1][4] == 17.5


def test_folds_do_not_overlap_and_follow_train():
    cfg = LabConfig()
    fl = folds(0, 730 * DAY_MS, cfg)
    assert len(fl) == 9
    assert fl[0] == (0, 180 * DAY_MS, 240 * DAY_MS)
    for a, b in zip(fl, fl[1:]):
        assert a[2] == b[1]


def test_walk_forward_selects_on_train_only():
    cfg = LabConfig(min_train_trades=2)
    d = DAY_MS
    fl = [(0, 100 * d, 160 * d)]
    good_then_bad = [Trade(10 * d, 11 * d, 0.05), Trade(20 * d, 21 * d, 0.05), Trade(110 * d, 111 * d, -0.10)]
    bad_then_good = [Trade(10 * d, 11 * d, -0.05), Trade(20 * d, 21 * d, -0.05), Trade(110 * d, 111 * d, 0.50)]
    oos, chosen = walk_forward({"a": good_then_bad, "b": bad_then_good}, fl, cfg)
    assert chosen == {"a": 1}
    assert [t.net for t in oos] == [-0.10]  # kiest a, krijgt het verlies: geen spieken


def test_walk_forward_ignores_trades_still_open_at_test_start():
    cfg = LabConfig(min_train_trades=1)
    d = DAY_MS
    straddle = [Trade(90 * d, 120 * d, 0.9)]
    oos, chosen = walk_forward({"a": straddle}, [(0, 100 * d, 160 * d)], cfg)
    assert oos == [] and chosen == {}


def test_walk_forward_skips_fold_when_nothing_worked():
    cfg = LabConfig(min_train_trades=1)
    d = DAY_MS
    losing = [Trade(10 * d, 11 * d, -0.01), Trade(110 * d, 111 * d, 0.2)]
    oos, _ = walk_forward({"a": losing}, [(0, 100 * d, 160 * d)], cfg)
    assert oos == []


def test_p_value():
    t, p = p_value_positive([0.01] * 10 + [0.011] * 10)
    assert p < 1e-6 and t > 10
    _, p0 = p_value_positive([0.01, -0.01] * 20)
    assert p0 == pytest.approx(0.5)
    assert p_value_positive([0.1])[1] == 1.0


def test_weekly_means_clusters_trades():
    w = 7 * DAY_MS
    trades = [Trade(0, 1, 0.02), Trade(10, 11, 0.04), Trade(w + 5, w + 6, -0.01)]
    assert weekly_means(trades) == pytest.approx([0.03, -0.01])


def test_buy_hold_includes_costs():
    bars = [(i * H, 100.0, 100.0, 100.0, 100.0, 1.0) for i in range(10)]
    assert buy_hold(bars, 0, 10 * H, Costs()) == pytest.approx(-0.006, abs=2e-4)


def test_random_walk_finds_no_edge():
    """Op ruis zonder drift mag niets door de toets komen."""
    data = {f"M{i}-EUR": make_bars(24 * 500, seed=100 + i, vol=0.012) for i in range(4)}
    rep = run_lab(data)
    assert rep["hypotheses"] == 12
    assert rep["alpha_gecorrigeerd"] == pytest.approx(0.05 / 12, abs=1e-5)
    assert len(rep["resultaten"]) == 12
    assert rep["oordeel"] == "geen edge" and rep["kandidaten"] == []


def test_planted_edge_is_detected():
    """Een ingebouwde, sterke cyclus moet de mean-reversion/grid-kant wél laten slagen."""
    data = {f"M{i}-EUR": make_bars(24 * 500, seed=200 + i, vol=0.002, cycle=0.012) for i in range(4)}
    rep = run_lab(data)
    assert rep["oordeel"] == "kandidaat"
    assert rep["resultaten"][0]["geslaagd"] and rep["resultaten"][0]["netto_pct"] > 0


def test_run_lab_handles_empty_and_short_data():
    rep = run_lab({"A-EUR": [], "B-EUR": make_bars(100)})
    assert rep["oordeel"] == "geen edge" and rep["beste"] is None


def test_excess_removes_market_drift():
    bars = [(i * H, 100.0 * 1.001**i, 0, 0, 100.0 * 1.001 ** (i + 1), 1.0) for i in range(100)]
    drift = market_drift(bars, 0, 100 * H)
    assert drift * H == pytest.approx(0.001)
    rode_mee = Trade(10 * H, 30 * H, 1.001**20 - 1)  # deed precies wat de markt deed
    assert excess(rode_mee, drift) == pytest.approx(0.0, abs=5e-4)
    assert excess(Trade(10 * H, 30 * H, 0.05), drift) == pytest.approx(0.05 - (1.001**20 - 1), abs=5e-4)


def test_bull_market_alone_is_not_an_edge():
    """Stijgende markt zonder patroon: long-only verdient geld, maar dat is geen timing."""
    data = {f"M{i}-EUR": make_bars(24 * 600, seed=300 + i, vol=0.012, drift=0.00017) for i in range(5)}
    rep = run_lab(data)
    assert rep["buy_hold_pct"] > 30
    assert rep["oordeel"] == "geen edge"
