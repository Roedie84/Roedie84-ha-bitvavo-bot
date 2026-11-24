"""Lab: walk-forward-toets van de vastgelegde hypotheses. Pure Python.

Opzet (zie docs/PREREGISTRATIE.md):
- Per markt, strategie en tijdschaal: elke parameterset één keer doorrekenen.
- Walk-forward: kies per venster de beste parameterset op de trainingsperiode,
  tel alleen de trades in de daaropvolgende testperiode mee (out-of-sample).
- Bewijsvraag: verdient de strategie OOS netto geld (na kosten) én meer dan
  je krijgt door even lang op willekeurige momenten in de markt te zitten?
  Dat laatste ("alpha") scheidt timing van simpelweg meeliften op een stijgende
  markt. Getoetst op weekgemiddelden (crypto-markten bewegen samen, losse trades zijn
  niet onafhankelijk), met Bonferroni-correctie over alle hypotheses.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from .strategies import STRATEGY_LABELS, Bar, Costs, Trade, param_grid

DAY_MS = 86_400_000
WEEK_MS = 7 * DAY_MS

TIMEFRAMES: dict[str, int] = {"1h": 1, "4h": 4, "1d": 24}


@dataclass(frozen=True)
class LabConfig:
    train_days: int = 180
    test_days: int = 60
    min_train_trades: int = 5
    alpha: float = 0.05
    min_oos_trades: int = 100
    min_weeks: int = 20
    min_market_share: float = 0.6
    costs: Costs = field(default_factory=Costs)


def resample(bars: Sequence[Bar], factor: int, base_ms: int = 3_600_000) -> list[Bar]:
    """Voeg uurbars samen tot bars van factor uur (UTC-uitgelijnd)."""
    if factor == 1:
        return list(bars)
    size = factor * base_ms
    out: list[Bar] = []
    cur: list[float] | None = None
    cur_key = None
    for ts, o, h, l, c, v in bars:
        key = ts - ts % size
        if key != cur_key:
            if cur is not None:
                out.append(tuple(cur))  # type: ignore[arg-type]
            cur, cur_key = [key, o, h, l, c, v], key
        else:
            cur[2] = max(cur[2], h)
            cur[3] = min(cur[3], l)
            cur[4] = c
            cur[5] += v
    if cur is not None:
        out.append(tuple(cur))  # type: ignore[arg-type]
    return out


def folds(start_ms: int, end_ms: int, cfg: LabConfig) -> list[tuple[int, int, int]]:
    """(train_start, test_start, test_end) per walk-forward-venster."""
    train, test = cfg.train_days * DAY_MS, cfg.test_days * DAY_MS
    out = []
    t = start_ms + train
    while t + test <= end_ms:
        out.append((t - train, t, t + test))
        t += test
    return out


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def walk_forward(
    per_param: Mapping[str, Sequence[Trade]], fold_list: Sequence[tuple[int, int, int]], cfg: LabConfig
) -> tuple[list[Trade], dict[str, int]]:
    """Selecteer per venster op train, verzamel de trades uit test."""
    oos: list[Trade] = []
    chosen: dict[str, int] = {}
    for train_start, test_start, test_end in fold_list:
        best, best_score = None, 0.0
        for name, trades in per_param.items():
            # alleen trades die vóór de testperiode al gesloten zijn
            tr = [t.net for t in trades if t.entry_ts >= train_start and t.exit_ts < test_start]
            if len(tr) < cfg.min_train_trades:
                continue
            score = _mean(tr)
            if best is None or score > best_score:
                best, best_score = name, score
        if best is None or best_score <= 0:
            continue  # niets werkte in train: niet handelen in dit venster
        chosen[best] = chosen.get(best, 0) + 1
        oos.extend(t for t in per_param[best] if test_start <= t.entry_ts < test_end)
    return oos, chosen


def p_value_positive(samples: Sequence[float]) -> tuple[float, float]:
    """Eenzijdige toets gemiddelde > 0. Geeft (t, p); normale benadering."""
    n = len(samples)
    if n < 2:
        return 0.0, 1.0
    m = _mean(samples)
    var = sum((x - m) ** 2 for x in samples) / (n - 1)
    if var <= 0:
        return (math.inf, 0.0) if m > 0 else (0.0, 1.0)
    t = m / math.sqrt(var / n)
    return t, 0.5 * math.erfc(t / math.sqrt(2))


def weekly_means(trades: Sequence[Trade], values: Sequence[float] | None = None) -> list[float]:
    """Weekgemiddelden van trade-uitkomsten (standaard net, of eigen waarden)."""
    buckets: dict[int, list[float]] = {}
    for idx, t in enumerate(trades):
        buckets.setdefault(t.entry_ts // WEEK_MS, []).append(t.net if values is None else values[idx])
    return [_mean(v) for _, v in sorted(buckets.items())]


def market_drift(bars: Sequence[Bar], start_ms: int, end_ms: int) -> float:
    """Gemiddeld (rekenkundig) marktrendement per ms over [start, end).

    Bewust het rekenkundige gemiddelde van de bar-rendementen en niet de
    log-groei: het verwachte rendement van 'willekeurig een tijd in de markt
    zitten' bevat ook de volatiliteitsterm (+σ²/2).
    """
    window = [b for b in bars if start_ms <= b[0] < end_ms]
    if len(window) < 2 or window[-1][0] == window[0][0]:
        return 0.0
    total = sum(window[i][4] / window[i - 1][4] - 1 for i in range(1, len(window)))
    return total / (window[-1][0] - window[0][0])


def excess(trade: Trade, drift: float) -> float:
    """Netto rendement min wat de markt gemiddeld deed in dezelfde tijdsduur."""
    return trade.net - (math.exp(drift * (trade.exit_ts - trade.entry_ts)) - 1)


def buy_hold(bars: Sequence[Bar], start_ms: int, end_ms: int, costs: Costs) -> float | None:
    window = [b for b in bars if start_ms <= b[0] < end_ms]
    if len(window) < 2:
        return None
    buy = window[0][1] * (1 + costs.slippage) * (1 + costs.taker)
    sell = window[-1][4] * (1 - costs.slippage) * (1 - costs.taker)
    return sell / buy - 1


def _compound(trades: Sequence[Trade]) -> float:
    eq = 1.0
    for t in sorted(trades, key=lambda x: x.exit_ts):
        eq *= 1 + t.net * t.weight
    return eq - 1


def run_lab(data: Mapping[str, Sequence[Bar]], cfg: LabConfig | None = None) -> dict:
    """Draai het volledige Lab. data: markt -> uurbars (oplopend)."""
    cfg = cfg or LabConfig()
    grid = param_grid()
    n_hyp = len(grid) * len(TIMEFRAMES)
    alpha_corr = cfg.alpha / n_hyp
    data = {m: b for m, b in data.items() if len(b) > 0}
    results: list[dict] = []
    bh_all: list[float] = []
    oos_range: list[int] = []

    for tf, factor in TIMEFRAMES.items():
        tf_bars = {m: resample(b, factor) for m, b in data.items()}
        for strat, params in grid.items():
            pooled: list[Trade] = []
            pooled_excess: list[float] = []
            per_market_mean: dict[str, float] = {}
            per_market_total: list[float] = []
            chosen_all: dict[str, int] = {}
            for market, bars in tf_bars.items():
                if len(bars) < 300:
                    continue
                fl = folds(bars[0][0], bars[-1][0], cfg)
                if not fl:
                    continue
                per_param = {name: run(bars, cfg.costs) for name, run in params.items()}
                oos, chosen = walk_forward(per_param, fl, cfg)
                for k, v in chosen.items():
                    chosen_all[k] = chosen_all.get(k, 0) + v
                if oos:
                    per_market_mean[market] = _mean([t.net for t in oos])
                    per_market_total.append(_compound(oos))
                drift = market_drift(data[market], fl[0][1], fl[-1][2])
                pooled.extend(oos)
                pooled_excess.extend(excess(t, drift) for t in oos)
                if tf == "1h" and strat == next(iter(grid)):
                    bh = buy_hold(data[market], fl[0][1], fl[-1][2], cfg.costs)
                    if bh is not None:
                        bh_all.append(bh)
                    oos_range += [fl[0][1], fl[-1][2]]

            nets = [t.net for t in pooled]
            weeks = weekly_means(pooled, pooled_excess)
            t_stat, p = p_value_positive(weeks)
            mean_net = _mean(nets)
            mean_alpha = _mean(pooled_excess)
            pos_share = (
                sum(1 for v in per_market_mean.values() if v > 0) / len(per_market_mean)
                if per_market_mean
                else 0.0
            )
            pooled.sort(key=lambda t: t.entry_ts)
            half = len(pooled) // 2
            h1, h2 = _mean([t.net for t in pooled[:half]]), _mean([t.net for t in pooled[half:]])
            checks = {
                "genoeg_trades": len(nets) >= cfg.min_oos_trades,
                "genoeg_weken": len(weeks) >= cfg.min_weeks,
                "netto_positief": mean_net > 0,
                "beter_dan_markt": mean_alpha > 0,
                "significant": p < alpha_corr,
                "breed_over_markten": pos_share >= cfg.min_market_share,
                "stabiel_beide_helften": half > 0 and h1 > 0 and h2 > 0,
            }
            results.append(
                {
                    "strategie": strat,
                    "naam": STRATEGY_LABELS.get(strat, strat),
                    "tijdschaal": tf,
                    "trades": len(nets),
                    "netto_pct": round(mean_net * 100, 3),
                    "alpha_pct": round(mean_alpha * 100, 3),
                    "winst_pct_trades": round(100 * sum(1 for x in nets if x > 0) / len(nets), 1) if nets else 0.0,
                    "weken": len(weeks),
                    "t": round(t_stat, 2) if math.isfinite(t_stat) else 99.0,
                    "p": round(p, 5),
                    "markten_positief_pct": round(pos_share * 100),
                    "totaal_pct": round(_mean(per_market_total) * 100, 1) if per_market_total else 0.0,
                    "geslaagd": all(checks.values()),
                    "checks": checks,
                    "gekozen": dict(sorted(chosen_all.items(), key=lambda kv: -kv[1])[:3]),
                }
            )

    results.sort(key=lambda r: (not r["geslaagd"], -r["netto_pct"]))
    candidates = [r for r in results if r["geslaagd"]]
    with_trades = [r for r in results if r["checks"]["genoeg_trades"]] or [
        r for r in results if r["trades"] > 0
    ]
    best = with_trades[0] if with_trades else None
    return {
        "preregistratie": "v1",
        "markten": sorted(data),
        "hypotheses": n_hyp,
        "alpha_gecorrigeerd": round(alpha_corr, 5),
        "kosten": {
            "taker_pct": cfg.costs.taker * 100,
            "maker_pct": cfg.costs.maker * 100,
            "slippage_pct": cfg.costs.slippage * 100,
        },
        "oos_van": min(oos_range) if oos_range else None,
        "oos_tot": max(oos_range) if oos_range else None,
        "buy_hold_pct": round(_mean(bh_all) * 100, 1) if bh_all else None,
        "oordeel": "kandidaat" if candidates else "geen edge",
        "kandidaten": [f"{r['strategie']} {r['tijdschaal']}" for r in candidates],
        "beste": f"{best['strategie']} {best['tijdschaal']}" if best else None,
        "beste_netto_pct": best["netto_pct"] if best else None,
        "resultaten": results,
    }
