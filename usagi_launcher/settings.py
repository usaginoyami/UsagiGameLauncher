from __future__ import annotations

from pathlib import Path
from typing import Any

from . import paths, vault
from .jsonio import load_json, save_json

# valores que não ficam no settings.json quando o cofre do sistema está disponível
SECRET_KEYS = ("github_token", "igdb_client_secret", "steamgriddb_key")

DEFAULTS: dict[str, Any] = {
    "github_token": "",
    "install_dir": "",  # vazio = ~/Games/UsagiGameLauncher
    "include_prereleases": True,
    "auto_discover": True,
    "discover_topics": ["recomp", "recompiled", "recompilation", "static-recompilation"],
    "discover_min_stars": 10,
    "discover_hours": 24,
    "covers_enabled": True,
    "steamgriddb_key": "",
    "igdb_client_id": "",
    "igdb_client_secret": "",
    "minimize_on_launch": False,
    "show_adult": False,
    "safety_confirm": True,
    "trusted": [],
    "backup_saves": True,
    "check_self_update": True,
    "self_update_checked": 0,
    "hidden": [],
    "sort_mode": "default",
    "last_backup_dir": "",
    "notify_updates": True,
    "update_check_hours": 6,
    "notified_updates": {},
    "custom_names": {},
    "custom_platforms": {},
    "favorites": [],
    "game_meta": {},
}


class Settings:
    def __init__(self, path: Path | None = None, backend: "vault.Backend | None | str" = "auto"):
        self.path = Path(path) if path else paths.data_dir() / "settings.json"
        self.vault = vault.default_backend() if backend == "auto" else backend
        stored = load_json(self.path, {})
        self._data: dict[str, Any] = {**DEFAULTS, **(stored if isinstance(stored, dict) else {})}
        if self.vault is not None:
            for key in SECRET_KEYS:
                if not self._data.get(key):  # o que sobrou em texto puro é migrado no próximo save()
                    try:
                        self._data[key] = self.vault.get(key) or ""
                    except Exception:
                        self._data[key] = ""

    def get(self, key: str) -> Any:
        return self._data.get(key, DEFAULTS.get(key))

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def update(self, values: dict[str, Any]) -> None:
        self._data.update(values)

    def save(self) -> None:
        data = dict(self._data)
        if self.vault is not None:
            for key in SECRET_KEYS:
                value = str(data.get(key) or "")
                try:
                    ok = self.vault.set(key, value) if value else self.vault.delete(key) or True
                except Exception:
                    ok = False
                if ok:
                    data[key] = ""  # guardado no cofre: o arquivo não leva o segredo
        save_json(self.path, data)

    def secrets_protected(self) -> bool:
        return self.vault is not None

    def install_dir(self) -> Path:
        raw = (self.get("install_dir") or "").strip()
        return Path(raw) if raw else paths.default_install_dir()

    # --- listas por chave (favoritos / ocultos) ---
    def has(self, key: str, value: str) -> bool:
        return value in self._data.get(key, [])

    def toggle(self, key: str, value: str) -> bool:
        items = list(self._data.get(key, []))
        if value in items:
            items.remove(value)
            state = False
        else:
            items.append(value)
            state = True
        self._data[key] = items
        return state

    def add_to(self, key: str, value: str) -> None:
        items = list(self._data.get(key, []))
        if value not in items:
            items.append(value)
        self._data[key] = items
