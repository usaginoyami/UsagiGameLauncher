from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "UsagiGameLauncher"
_override: Path | None = None


def set_data_dir(path: str | os.PathLike | None) -> None:
    """Define uma pasta de dados alternativa (perfil separado, testes)."""
    global _override
    _override = Path(path) if path else None


def data_dir() -> Path:
    if _override is not None:
        base = _override
    elif os.environ.get("USAGI_DATA_DIR"):
        base = Path(os.environ["USAGI_DATA_DIR"])
    else:
        root = (
            os.environ.get("LOCALAPPDATA")
            or os.environ.get("XDG_DATA_HOME")
            or str(Path.home() / ".local" / "share")
        )
        base = Path(root) / APP_DIR_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def sub_dir(name: str) -> Path:
    p = data_dir() / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def default_install_dir() -> Path:
    return Path.home() / "Games" / APP_DIR_NAME


def resource_path(rel: str) -> Path:
    """Caminho de um recurso empacotado (funciona dentro do PyInstaller)."""
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return root / rel
