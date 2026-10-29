"""Tempo de jogo, status e nota pessoal de cada jogo."""
from __future__ import annotations

import threading
import time
from typing import Callable

STATUSES: list[tuple[str, str]] = [
    ("", "Sem status"),
    ("want", "Quero jogar"),
    ("playing", "Jogando"),
    ("done", "Terminado"),
    ("dropped", "Abandonado"),
]
STATUS_LABEL = dict(STATUSES)
MIN_SESSION = 10  # sessões de menos de 10 s (jogo que fechou na hora) não contam
MAX_NOTE = 2000


def fmt_playtime(seconds: float) -> str:
    seconds = int(seconds or 0)
    if seconds < 60:
        return "menos de 1 min" if seconds else "0 min"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    h, m = divmod(minutes, 60)
    return f"{h} h {m:02d} min" if m else f"{h} h"


# ----------------------------------------------------------------- status e nota
def get_meta(settings, key: str) -> dict:
    meta = (settings.get("game_meta") or {}).get(key.lower())
    return dict(meta) if isinstance(meta, dict) else {}


def set_meta(settings, key: str, status: str | None = None, note: str | None = None) -> None:
    all_meta = dict(settings.get("game_meta") or {})
    cur = dict(all_meta.get(key.lower()) or {})
    if status is not None:
        cur["status"] = status if status in STATUS_LABEL else ""
    if note is not None:
        cur["note"] = note.strip()[:MAX_NOTE]
    cur = {k: v for k, v in cur.items() if v}
    if cur:
        all_meta[key.lower()] = cur
    else:
        all_meta.pop(key.lower(), None)
    settings.set("game_meta", all_meta)


def status_of(settings, key: str) -> str:
    return get_meta(settings, key).get("status", "")


def note_of(settings, key: str) -> str:
    return get_meta(settings, key).get("note", "")


# ----------------------------------------------------------------- tempo de jogo
class PlayTracker:
    """Conta quanto tempo cada processo de jogo ficou aberto.

    `on_finish(repo, segundos)` é chamado numa thread de fundo quando o jogo fecha. Ao sair do
    launcher, `stop_all()` credita o que já passou dos jogos ainda abertos."""

    def __init__(self, on_finish: Callable[[str, float], None], clock=time.time):
        self.on_finish = on_finish
        self.clock = clock
        self._lock = threading.Lock()
        self._running: dict[str, tuple[object, float]] = {}

    def track(self, repo: str, proc) -> bool:
        if proc is None:  # aberto com elevação (UAC): não há processo para acompanhar
            return False
        started = self.clock()
        with self._lock:
            self._running[repo] = (proc, started)
        threading.Thread(target=self._wait, args=(repo, proc, started), daemon=True).start()
        return True

    def _wait(self, repo: str, proc, started: float) -> None:
        try:
            proc.wait()
        except Exception:
            return
        with self._lock:
            current = self._running.get(repo)
            if not current or current[0] is not proc:
                return  # já creditado por stop_all
            del self._running[repo]
        elapsed = self.clock() - started
        if elapsed >= MIN_SESSION:
            self.on_finish(repo, elapsed)

    def is_running(self, repo: str) -> bool:
        with self._lock:
            return repo in self._running

    def stop_all(self) -> dict[str, float]:
        with self._lock:
            running, self._running = self._running, {}
        now = self.clock()
        return {repo: now - started for repo, (_p, started) in running.items()
                if now - started >= MIN_SESSION}
