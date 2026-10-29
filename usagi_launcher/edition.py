"""Duas edições do mesmo programa.

Curador (a sua): monta o catálogo, edita nomes, plataformas e capas, descobre projetos e publica.
Cliente (a de quem baixa): só recebe o catálogo que o curador publicou; instala, joga e atualiza.

A edição vem do arquivo data/edition.txt empacotado no .exe, ou da variável USAGI_EDITION
(útil para testar). Sem nenhum dos dois, é a edição do curador.
"""
from __future__ import annotations

import os

from . import paths

CURATOR, CLIENT = "curator", "client"
_override: str | None = None


def set_edition(value: str | None) -> None:
    global _override
    _override = value if value in (CURATOR, CLIENT) else None


def current() -> str:
    if _override:
        return _override
    env = os.environ.get("USAGI_EDITION", "").strip().lower()
    if env in (CURATOR, CLIENT):
        return env
    try:
        text = paths.resource_path("data/edition.txt").read_text(encoding="utf-8").strip().lower()
    except OSError:
        return CURATOR
    return CLIENT if text == CLIENT else CURATOR


def is_client() -> bool:
    return current() == CLIENT


def app_name() -> str:
    return "Usagi Game Client" if is_client() else "Usagi GameLaucher"


def exe_name() -> str:
    return "UsagiGameClient.exe" if is_client() else "UsagiGameLauncher.exe"


def data_folder() -> str:
    """Pasta de dados própria, para as duas edições conviverem no mesmo PC."""
    return "UsagiGameClient" if is_client() else "UsagiGameLauncher"


def asset_matches(name: str) -> bool:
    """O pacote de atualização desta edição (o zip do cliente tem 'Client' no nome)."""
    has = "client" in name.lower()
    return has if is_client() else not has
