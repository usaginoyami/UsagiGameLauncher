"""Estrelas dos repositórios no GitHub, guardadas em disco para aparecerem na hora."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from .jsonio import load_json, save_json

STALE_AFTER = 24 * 3600


def format_stars(n: int) -> str:
    """1234 -> '1,2 mil'; 56 -> '56'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} mi".replace(".", ",")
    if n >= 1000:
        return f"{n / 1000:.1f} mil".replace(".", ",").replace(",0 mil", " mil")
    return str(n)


class StarCache:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        raw = load_json(self.path, {})
        self._d: dict[str, dict] = raw if isinstance(raw, dict) else {}
        self._lock = threading.Lock()

    def get(self, key: str) -> int | None:
        v = self._d.get(key.lower())
        return int(v["stars"]) if isinstance(v, dict) and "stars" in v else None

    def set(self, key: str, stars: int) -> None:
        with self._lock:
            self._d[key.lower()] = {"stars": int(stars), "ts": time.time()}

    def is_stale(self, key: str) -> bool:
        v = self._d.get(key.lower())
        return not isinstance(v, dict) or time.time() - float(v.get("ts", 0)) > STALE_AFTER

    def save(self) -> None:
        with self._lock:
            save_json(self.path, self._d)
