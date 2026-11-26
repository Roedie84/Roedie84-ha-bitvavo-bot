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

from bisect import bisect_right

from .strategies import HYPOTHESES, POOLED, STRATEGY_LABELS, Bar, Costs, Trade, param_specs, run_strategy

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


def closed_bars(hour_bars: Sequence[Bar], factor: int, base_ms: int = 3_600_000) -> list[Bar]:
    """Alleen volledig afgesloten bars van factor uur (voor live gebruik)."""
    out = resample(hour_bars, factor, base_ms)
    if out and hour_bars and out[-1][0] + factor * base_ms > hour_bars[-1][0] + base_ms:
        out.pop()
    return out


PREREG = "v2"
REGIME_MARKET = "BTC-EUR"
REGIME_DAYS = 200
TOP_N = 3
MONDAY_OFFSET = 4 * DAY_MS  # 1-1-1970 was een donderdag


class Context:
    """Wat een strategie over markten heen mag weten, zonder vooruit te kijken:
    elk antwoord op tijdstip t gebruikt alleen dagbars die op t al gesloten waren."""

    def __init__(self, data: Mapping[str, Sequence[Bar]]) -> None:
        self._daily = {m: resample(b, 24) for m, b in data.items() if b}
        self._regime_t: list[int] = []
        self._regime_v: list[bool] = []
        btc = self._daily.get(REGIME_MARKET) or []
        closes = [b[4] for b in btc]
        total = 0.0
        for i, bar in enumerate(btc):
            total += closes[i]
            if i >= REGIME_DAYS:
                total -= closes[i - REGIME_DAYS]
            if i >= REGIME_DAYS - 1:
                self._regime_t.append(bar[0] + DAY_MS)  # bekend zodra deze dag gesloten is
                self._regime_v.append(closes[i] > total / REGIME_DAYS)
        self._close_at = {m: {b[0] + DAY_MS: b[4] for b in bars} for m, bars in self._daily.items()}
        self._rank: dict[int, tuple[list[int], list[frozenset]]] = {}

    def regime_at(self, t_ms: int) -> bool:
        i = bisect_right(self._regime_t, t_ms) - 1
        return i >= 0 and self._regime_v[i]

    def _ranking(self, lookback: int) -> tuple[list[int], list[frozenset]]:
        if lookback not in self._rank:
            times = [t for closes in self._close_at.values() for t in closes]
            weeks, picks = [], []
            if times:
                w = min(times)
                w += (MONDAY_OFFSET - w) % WEEK_MS  # eerste maandag 00:00 UTC
                while w <= max(times):
                    scores = []
                    for market, closes in self._close_at.items():
                        now, then = closes.get(w), closes.get(w - lookback * DAY_MS)
                        if now and then:
                            scores.append((now / then - 1, market))
                    scores.sort(reverse=True)
                    weeks.append(w)
                    picks.append(frozenset(m for _, m in scores[:TOP_N]))
                    w += WEEK_MS
            self._rank[lookback] = (weeks, picks)
        return self._rank[lookback]

    def is_selected(self, market: str, lookback: int, t_ms: int) -> bool:
        weeks, picks = self._ranking(lookback)
        i = bisect_right(weeks, t_ms) - 1
        return i >= 0 and market in picks[i]


def _eligible(bars: Sequence[Bar], cfg: LabConfig) -> bool:
    """Genoeg historie voor minstens één volledig venster (train + test)."""
    return len(bars) >= 300 and bars[-1][0] - bars[0][0] >= (cfg.train_days + cfg.test_days) * DAY_MS


def select_params(
    data: Mapping[str, Sequence[Bar]], strategy: str, timeframe: str, cfg: LabConfig | None = None
) -> dict[str, str | None]:
    """Kies de parameterset voor de komende periode, exact zoals de walk-forward
    dat doet: beste op de laatste train_days, anders niet handelen. Te jonge
    markten doen niet mee."""
    cfg = cfg or LabConfig()
    specs = param_specs()[strategy]
    factor = TIMEFRAMES[timeframe]
    tf_ms = factor * 3_600_000
    ctx = Context(data)
    out: dict[str, str | None] = {m: None for m in data}
    tf_bars = {m: closed_bars(b, factor) for m, b in data.items()}
    tf_bars = {m: b for m, b in tf_bars.items() if _eligible(b, cfg)}
    if not tf_bars:
        return out

    def fold_for(end: int):
        return [(end - cfg.train_days * DAY_MS, end, end + cfg.test_days * DAY_MS)]

    if strategy in POOLED:
        per_param: dict[str, list[Trade]] = {name: [] for name in specs}
        for market, bars in tf_bars.items():
            for name, kw in specs.items():
                per_param[name] += run_strategy(strategy, kw, market, bars, ctx, tf_ms, cfg.costs)
        _, chosen = walk_forward(per_param, fold_for(max(b[-1][0] for b in tf_bars.values())), cfg)
        pick = next(iter(chosen), None)
        for market in tf_bars:
            out[market] = pick
        return out
    for market, bars in tf_bars.items():
        per_param = {name: run_strategy(strategy, kw, market, bars, ctx, tf_ms, cfg.costs) for name, kw in specs.items()}
        _, chosen = walk_forward(per_param, fold_for(bars[-1][0]), cfg)
        out[market] = next(iter(chosen), None)
    return out


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


def _oos_trades(strat, params, is_pooled, usable, ctx, tf_ms, cfg) -> tuple[list[Trade], dict[str, int]]:
    """Out-of-sample trades van één hypothese (walk-forward), voor alle bruikbare markten."""
    pooled: list[Trade] = []
    chosen_all: dict[str, int] = {}
    if is_pooled and usable:
        # één keuze voor alle markten samen (de strategie kiest zelf tussen markten)
        per_param: dict[str, list[Trade]] = {name: [] for name in params}
        for market, bars in usable.items():
            for name, kw in params.items():
                per_param[name] += run_strategy(strat, kw, market, bars, ctx, tf_ms, cfg.costs)
        start = min(b[0][0] for b in usable.values())
        end = max(b[-1][0] for b in usable.values())
        return walk_forward(per_param, folds(start, end, cfg), cfg)
    for market, bars in usable.items():
        per_param = {
            name: run_strategy(strat, kw, market, bars, ctx, tf_ms, cfg.costs) for name, kw in params.items()
        }
        oos, chosen = walk_forward(per_param, folds(bars[0][0], bars[-1][0], cfg), cfg)
        for k, v in chosen.items():
            chosen_all[k] = chosen_all.get(k, 0) + v
        pooled.extend(oos)
    return pooled, chosen_all


def recent_performance(
    data: Mapping[str, Sequence[Bar]], strategy: str, timeframe: str, days: int = 365, cfg: LabConfig | None = None
) -> dict:
    """Houdbaarheid: hoe deed deze hypothese het out-of-sample in de laatste `days` dagen?

    Zelfde walk-forward als het Lab, maar alleen de recente trades tellen. Dit is
    bewaking, geen nieuwe toets: het kan het slot alleen dichthouden, niet openen.
    """
    cfg = cfg or LabConfig()
    data = {m: b for m, b in data.items() if len(b) > 0}
    is_pooled = strategy in POOLED
    factor = TIMEFRAMES[timeframe]
    tf_bars = {m: resample(b, factor) for m, b in data.items()}
    usable = {m: b for m, b in tf_bars.items() if len(b) >= 300 and folds(b[0][0], b[-1][0], cfg)}
    if not usable:
        return {"trades": 0, "netto_pct": None, "dagen": days}
    trades, _ = _oos_trades(strategy, param_specs()[strategy], is_pooled, usable, Context(data), factor * 3_600_000, cfg)
    since = max(b[-1][0] for b in usable.values()) - days * DAY_MS
    recent = [t.net for t in trades if t.entry_ts >= since]
    return {
        "trades": len(recent),
        "netto_pct": round(_mean(recent) * 100, 3) if recent else None,
        "winst_pct_trades": round(100 * sum(1 for x in recent if x > 0) / len(recent), 1) if recent else None,
        "dagen": days,
    }


def run_lab(data: Mapping[str, Sequence[Bar]], cfg: LabConfig | None = None) -> dict:
    """Draai het volledige Lab. data: markt -> uurbars (oplopend)."""
    cfg = cfg or LabConfig()
    specs = param_specs()
    n_hyp = sum(len(tfs) for _, tfs, _, _ in HYPOTHESES)
    alpha_corr = cfg.alpha / n_hyp
    data = {m: b for m, b in data.items() if len(b) > 0}
    ctx = Context(data)
    results: list[dict] = []
    bh_all: list[float] = []
    oos_range: list[int] = []
    tf_cache: dict[str, dict[str, list[Bar]]] = {}
    first = True

    for strat, timeframes, is_pooled, prereg in HYPOTHESES:
        params = specs[strat]
        for tf in timeframes:
            factor = TIMEFRAMES[tf]
            tf_ms = factor * 3_600_000
            if tf not in tf_cache:
                tf_cache[tf] = {m: resample(b, factor) for m, b in data.items()}
            usable = {m: b for m, b in tf_cache[tf].items() if len(b) >= 300 and folds(b[0][0], b[-1][0], cfg)}
            pooled: list[Trade] = []
            pooled_excess: list[float] = []
            chosen_all: dict[str, int] = {}
            drifts: dict[str, float] = {}
            for market, bars in usable.items():
                fl = folds(bars[0][0], bars[-1][0], cfg)
                drifts[market] = market_drift(data[market], fl[0][1], fl[-1][2])
                if first:
                    bh = buy_hold(data[market], fl[0][1], fl[-1][2], cfg.costs)
                    if bh is not None:
                        bh_all.append(bh)
                    oos_range += [fl[0][1], fl[-1][2]]
            first = False

            pooled, chosen_all = _oos_trades(strat, params, is_pooled, usable, ctx, tf_ms, cfg)

            pooled = sorted(pooled, key=lambda t: t.entry_ts)
            pooled_excess = [excess(t, drifts.get(t.market, 0.0)) for t in pooled]
            by_market: dict[str, list[Trade]] = {}
            for t in pooled:
                by_market.setdefault(t.market, []).append(t)
            per_market_mean = {m: _mean([t.net for t in ts]) for m, ts in by_market.items()}
            per_market_total = [_compound(ts) for ts in by_market.values()]

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
                    "versie": prereg,
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
        "preregistratie": PREREG,
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
