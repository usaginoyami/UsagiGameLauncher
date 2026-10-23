from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any


def load_json(path: str | os.PathLike, default: Any) -> Any:
    """Lê JSON; se o arquivo estiver corrompido, tenta o backup .bak."""
    path = Path(path)
    for candidate in (path, path.with_suffix(path.suffix + ".bak")):
        try:
            with open(candidate, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeDecodeError):
            continue
    return default


def save_json(path: str | os.PathLike, data: Any) -> None:
    """Grava JSON com substituição atômica e backup do arquivo anterior."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    if path.exists():
        try:
            shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        except OSError:
            pass
    os.replace(tmp, path)
