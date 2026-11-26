"""Strategieën en backtest-motor. Pure Python, geen Home Assistant-afhankelijkheden.

Regels die lookahead voorkomen:
- Signaalstrategieën beslissen op de slotkoers van bar i en handelen op de
  openingskoers van bar i+1.
- De grid plaatst limietorders op basis van data t/m bar i-1 en wordt alleen
  gevuld als bar i strikt door de prijs heen gaat.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Sequence

# Bar = (ts_ms, open, high, low, close, volume), oplopend gesorteerd.
Bar = tuple[int, float, float, float, float, float]


@dataclass(frozen=True)
class Costs:
    """Kosten per zijde, als fractie."""

    taker: float = 0.0025
    maker: float = 0.0015
    slippage: float = 0.0005
    borrow_daily: float = 0.0005  # leenkosten per dag over het geleende bedrag (margin/short)


@dataclass(frozen=True)
class Trade:
    entry_ts: int
    exit_ts: int
    net: float  # netto rendement van deze trade na kosten (fractie)
    weight: float = 1.0  # deel van het kapitaal (grid: 1/levels)
    market: str = ""
    side: int = 1  # 1 = long, -1 = short
    gross: float = 0.0  # koersbeweging in het voordeel, vóór kosten
    cost: float = 0.0  # handelskosten (fees + slippage) als fractie, zonder leenkosten
    mae: float = 0.0  # grootste beweging tegen de positie in tijdens de looptijd (fractie)


# ---------------------------------------------------------------- indicatoren


def sma(values: Sequence[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


def ema(values: Sequence[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < n:
        return out
    k = 2.0 / (n + 1)
    cur = sum(values[:n]) / n
    out[n - 1] = cur
    for i in range(n, len(values)):
        cur = values[i] * k + cur * (1 - k)
        out[i] = cur
    return out


def rsi(values: Sequence[float], n: int) -> list[float | None]:
    """RSI volgens Wilder."""
    out: list[float | None] = [None] * len(values)
    if len(values) <= n:
        return out
    gain = loss = 0.0
    for i in range(1, n + 1):
        d = values[i] - values[i - 1]
        if d > 0:
            gain += d
        else:
            loss -= d
    gain /= n
    loss /= n
    out[n] = 100.0 if loss == 0 else 100.0 - 100.0 / (1 + gain / loss)
    for i in range(n + 1, len(values)):
        d = values[i] - values[i - 1]
        gain = (gain * (n - 1) + (d if d > 0 else 0.0)) / n
        loss = (loss * (n - 1) + (-d if d < 0 else 0.0)) / n
        out[i] = 100.0 if loss == 0 else 100.0 - 100.0 / (1 + gain / loss)
    return out


# ------------------------------------------------------- signaal-strategieën


def target_donchian(bars: Sequence[Bar], entry_n: int, exit_n: int) -> list[int]:
    """Trend: koop bij uitbraak boven de hoogste high van entry_n bars,
    verkoop onder de laagste low van exit_n bars."""
    highs = [b[2] for b in bars]
    lows = [b[3] for b in bars]
    target = [0] * len(bars)
    pos = 0
    for i in range(len(bars)):
        close = bars[i][4]
        if not pos and i >= entry_n and close > max(highs[i - entry_n : i]):
            pos = 1
        elif pos and i >= exit_n and close < min(lows[i - exit_n : i]):
            pos = 0
        target[i] = pos
    return target


def target_ema_cross(bars: Sequence[Bar], fast: int, slow: int) -> list[int]:
    """Trend: long zolang de snelle EMA boven de trage ligt."""
    closes = [b[4] for b in bars]
    f, s = ema(closes, fast), ema(closes, slow)
    return [
        1 if f[i] is not None and s[i] is not None and f[i] > s[i] else 0
        for i in range(len(bars))
    ]


def target_rsi_dip(
    bars: Sequence[Bar], rsi_n: int, low: float, high: float, trend_n: int, max_hold: int
) -> list[int]:
    """Mean-reversion: koop een dip (RSI < low) in een opwaartse trend
    (slot > SMA(trend_n)); verkoop bij herstel (RSI > high) of na max_hold bars."""
    closes = [b[4] for b in bars]
    r, t = rsi(closes, rsi_n), sma(closes, trend_n)
    target = [0] * len(bars)
    pos = held = 0
    for i in range(len(bars)):
        if r[i] is None or t[i] is None:
            continue
        if not pos:
            if closes[i] > t[i] and r[i] < low:
                pos, held = 1, 0
        else:
            held += 1
            if r[i] > high or held >= max_hold:
                pos = 0
        target[i] = pos
    return target


def target_donchian_regime(
    bars: Sequence[Bar], entry_n: int, exit_n: int, regime: Callable[[int], bool], tf_ms: int
) -> list[int]:
    """Donchian-uitbraak, maar alleen instappen als de markt als geheel meewerkt
    (regime(t) = BTC boven zijn 200-daags gemiddelde op tijdstip t)."""
    highs = [b[2] for b in bars]
    lows = [b[3] for b in bars]
    target = [0] * len(bars)
    pos = 0
    for i in range(len(bars)):
        close = bars[i][4]
        if not pos and i >= entry_n and close > max(highs[i - entry_n : i]) and regime(bars[i][0] + tf_ms):
            pos = 1
        elif pos and i >= exit_n and close < min(lows[i - exit_n : i]):
            pos = 0
        target[i] = pos
    return target


def target_vol_breakout(bars: Sequence[Bar], n: int, k: float, m: float) -> list[int]:
    """Volatiliteitsuitbraak: koop als de slotkoers meer dan k x de gemiddelde
    beweging (over n bars) stijgt; verkoop onder de top sinds instap min m x die beweging."""
    closes = [b[4] for b in bars]
    moves = [0.0] + [abs(closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    avg = sma(moves, n)
    target = [0] * len(bars)
    pos = 0
    peak = 0.0
    for i in range(1, len(bars)):
        a_prev, a_now = avg[i - 1], avg[i]
        if a_prev is None or a_now is None:
            continue
        if not pos:
            if closes[i] - closes[i - 1] > k * a_prev:
                pos, peak = 1, closes[i]
        else:
            peak = max(peak, closes[i])
            if closes[i] < peak - m * a_now:
                pos = 0
        target[i] = pos
    return target


def target_donchian_short(
    bars: Sequence[Bar], entry_n: int, exit_n: int, regime: Callable[[int], bool], tf_ms: int
) -> list[int]:
    """Short: instappen bij een uitbraak onder de laagste low van entry_n bars, alleen
    als de markt als geheel zwak is (regime uit); dekken boven de hoogste high van exit_n."""
    highs = [b[2] for b in bars]
    lows = [b[3] for b in bars]
    target = [0] * len(bars)
    pos = 0
    for i in range(len(bars)):
        close = bars[i][4]
        if not pos and i >= entry_n and close < min(lows[i - entry_n : i]) and not regime(bars[i][0] + tf_ms):
            pos = 1
        elif pos and i >= exit_n and close > max(highs[i - exit_n : i]):
            pos = 0
        target[i] = pos
    return target


def target_ema_cross_short(bars: Sequence[Bar], fast: int, slow: int, regime: Callable[[int], bool], tf_ms: int) -> list[int]:
    """Short zolang de snelle EMA onder de trage ligt én de markt als geheel zwak is."""
    closes = [b[4] for b in bars]
    f, s_ = ema(closes, fast), ema(closes, slow)
    return [
        1 if f[i] is not None and s_[i] is not None and f[i] < s_[i] and not regime(bars[i][0] + tf_ms) else 0
        for i in range(len(bars))
    ]


def target_rel_strength(
    bars: Sequence[Bar],
    lookback: int,
    selected: Callable[[int, int], bool],
    regime: Callable[[int], bool],
    tf_ms: int,
) -> list[int]:
    """Relatieve sterkte: long zolang deze markt bij de sterkste hoort (wekelijkse
    ranglijst over lookback dagen) en de markt als geheel meewerkt."""
    out = []
    for b in bars:
        t = b[0] + tf_ms
        out.append(1 if regime(t) and selected(lookback, t) else 0)
    return out


DAY_MS_F = 86_400_000.0


def simulate_signals(bars: Sequence[Bar], target: Sequence[int], costs: Costs, side: int = 1) -> list[Trade]:
    """Voer een 0/1-doelpositie uit met marktorders op de volgende open.

    side=1: long (spot). side=-1: short op margin: verkopen bij instap, terugkopen
    bij uitstap, met leenkosten over de looptijd."""
    trades: list[Trade] = []
    fee, slip = costs.taker, costs.slippage
    pos = False
    raw_in = 0.0
    entry_ts = 0
    worst = 0.0
    last = len(bars) - 1

    def close(raw_out: float, exit_ts: int) -> None:
        if side > 0:
            net = _net(raw_in * (1 + slip), raw_out * (1 - slip), fee, fee)
            gross = raw_out / raw_in - 1
            mae = max(0.0, 1 - worst / raw_in)
            trades.append(Trade(entry_ts, exit_ts, net, 1.0, "", 1, gross, gross - net, mae))
        else:
            sell, buy = raw_in * (1 - slip), raw_out * (1 + slip)
            traded = (sell * (1 - fee) - buy * (1 + fee)) / raw_in
            gross = 1 - raw_out / raw_in
            borrow = costs.borrow_daily * (exit_ts - entry_ts) / DAY_MS_F
            mae = max(0.0, worst / raw_in - 1)
            trades.append(Trade(entry_ts, exit_ts, traded - borrow, 1.0, "", -1, gross, gross - traded, mae))

    for i in range(last):
        want = target[i]
        nxt = bars[i + 1]
        if pos:
            worst = min(worst, bars[i][3]) if side > 0 else max(worst, bars[i][2])
        if want and not pos:
            pos, raw_in, entry_ts, worst = True, nxt[1], nxt[0], nxt[1]
        elif not want and pos:
            close(nxt[1], nxt[0])
            pos = False
    if pos:  # open positie afrekenen op de laatste slotkoers
        worst = min(worst, bars[last][3]) if side > 0 else max(worst, bars[last][2])
        close(bars[last][4], bars[last][0])
    return trades


def _net(entry_px: float, exit_px: float, fee_in: float, fee_out: float) -> float:
    return (exit_px * (1 - fee_out)) / (entry_px * (1 + fee_in)) - 1


# ------------------------------------------------------------------- grid


def simulate_grid(
    bars: Sequence[Bar], ref_n: int, step: float, levels: int, max_hold: int, costs: Costs
) -> list[Trade]:
    """Grid rond een voortschrijdend gemiddelde.

    Kooplimieten op ref*(1 - k*step) voor k=1..levels (ref = SMA t/m vorige bar),
    elk lot verkoopt op koopprijs*(1+step). Limietorders = maker-fee. Een lot dat
    na max_hold bars niet verkocht is gaat op de slotkoers weg (taker + slippage).
    """
    closes = [b[4] for b in bars]
    ref = sma(closes, ref_n)
    weight = 1.0 / levels
    open_lots: dict[int, list] = {}  # level -> [koopprijs, ts, bar-index, laagste koers sindsdien]
    trades: list[Trade] = []

    def done(lot: list, exit_ts: int, raw_out: float, net: float) -> None:
        gross = raw_out / lot[0] - 1
        trades.append(Trade(lot[1], exit_ts, net, weight, "", 1, gross, gross - net, max(0.0, 1 - lot[3] / lot[0])))

    for i in range(1, len(bars)):
        ts, o, h, l, c, _ = bars[i]
        # eerst verkopen (alleen lots uit eerdere bars)
        sold: set[int] = set()
        for k in list(open_lots):
            lot = open_lots[k]
            buy_px, buy_i = lot[0], lot[2]
            lot[3] = min(lot[3], l)
            sell_px = buy_px * (1 + step)
            if h > sell_px:
                done(lot, ts, sell_px, _net(buy_px, sell_px, costs.maker, costs.maker))
                del open_lots[k]
                sold.add(k)
            elif i - buy_i >= max_hold:
                done(lot, ts, c, _net(buy_px, c * (1 - costs.slippage), costs.maker, costs.taker))
                del open_lots[k]
                sold.add(k)
        r = ref[i - 1]
        if r is None:
            continue
        for k in range(1, levels + 1):
            if k in open_lots or k in sold:
                continue  # een niveau wordt niet in dezelfde bar verkocht én opnieuw gekocht
            level_px = r * (1 - k * step)
            if l < level_px:
                px = min(level_px, o)
                open_lots[k] = [px, ts, i, min(px, l)]
    last = bars[-1]
    for lot in open_lots.values():
        done(lot, last[0], last[4], _net(lot[0], last[4] * (1 - costs.slippage), costs.maker, costs.taker))
    trades.sort(key=lambda t: t.entry_ts)
    return trades


def leveraged_return(t: Trade, leverage: float, costs: Costs, maintenance: float = 0.05, liq_fee: float = 0.02) -> tuple[float, bool]:
    """Rendement op de eigen inleg als deze trade met hefboom was gedaan.

    Geeft (rendement, geliquideerd). Aannames (Bitvavo publiceert niet alles):
    liquidatie zodra de koers (1/hefboom - maintenance) tegen je in gaat, 2%
    liquidatiekosten over de hele positie, leenkosten per dag over het geleende deel."""
    days = (t.exit_ts - t.entry_ts) / DAY_MS_F
    borrowed = (leverage - 1) if t.side > 0 else leverage
    if leverage > 1 or t.side < 0:
        liq_move = 1 / leverage - maintenance
        if liq_move > 0 and t.mae >= liq_move:
            return -min(1.0, leverage * (liq_move + liq_fee + t.cost / 2)), True
    return leverage * (t.gross - t.cost) - costs.borrow_daily * days * borrowed, False


def leverage_table(trades: Sequence[Trade], costs: Costs, levels: Sequence[float] = (1, 2, 3, 5)) -> list[dict]:
    """Wat dezelfde trades met hefboom hadden gedaan (op de eigen inleg)."""
    rows = []
    ordered = sorted(trades, key=lambda t: t.exit_ts)
    for lev in levels:
        rets, liqs, total, peak, dd = [], 0, 0.0, 0.0, 0.0
        for t in ordered:
            r, liq = leveraged_return(t, lev, costs)
            rets.append(r)
            liqs += liq
            total += r * t.weight
            peak = max(peak, total)
            dd = max(dd, peak - total)
        n = len(rets)
        rows.append(
            {
                "hefboom": lev,
                "trades": n,
                "gem_pct": round(100 * sum(rets) / n, 3) if n else None,
                "slechtste_pct": round(100 * min(rets), 1) if n else None,
                "verlies_trades_pct": round(100 * sum(1 for r in rets if r < 0) / n, 1) if n else None,
                "liquidaties": liqs,
                "totaal_pct": round(100 * total, 1),
                "max_terugval_pct": round(100 * dd, 1),
            }
        )
    return rows


# ----------------------------------------------------- vastgelegde hypotheses

Runner = Callable[[Sequence[Bar], Costs], list[Trade]]

TARGETS = {
    "donchian": target_donchian,
    "ema_cross": target_ema_cross,
    "rsi_dip": target_rsi_dip,
}


def param_specs() -> dict[str, dict[str, dict]]:
    """De vooraf vastgelegde parameterruimte (zie docs/PREREGISTRATIE.md).

    Wijzigen = nieuwe preregistratie-versie; niet stilletjes bijstellen.
    """
    return {
        "donchian": {
            f"in{e}/uit{x}": {"entry_n": e, "exit_n": x} for e in (20, 55, 100) for x in (10, 20)
        },
        "ema_cross": {
            f"{f}/{s}": {"fast": f, "slow": s} for f, s in ((10, 30), (20, 50), (20, 100), (50, 200))
        },
        "rsi_dip": {
            f"rsi{n}<{lo} trend{t}": {"rsi_n": n, "low": lo, "high": 60.0, "trend_n": t, "max_hold": 10}
            for n, lo in ((2, 10.0), (3, 15.0), (5, 25.0))
            for t in (100, 200)
        },
        "grid": {
            f"stap{int(s * 1000) / 10}% x{lv}": {"ref_n": 50, "step": s, "levels": lv, "max_hold": 200}
            for s in (0.02, 0.03, 0.05)
            for lv in (3, 5)
        },
        # ---- preregistratie v2 ----
        "rel_strength": {f"{n} dagen": {"lookback": n} for n in (30, 60, 90)},
        "donchian_regime": {
            f"in{e}/uit{x}": {"entry_n": e, "exit_n": x} for e in (20, 55, 100) for x in (10, 20)
        },
        "vol_breakout": {
            f"k{k} stop{m}": {"n": 20, "k": k, "m": float(m)} for k in (1.5, 2.0, 2.5) for m in (2, 3)
        },
        # ---- preregistratie v3: short op margin (alleen gesimuleerd) ----
        "donchian_short": {
            f"in{e}/uit{x}": {"entry_n": e, "exit_n": x} for e in (20, 55, 100) for x in (10, 20)
        },
        "ema_cross_short": {
            f"{f}/{s}": {"fast": f, "slow": s} for f, s in ((10, 30), (20, 50), (20, 100), (50, 200))
        },
    }


# (strategie, tijdschalen, gepoold kiezen, preregistratie-versie)
HYPOTHESES: tuple[tuple[str, tuple[str, ...], bool, str], ...] = (
    ("donchian", ("1h", "4h", "1d"), False, "v1"),
    ("ema_cross", ("1h", "4h", "1d"), False, "v1"),
    ("rsi_dip", ("1h", "4h", "1d"), False, "v1"),
    ("grid", ("1h", "4h", "1d"), False, "v1"),
    ("rel_strength", ("1d",), True, "v2"),
    ("donchian_regime", ("4h", "1d"), False, "v2"),
    ("vol_breakout", ("4h", "1d"), False, "v2"),
    ("donchian_short", ("4h", "1d"), False, "v3"),
    ("ema_cross_short", ("4h", "1d"), False, "v3"),
)
POOLED = {h[0] for h in HYPOTHESES if h[2]}
SHORT = {"donchian_short", "ema_cross_short"}  # alleen te simuleren: Bitvavo biedt margin niet via de API


def compute_target(strategy: str, kwargs: dict, market: str, bars: Sequence[Bar], ctx, tf_ms: int) -> list[int]:
    """Doelpositie per bar. Eén functie voor backtest én live, zodat ze nooit uiteenlopen.
    ctx levert wat over markten heen nodig is (marktregime, ranglijst)."""
    if strategy == "donchian_regime":
        return target_donchian_regime(bars, regime=ctx.regime_at, tf_ms=tf_ms, **kwargs)
    if strategy == "donchian_short":
        return target_donchian_short(bars, regime=ctx.regime_at, tf_ms=tf_ms, **kwargs)
    if strategy == "ema_cross_short":
        return target_ema_cross_short(bars, regime=ctx.regime_at, tf_ms=tf_ms, **kwargs)
    if strategy == "vol_breakout":
        return target_vol_breakout(bars, **kwargs)
    if strategy == "rel_strength":
        return target_rel_strength(
            bars, kwargs["lookback"], lambda n, t: ctx.is_selected(market, n, t), ctx.regime_at, tf_ms
        )
    return TARGETS[strategy](bars, **kwargs)


def run_strategy(
    strategy: str, kwargs: dict, market: str, bars: Sequence[Bar], ctx, tf_ms: int, costs: Costs
) -> list[Trade]:
    if strategy == "grid":
        trades = simulate_grid(bars, costs=costs, **kwargs)
    else:
        side = -1 if strategy in SHORT else 1
        trades = simulate_signals(bars, compute_target(strategy, kwargs, market, bars, ctx, tf_ms), costs, side)
    return [replace(t, market=market) for t in trades]


def live_target(strategy: str, kwargs: dict, bars: Sequence[Bar]) -> list[int]:
    """Doelpositie voor de v1-signaalstrategieën (geen context nodig)."""
    return TARGETS[strategy](bars, **kwargs)


STRATEGY_LABELS = {
    "donchian": "Trend – Donchian-uitbraak",
    "ema_cross": "Trend – EMA-kruising",
    "rsi_dip": "Mean-reversion – RSI-dip",
    "grid": "Grid rond gemiddelde",
    "rel_strength": "Relatieve sterkte (top 3, wekelijks)",
    "donchian_regime": "Trend – Donchian met marktfilter",
    "vol_breakout": "Volatiliteitsuitbraak",
    "donchian_short": "Short – Donchian-uitbraak omlaag",
    "ema_cross_short": "Short – EMA-kruising omlaag",
}
