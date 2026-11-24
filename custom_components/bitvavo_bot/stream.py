"""Directe koersstroom van Bitvavo (WebSocket): koersen per transactie en de lopende uurcandle.

Alleen om te tonen en om paper-limietorders sneller als gevuld te herkennen.
Handelsbeslissingen blijven op afgesloten candles, zoals in de backtest.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Callable

import aiohttp

_LOGGER = logging.getLogger(__name__)

WS_URL = "wss://ws.bitvavo.com/v2/"
FRESH_SECONDS = 30


class BitvavoStream:
    def __init__(self, session: aiohttp.ClientSession, markets: Callable[[], list[str]], prices: dict[str, float]) -> None:
        self._session = session
        self._markets = markets
        self.prices = prices
        self.forming: dict[str, list[float]] = {}  # markt -> lopende uurcandle [ts, o, h, l, c, v]
        self.extremes: dict[str, list[float]] = {}  # markt -> [laagste, hoogste] sinds de laatste reset
        self.connected = False
        self.last_msg = 0.0
        self.version = 0  # loopt op bij elke koerswijziging

    @property
    def fresh(self) -> bool:
        return self.connected and time.time() - self.last_msg < FRESH_SECONDS

    def reset_extremes(self) -> None:
        self.extremes.clear()

    def _price(self, market: str, px: float) -> None:
        if px <= 0:
            return
        if self.prices.get(market) != px:
            self.prices[market] = px
            self.version += 1
        ext = self.extremes.get(market)
        if ext is None:
            self.extremes[market] = [px, px]
        else:
            ext[0], ext[1] = min(ext[0], px), max(ext[1], px)

    def handle(self, data: dict) -> None:
        self.last_msg = time.time()
        event = data.get("event")
        market = data.get("market")
        try:
            if event == "ticker" and market and data.get("lastPrice") is not None:
                self._price(market, float(data["lastPrice"]))
            elif event in ("candle", "candles") and market and data.get("interval") == "1h":
                rows = data.get("candle") or []
                if rows:
                    row = [int(rows[-1][0]), *(float(x) for x in rows[-1][1:6])]
                    if self.forming.get(market) != row:
                        self.forming[market] = row
                        self.version += 1
                    self._price(market, row[4])
            elif "errorCode" in data:
                _LOGGER.warning("Bitvavo-koersstroom meldt een fout: %s", data.get("error"))
        except (TypeError, ValueError, IndexError):
            _LOGGER.debug("Onleesbaar streambericht: %s", data)

    async def run(self) -> None:
        """Blijft verbinden zolang de integratie draait; herverbindt met oplopende pauze."""
        backoff = 5
        while True:
            markets = list(self._markets())
            if not markets:
                await asyncio.sleep(5)
                continue
            try:
                async with self._session.ws_connect(WS_URL, heartbeat=30) as ws:
                    await ws.send_json(
                        {
                            "action": "subscribe",
                            "channels": [
                                {"name": "ticker", "markets": markets},
                                {"name": "candles", "markets": markets, "interval": ["1h"]},
                            ],
                        }
                    )
                    self.connected, backoff = True, 5
                    self.last_msg = time.time()
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                self.handle(json.loads(msg.data))
                            except ValueError:
                                continue
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
                        if set(self._markets()) != set(markets):
                            break  # marktenlijst gewijzigd: opnieuw abonneren
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - de stroom mag nooit de integratie raken
                _LOGGER.debug("Koersstroom verbroken: %s", err)
            finally:
                self.connected = False
            await asyncio.sleep(backoff)
            backoff = min(60, backoff * 2)
