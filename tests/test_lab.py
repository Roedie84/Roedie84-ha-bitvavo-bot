import pytest

from custom_components.bitvavo_bot.lab import (
    Context,
    DAY_MS,
    LabConfig,
    buy_hold,
    excess,
    market_drift,
    folds,
    p_value_positive,
    resample,
    run_lab,
    select_params,
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
    assert rep["hypotheses"] == 25
    assert rep["alpha_gecorrigeerd"] == pytest.approx(0.05 / 25, abs=1e-5)
    assert len(rep["resultaten"]) == 25 and rep["preregistratie"] == "v4"
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


def test_select_params_skips_young_markets():
    old = make_bars(24 * 300, seed=5, vol=0.004, cycle=0.012)
    young = old[-24 * 60:]  # zelfde koersen, maar pas 60 dagen historie
    chosen = select_params({"OLD-EUR": old, "NEW-EUR": young}, "grid", "1h")
    assert chosen["NEW-EUR"] is None
    assert chosen["OLD-EUR"] is not None


def _trend(n_days, daily, start_px=100.0, seed=1):
    """Uurbars met een vaste dagelijkse groei en een beetje ruis."""
    import math as _m
    import random as _r

    rnd, out, px = _r.Random(seed), [], start_px
    t0 = 1_700_000_000_000 // (24 * H) * (24 * H)
    for i in range(n_days * 24):
        o = px
        c = o * _m.exp(daily / 24 + rnd.gauss(0, 0.001))
        out.append((t0 + i * H, o, max(o, c), min(o, c), c, 1.0))
        px = c
    return out


def test_context_regime_and_ranking_do_not_look_ahead():
    data = {
        "BTC-EUR": _trend(400, 0.002, seed=1),
        "FAST-EUR": _trend(400, 0.01, seed=2),
        "MID-EUR": _trend(400, 0.004, seed=3),
        "SLOW-EUR": _trend(400, -0.004, seed=4),
        "FLAT-EUR": _trend(400, 0.0, seed=5),
    }
    ctx = Context(data)
    t0 = data["BTC-EUR"][0][0]
    day = 24 * H
    assert not ctx.regime_at(t0 + 150 * day)  # nog geen 200 dagen historie
    assert ctx.regime_at(t0 + 300 * day)  # stijgende BTC boven zijn gemiddelde
    t = t0 + 300 * day
    assert ctx.is_selected("FAST-EUR", 30, t) and ctx.is_selected("MID-EUR", 30, t)
    assert not ctx.is_selected("SLOW-EUR", 30, t)
    # zelfde antwoord als alle data ná t wordt weggelaten
    cut = {m: [b for b in bars if b[0] + H <= t] for m, bars in data.items()}
    ctx2 = Context(cut)
    for m in data:
        assert ctx2.is_selected(m, 30, t) == ctx.is_selected(m, 30, t)
    assert ctx2.regime_at(t) == ctx.regime_at(t)
    falling = Context({"BTC-EUR": _trend(400, -0.003)})
    assert not falling.regime_at(t0 + 300 * day)


def test_v2_no_false_positive_on_noise_with_btc_present():
    data = {"BTC-EUR": make_bars(24 * 700, seed=900, vol=0.012)}
    data.update({f"M{i}-EUR": make_bars(24 * 700, seed=910 + i, vol=0.012) for i in range(5)})
    rep = run_lab(data)
    assert rep["oordeel"] == "geen edge"
    v2 = [r for r in rep["resultaten"] if r["versie"] == "v2"]
    assert len(v2) == 5 and any(r["trades"] > 0 for r in v2)


def test_select_params_pooled_gives_one_choice_for_all_markets():
    data = {
        "BTC-EUR": _trend(500, 0.002, seed=1),
        "FAST-EUR": _trend(500, 0.008, seed=2),
        "MID-EUR": _trend(500, 0.004, seed=3),
        "SLOW-EUR": _trend(500, -0.002, seed=4),
        "NEW-EUR": _trend(60, 0.02, seed=5),
    }
    chosen = select_params(data, "rel_strength", "1d")
    assert chosen["NEW-EUR"] is None  # te jong
    picks = {v for k, v in chosen.items() if k != "NEW-EUR"}
    assert len(picks) == 1


def test_report_has_leverage_table_and_marks_short_rows():
    data = {"BTC-EUR": make_bars(24 * 700, seed=31, vol=0.004, cycle=0.01)}
    data.update({f"M{i}-EUR": make_bars(24 * 700, seed=32 + i, vol=0.004, cycle=0.01) for i in range(3)})
    rep = run_lab(data)
    shorts = [r for r in rep["resultaten"] if r["short"]]
    assert len(shorts) == 4 and all(r["versie"] == "v3" for r in shorts)
    lev = rep["hefboom"]
    assert lev["hypothese"] and " " in lev["hypothese"] and lev["leen_pct_per_dag"] == pytest.approx(0.05)
    assert [r["hefboom"] for r in lev["rijen"]] == [1, 2, 3, 5]
    one = lev["rijen"][0]
    best = next(r for r in rep["resultaten"] if f"{r['strategie']} {r['tijdschaal']}" == lev["hypothese"])
    assert one["trades"] == best["trades"] and one["gem_pct"] == pytest.approx(best["netto_pct"], abs=0.01)
    assert one["liquidaties"] == 0


def test_short_hypotheses_earn_in_a_falling_market_and_alpha_corrects_for_it():
    """In een dalende markt verdient elke short; alpha moet dat wegstrepen."""
    data = {"BTC-EUR": make_bars(24 * 700, seed=41, vol=0.012, drift=-0.00012)}
    data.update({f"M{i}-EUR": make_bars(24 * 700, seed=42 + i, vol=0.012, drift=-0.00012) for i in range(5)})
    rep = run_lab(data)
    assert rep["buy_hold_pct"] < -50
    shorts = [r for r in rep["resultaten"] if r["short"] and r["trades"] > 30]
    assert shorts and any(r["netto_pct"] > 0 for r in shorts)
    assert all(r["alpha_pct"] < r["netto_pct"] for r in shorts)
    assert rep["oordeel"] == "geen edge"
