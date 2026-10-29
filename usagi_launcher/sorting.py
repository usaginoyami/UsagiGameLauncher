"""Ordenações da lista de jogos."""
from __future__ import annotations

from typing import Callable

from . import catalog

MODES: list[tuple[str, str]] = [
    ("default", "Padrão (atualizações, instalados, favoritos)"),
    ("name_az", "Nome (A → Z)"),
    ("name_za", "Nome (Z → A)"),
    ("stars_desc", "Estrelas (mais primeiro)"),
    ("stars_asc", "Estrelas (menos primeiro)"),
    ("platform", "Plataforma"),
    ("release_new", "Release mais recente"),
    ("played", "Jogados recentemente"),
    ("installed_new", "Instalados recentemente"),
    ("playtime", "Tempo de jogo (mais primeiro)"),
    ("status", "Status (jogando, quero jogar…)"),
]
MODE_KEYS = [k for k, _ in MODES]
# modos cujo resultado muda quando estrelas ou releases chegam do GitHub
LIVE_MODES = {"default", "stars_desc", "stars_asc", "release_new"}


def _name(row) -> str:
    return row.entry.name.casefold()


def _desc_text(value: str) -> tuple:
    """Texto ISO de data em ordem decrescente; vazio vai para o fim."""
    return (0, tuple(-ord(c) for c in value)) if value else (1, ())


def sort_key(row, mode: str, is_fav: Callable[[str], bool]) -> tuple:
    e = row.entry
    if mode == "name_az":
        return (_name(row),)
    if mode == "name_za":
        return (tuple(-ord(c) for c in _name(row)),)
    if mode == "stars_desc":
        return (e.stars <= 0, -e.stars, _name(row))
    if mode == "stars_asc":
        return (e.stars <= 0, e.stars, _name(row))
    if mode == "platform":
        order = catalog.PLATFORMS.index(e.platform) if e.platform in catalog.PLATFORMS else 99
        return (order, e.platform.casefold(), _name(row))
    if mode == "release_new":
        pub = row.release.published_at if row.release else ""
        return (_desc_text(pub), _name(row))
    if mode == "played":
        played = (row.rec or {}).get("last_played", "") if row.rec else ""
        return (_desc_text(played), _name(row))
    if mode == "playtime":
        t = int((row.rec or {}).get("playtime", 0)) if row.rec else 0
        return (t <= 0, -t, _name(row))
    if mode == "status":
        order = {"playing": 0, "want": 1, "done": 2, "dropped": 3}
        return (order.get(getattr(row, "status", ""), 4), _name(row))
    if mode == "installed_new":
        inst = (row.rec or {}).get("installed_at", "") if row.rec else ""
        return (_desc_text(inst), _name(row))
    return (not row.has_update, not row.installed, not is_fav(e.key),
            e.source == "discovered", _name(row))


def sort_rows(rows: list, mode: str, is_fav: Callable[[str], bool]) -> list:
    if mode not in MODE_KEYS:
        mode = "default"
    return sorted(rows, key=lambda r: sort_key(r, mode, is_fav))
