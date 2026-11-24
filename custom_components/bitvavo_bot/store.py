"""Opslag van candles en het Lab-rapport op schijf (config/bitvavo_bot/)."""

from __future__ import annotations

import gzip
import json
import os
from pathlib import Path


class Store:
    def __init__(self, base: str) -> None:
        self._base = Path(base)
        self._candles = self._base / "candles"

    def ensure(self) -> None:
        self._candles.mkdir(parents=True, exist_ok=True)

    def _path(self, market: str) -> Path:
        return self._candles / f"{market}_1h.json.gz"

    def load_candles(self, market: str) -> list[list[float]]:
        path = self._path(market)
        if not path.exists():
            return []
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError, EOFError):
            return []

    def save_candles(self, market: str, rows: list[list[float]]) -> None:
        self.ensure()
        self._atomic(self._path(market), rows, compress=True)

    def merge_candles(self, market: str, new_rows: list[list[float]]) -> list[list[float]]:
        merged = {int(r[0]): r for r in self.load_candles(market)}
        merged.update({int(r[0]): r for r in new_rows})
        rows = [merged[k] for k in sorted(merged)]
        self.save_candles(market, rows)
        return rows

    def load_json(self, name: str) -> dict | None:
        path = self._base / name
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def save_json(self, name: str, payload: dict) -> None:
        self.ensure()
        self._atomic(self._base / name, payload, compress=False)

    def load_report(self) -> dict | None:
        path = self._base / "lab_report.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def save_report(self, report: dict) -> None:
        self.ensure()
        self._atomic(self._base / "lab_report.json", report, compress=False)

    @staticmethod
    def _atomic(path: Path, payload, compress: bool) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        if compress:
            with gzip.open(tmp, "wt", encoding="utf-8") as fh:
                json.dump(payload, fh, separators=(",", ":"))
        else:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
