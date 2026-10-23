from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from . import paths
from .jsonio import load_json, save_json


class Library:
    """Jogos instalados. Um registro por repositório, com histórico de versões."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else paths.data_dir() / "library.json"
        self._lock = threading.Lock()
        raw = load_json(self.path, {})
        games = raw.get("games", {}) if isinstance(raw, dict) else {}
        self.games: dict[str, dict[str, Any]] = games if isinstance(games, dict) else {}

    def get(self, repo: str) -> dict | None:
        return self.games.get(repo.lower())

    def all(self) -> list[dict]:
        return list(self.games.values())

    def record_install(self, repo: str, name: str, version: str, folder: str,
                       exe: str | None, root: str) -> dict:
        key = repo.lower()
        with self._lock:
            rec = self.games.get(key) or {"repo": repo, "args": "", "versions": []}
            rec.update(name=name, version=version, folder=folder, exe=exe or "", root=root,
                       installed_at=datetime.now().isoformat(timespec="seconds"))
            versions = [v for v in rec.get("versions", []) if v.get("version") != version]
            versions.append({"version": version, "folder": folder, "exe": exe or ""})
            rec["versions"] = versions
            self.games[key] = rec
        self.save()
        return rec

    def add_existing(self, repo: str, name: str, folder: str, exe: str) -> dict:
        """Registra um jogo que já estava no disco (instalação manual)."""
        return self.record_install(repo, name, "manual", folder, exe, str(Path(folder).parent))

    def set_exe(self, repo: str, exe: str) -> None:
        rec = self.get(repo)
        if rec is not None:
            rec["exe"] = exe
            for v in rec.get("versions", []):
                if v.get("version") == rec.get("version"):
                    v["exe"] = exe
            self.save()

    def set_args(self, repo: str, args: str) -> None:
        rec = self.get(repo)
        if rec is not None:
            rec["args"] = args
            self.save()

    def mark_played(self, repo: str) -> None:
        rec = self.get(repo)
        if rec is not None:
            rec["last_played"] = datetime.now().isoformat(timespec="seconds")
            self.save()

    def drop_old_versions(self, repo: str) -> list[str]:
        """Remove do registro as versões antigas e devolve as pastas a apagar."""
        rec = self.get(repo)
        if rec is None:
            return []
        current = rec.get("version")
        old = [v["folder"] for v in rec.get("versions", []) if v.get("version") != current]
        rec["versions"] = [v for v in rec.get("versions", []) if v.get("version") == current]
        self.save()
        return old

    def remove(self, repo: str) -> None:
        with self._lock:
            self.games.pop(repo.lower(), None)
        self.save()

    def save(self) -> None:
        with self._lock:
            snapshot = {"version": 1, "games": dict(self.games)}
        save_json(self.path, snapshot)
