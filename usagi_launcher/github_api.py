from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

import requests

from .errors import Cancelled, GitHubError, NotFoundError, RateLimitError
from .jsonio import load_json, save_json

API = "https://api.github.com"
USER_AGENT = "UsagiGameLauncher/1.0"
MAX_CACHE_ENTRIES = 400


@dataclass
class Asset:
    name: str
    size: int
    url: str
    digest: str = ""
    downloads: int = 0


@dataclass
class Release:
    tag: str
    name: str
    published_at: str
    prerelease: bool
    html_url: str
    body: str
    assets: list[Asset] = field(default_factory=list)


def parse_asset(d: dict) -> Asset:
    return Asset(
        name=d.get("name", ""),
        size=int(d.get("size") or 0),
        url=d.get("browser_download_url", ""),
        digest=d.get("digest") or "",
        downloads=int(d.get("download_count") or 0),
    )


def parse_release(d: dict) -> Release:
    return Release(
        tag=d.get("tag_name", ""),
        name=d.get("name") or d.get("tag_name", ""),
        published_at=d.get("published_at") or d.get("created_at") or "",
        prerelease=bool(d.get("prerelease")),
        html_url=d.get("html_url", ""),
        body=d.get("body") or "",
        assets=[parse_asset(a) for a in d.get("assets", [])],
    )


def pick_release(releases: list[dict], allow_prerelease: bool = True) -> Release | None:
    """Prefere a release estável mais recente; cai para pré-release se permitido."""
    live = [r for r in releases if not r.get("draft")]
    for r in live:
        if not r.get("prerelease"):
            return parse_release(r)
    if allow_prerelease:
        for r in live:
            return parse_release(r)
    return None


class GitHubClient:
    """Cliente mínimo da API do GitHub com cache por ETag.

    Respostas 304 não contam no limite da API, então atualizar a lista é barato.
    """

    def __init__(self, token: str = "", cache_path: str | os.PathLike | None = None,
                 session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.update({
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        })
        self.set_token(token)
        self.cache_path = Path(cache_path) if cache_path else None
        self._lock = threading.Lock()
        self._cache: dict[str, dict] = {}
        self.rate_remaining: int | None = None
        if self.cache_path:
            data = load_json(self.cache_path, {})
            if isinstance(data, dict):
                self._cache = data

    # ---------------------------------------------------------------- token
    def set_token(self, token: str) -> None:
        token = (token or "").strip()
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        else:
            self.session.headers.pop("Authorization", None)

    # ---------------------------------------------------------------- cache
    @staticmethod
    def _key(path: str, params: dict | None) -> str:
        return path + ("?" + urlencode(sorted(params.items())) if params else "")

    def flush(self) -> None:
        if not self.cache_path:
            return
        with self._lock:
            if len(self._cache) > MAX_CACHE_ENTRIES:
                keep = sorted(self._cache.items(), key=lambda kv: kv[1].get("ts", 0), reverse=True)
                self._cache = dict(keep[:MAX_CACHE_ENTRIES])
            snapshot = dict(self._cache)
        try:
            save_json(self.cache_path, snapshot)
        except OSError:
            pass

    # ------------------------------------------------------------- requests
    def get_json(self, path: str, params: dict | None = None) -> Any:
        key = self._key(path, params)
        with self._lock:
            entry = self._cache.get(key)
        headers = {}
        if entry and entry.get("etag"):
            headers["If-None-Match"] = entry["etag"]
        try:
            r = self.session.get(API + path, params=params, headers=headers, timeout=20)
        except requests.RequestException as e:
            if entry:  # offline: usa o último resultado conhecido
                return entry["data"]
            raise GitHubError(f"Sem conexão com o GitHub: {e}") from e

        remaining = r.headers.get("X-RateLimit-Remaining")
        if remaining is not None and remaining.isdigit():
            self.rate_remaining = int(remaining)

        if r.status_code == 304 and entry:
            entry["ts"] = time.time()
            return entry["data"]
        limited = r.status_code == 429 or (
            r.status_code == 403
            and (remaining == "0" or "rate limit" in r.text.lower())
        )
        if limited:
            if entry:
                return entry["data"]
            reset = r.headers.get("X-RateLimit-Reset", "0")
            raise RateLimitError(int(reset) if reset.isdigit() else 0)
        if r.status_code == 404:
            raise NotFoundError(f"Não encontrado: {path}")
        if not r.ok:
            raise GitHubError(f"GitHub respondeu {r.status_code} em {path}")

        data = r.json()
        etag = r.headers.get("ETag")
        if etag:
            with self._lock:
                self._cache[key] = {"etag": etag, "ts": time.time(), "data": data}
        return data

    # ------------------------------------------------------------ endpoints
    def repo_info(self, full_name: str) -> dict:
        return self.get_json(f"/repos/{full_name}")

    def latest_release(self, full_name: str, allow_prerelease: bool = True) -> Release | None:
        data = self.get_json(f"/repos/{full_name}/releases", {"per_page": 10})
        return pick_release(data, allow_prerelease)

    def release_by_tag(self, full_name: str, tag: str) -> Release | None:
        from urllib.parse import quote
        try:
            return parse_release(self.get_json(f"/repos/{full_name}/releases/tags/{quote(tag, safe='')}"))
        except NotFoundError:
            return None

    def search_repos(self, topic: str, min_stars: int = 10, per_page: int = 50) -> list[dict]:
        q = f"topic:{topic} stars:>={int(min_stars)} archived:false"
        data = self.get_json(
            "/search/repositories",
            {"q": q, "sort": "stars", "order": "desc", "per_page": per_page},
        )
        return data.get("items", [])

    # ------------------------------------------------------------- download
    def download(self, url: str, dest: str | os.PathLike,
                 progress: Callable[[int, int], None] | None = None,
                 cancel: threading.Event | None = None, expected_size: int = 0) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        try:
            with self.session.get(url, stream=True, timeout=30,
                                  headers={"Accept": "*/*"}) as r:
                if r.status_code == 404:
                    raise NotFoundError("Arquivo da release não encontrado.")
                if not r.ok:
                    raise GitHubError(f"Download falhou (HTTP {r.status_code}).")
                total = int(r.headers.get("Content-Length") or expected_size or 0)
                done = 0
                with open(part, "wb") as f:
                    for chunk in r.iter_content(256 * 1024):
                        if cancel is not None and cancel.is_set():
                            raise Cancelled()
                        if chunk:
                            f.write(chunk)
                            done += len(chunk)
                            if progress:
                                progress(done, total)
            os.replace(part, dest)
        except BaseException:
            try:
                part.unlink()
            except OSError:
                pass
            raise
        return dest
