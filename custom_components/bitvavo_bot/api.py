"""Publieke Bitvavo REST-client (alleen marktdata; v0.1 plaatst geen orders)."""

from __future__ import annotations

import asyncio
import time

import aiohttp

from .const import API_BASE, CANDLE_INTERVAL, CANDLE_LIMIT, CANDLE_MS, EXCLUDED_BASES


class BitvavoError(Exception):
    """Fout bij het ophalen van Bitvavo-data."""


class BitvavoPublicClient:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def _get(self, path: str, params: dict | None = None):
        for attempt in range(4):
            try:
                async with self._session.get(
                    f"{API_BASE}{path}", params=params, timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    remaining = resp.headers.get("bitvavo-ratelimit-remaining")
                    reset_at = resp.headers.get("bitvavo-ratelimit-resetat")
                    if resp.status == 429 or (remaining is not None and int(remaining) < 50):
                        wait = 10.0
                        if reset_at:
                            wait = max(1.0, min(65.0, int(reset_at) / 1000 - time.time() + 1))
                        await asyncio.sleep(wait)
                        if resp.status == 429:
                            continue
                    if resp.status >= 500:
                        await asyncio.sleep(2 * (attempt + 1))
                        continue
                    if resp.status != 200:
                        raise BitvavoError(f"{path}: HTTP {resp.status} {await resp.text()}")
                    return await resp.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                if attempt == 3:
                    raise BitvavoError(f"{path}: {err}") from err
                await asyncio.sleep(2 * (attempt + 1))
        raise BitvavoError(f"{path}: geen antwoord na herhaalde pogingen")

    async def top_eur_markets(self, count: int) -> list[str]:
        """De meest verhandelde EUR-markten (24u-omzet), zonder stablecoins."""
        markets, tickers = await asyncio.gather(self._get("/markets"), self._get("/ticker/24h"))
        return select_markets(markets, tickers, count)

    async def candles(self, market: str, since_ms: int, until_ms: int) -> list[list[float]]:
        """Afgesloten uurcandles in [since_ms, until_ms), oplopend."""
        since_ms -= since_ms % CANDLE_MS
        end = until_ms - until_ms % CANDLE_MS
        rows: dict[int, list[float]] = {}
        while end > since_ms:
            batch = await self._get(
                f"/{market}/candles",
                {"interval": CANDLE_INTERVAL, "limit": CANDLE_LIMIT, "start": since_ms, "end": end},
            )
            parsed = parse_candles(batch)
            fresh = [r for r in parsed if since_ms <= r[0] < until_ms - until_ms % CANDLE_MS]
            if not fresh:
                break
            for r in fresh:
                rows[int(r[0])] = r
            oldest = int(min(r[0] for r in parsed))
            if oldest >= end or len(batch) < CANDLE_LIMIT:
                break
            end = oldest
            await asyncio.sleep(0.1)
        return [rows[k] for k in sorted(rows)]


def parse_candles(batch) -> list[list[float]]:
    """Bitvavo geeft [ts, open, high, low, close, volume] met strings, nieuwste eerst."""
    out = []
    for row in batch or []:
        if isinstance(row, dict):
            row = [row.get(k) for k in ("timestamp", "open", "high", "low", "close", "volume")]
        try:
            out.append([int(row[0]), *(float(x) for x in row[1:6])])
        except (TypeError, ValueError, IndexError):
            continue
    return out


def select_markets(markets, tickers, count: int) -> list[str]:
    tradable = {
        m["market"]
        for m in markets
        if m.get("quote") == "EUR"
        and m.get("status") == "trading"
        and m.get("base") not in EXCLUDED_BASES
    }
    ranked = []
    for t in tickers:
        if t.get("market") in tradable:
            try:
                ranked.append((float(t.get("volumeQuote") or 0), t["market"]))
            except (TypeError, ValueError):
                continue
    ranked.sort(reverse=True)
    return [m for _, m in ranked[:count]]
