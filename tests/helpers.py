import math
import random

H = 3_600_000


def make_bars(n, seed=1, drift=0.0, vol=0.01, start=1_700_000_000_000 // H * H, cycle=0.0):
    """Synthetische uurbars. drift=0 is een echte martingaal in prijs (dus zonder
    de verborgen +vol²/2-drift van een log-random-walk): daar hoort niets te verdienen."""
    rnd = random.Random(seed)
    drift -= vol * vol / 2
    bars, px = [], 100.0
    for i in range(n):
        o = px
        r = rnd.gauss(drift, vol) + (cycle * math.sin(i / 12) if cycle else 0.0)
        c = o * math.exp(r)
        h = max(o, c) * (1 + abs(rnd.gauss(0, vol / 3)))
        l = min(o, c) * (1 - abs(rnd.gauss(0, vol / 3)))
        bars.append((start + i * H, o, h, l, c, 10.0))
        px = c
    return bars
