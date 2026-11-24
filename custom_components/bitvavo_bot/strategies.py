"""Strategieën en backtest-motor. Pure Python, geen Home Assistant-afhankelijkheden.

Regels die lookahead voorkomen:
- Signaalstrategieën beslissen op de slotkoers van bar i en handelen op de
  openingskoers van bar i+1.
- De grid plaatst limietorders op basis van data t/m bar i-1 en wordt alleen
  gevuld als bar i strikt door de prijs heen gaat.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

# Bar = (ts_ms, open, high, low, close, volume), oplopend gesorteerd.
Bar = tuple[int, float, float, float, float, float]


@dataclass(frozen=True)
class Costs:
    """Kosten per zijde, als fractie."""

    taker: float = 0.0025
    maker: float = 0.0015
    slippage: float = 0.0005


@dataclass(frozen=True)
class Trade:
    entry_ts: int
    exit_ts: int
    net: float  # netto rendement van deze trade na kosten (fractie)
    weight: float = 1.0  # deel van het kapitaal (grid: 1/levels)


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


def simulate_signals(bars: Sequence[Bar], target: Sequence[int], costs: Costs) -> list[Trade]:
    """Voer een 0/1-doelpositie uit met marktorders op de volgende open."""
    trades: list[Trade] = []
    fee, slip = costs.taker, costs.slippage
    pos = False
    entry_px = 0.0
    entry_ts = 0
    last = len(bars) - 1
    for i in range(last):
        want = target[i]
        nxt = bars[i + 1]
        if want and not pos:
            pos, entry_px, entry_ts = True, nxt[1] * (1 + slip), nxt[0]
        elif not want and pos:
            exit_px = nxt[1] * (1 - slip)
            trades.append(Trade(entry_ts, nxt[0], _net(entry_px, exit_px, fee, fee)))
            pos = False
    if pos:  # open positie afrekenen op de laatste slotkoers
        exit_px = bars[last][4] * (1 - slip)
        trades.append(Trade(entry_ts, bars[last][0], _net(entry_px, exit_px, fee, fee)))
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
    open_lots: dict[int, tuple[float, int, int]] = {}  # level -> (koopprijs, ts, bar-index)
    trades: list[Trade] = []
    for i in range(1, len(bars)):
        ts, o, h, l, c, _ = bars[i]
        # eerst verkopen (alleen lots uit eerdere bars)
        sold: set[int] = set()
        for k in list(open_lots):
            buy_px, buy_ts, buy_i = open_lots[k]
            sell_px = buy_px * (1 + step)
            if h > sell_px:
                trades.append(Trade(buy_ts, ts, _net(buy_px, sell_px, costs.maker, costs.maker), weight))
                del open_lots[k]
                sold.add(k)
            elif i - buy_i >= max_hold:
                exit_px = c * (1 - costs.slippage)
                trades.append(Trade(buy_ts, ts, _net(buy_px, exit_px, costs.maker, costs.taker), weight))
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
                open_lots[k] = (min(level_px, o), ts, i)
    last = bars[-1]
    for buy_px, buy_ts, _ in open_lots.values():
        exit_px = last[4] * (1 - costs.slippage)
        trades.append(Trade(buy_ts, last[0], _net(buy_px, exit_px, costs.maker, costs.taker), weight))
    trades.sort(key=lambda t: t.entry_ts)
    return trades


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
    }


def make_runner(strategy: str, kwargs: dict) -> Runner:
    if strategy == "grid":
        return lambda bars, costs: simulate_grid(bars, costs=costs, **kwargs)
    fn = TARGETS[strategy]
    return lambda bars, costs: simulate_signals(bars, fn(bars, **kwargs), costs)


def param_grid() -> dict[str, dict[str, Runner]]:
    return {
        strat: {name: make_runner(strat, kw) for name, kw in params.items()}
        for strat, params in param_specs().items()
    }


def live_target(strategy: str, kwargs: dict, bars: Sequence[Bar]) -> list[int]:
    """Doelpositie per bar voor een signaalstrategie (zelfde code als de backtest)."""
    return TARGETS[strategy](bars, **kwargs)


STRATEGY_LABELS = {
    "donchian": "Trend – Donchian-uitbraak",
    "ema_cross": "Trend – EMA-kruising",
    "rsi_dip": "Mean-reversion – RSI-dip",
    "grid": "Grid rond gemiddelde",
}
