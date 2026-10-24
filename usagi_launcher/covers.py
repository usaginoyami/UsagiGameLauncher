"""Busca e cache de capas dos jogos.

Ordem das fontes:
  1. capa definida no catálogo (campo "cover": link ou arquivo local)
  2. SteamGridDB (só se houver chave de API nas Configurações)
  3. Wikipedia (imagem da página do jogo, sem chave)
  4. primeira imagem do README do repositório

A busca envia apenas o nome do jogo (ou o repositório, para ler o README).
A chave do SteamGridDB só vai para a API do SteamGridDB, nunca para os servidores de imagens.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Callable
from urllib.parse import quote

import requests

UA = "UsagiGameLauncher/1.0 (+https://github.com/usaginoyami/UsagiGameLauncher)"
WIKI_API = "https://en.wikipedia.org/w/api.php"
SGDB_API = "https://www.steamgriddb.com/api/v2"
MAX_BYTES = 8 * 1024 * 1024
RETRY_AFTER = 7 * 24 * 3600  # depois de uma busca sem resultado, espera 7 dias
MIN_SIMILARITY = 0.6
_STOP = {"the", "of", "a", "an", "and"}

_MD_IMG = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_HTML_IMG = re.compile(r"<img\b[^>]*?\bsrc=[\"']([^\"']+)[\"']", re.I)
_BADGE = re.compile(
    r"(shields\.io|badge|badgen|travis-ci|codecov|/actions/|\.svg(\?|$)|sponsor|"
    r"buymeacoffee|ko-fi|discord|patreon|paypal)", re.I)


def sniff(data: bytes) -> str | None:
    """Reconhece o formato pelo conteúdo, não pelo que o servidor diz."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return None


def clean_title(name: str) -> str:
    s = re.sub(r"\([^)]*\)", " ", name)
    s = re.sub(r"\b(recompiled|recompilation|recomp|port|pc)\b", " ", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" :-–")
    return s or name


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower())) - _STOP


def similarity(query: str, title: str) -> float:
    q = _tokens(query)
    if not q:
        return 0.0
    return len(q & _tokens(title)) / len(q)


# ------------------------------------------------------------------ fontes
def download_image(session, url: str) -> bytes | None:
    r = session.get(url, stream=True, timeout=20, headers={"User-Agent": UA})
    try:
        if not r.ok:
            return None
        length = r.headers.get("Content-Length")
        if length and length.isdigit() and int(length) > MAX_BYTES:
            return None
        buf = bytearray()
        for chunk in r.iter_content(64 * 1024):
            buf += chunk
            if len(buf) > MAX_BYTES:
                return None
    finally:
        r.close()
    data = bytes(buf)
    return data if sniff(data) else None


def find_wikipedia(session, title: str) -> str | None:
    r = session.get(WIKI_API, headers={"User-Agent": UA}, timeout=15, params={
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"{title} video game", "gsrlimit": 3,
        "prop": "pageimages", "piprop": "thumbnail", "pithumbsize": 600, "pilimit": 3,
    })
    if not r.ok:
        return None
    pages = ((r.json() or {}).get("query") or {}).get("pages") or {}
    for page in sorted(pages.values(), key=lambda p: p.get("index", 99)):
        thumb = (page.get("thumbnail") or {}).get("source")
        if thumb and similarity(title, page.get("title", "")) >= MIN_SIMILARITY:
            return thumb
    return None


def find_steamgriddb(session, key: str, title: str) -> str | None:
    headers = {"Authorization": f"Bearer {key}", "User-Agent": UA}
    r = session.get(f"{SGDB_API}/search/autocomplete/{quote(title, safe='')}",
                    headers=headers, timeout=15)
    if not r.ok:
        return None
    game_id = None
    for g in ((r.json() or {}).get("data") or [])[:5]:
        if similarity(title, g.get("name", "")) >= MIN_SIMILARITY:
            game_id = g.get("id")
            break
    if game_id is None:
        return None
    r = session.get(f"{SGDB_API}/grids/game/{game_id}", headers=headers, timeout=15,
                    params={"dimensions": "460x215,920x430", "types": "static", "nsfw": "false"})
    if not r.ok:
        return None
    items = (r.json() or {}).get("data") or []
    return items[0].get("url") if items else None


def readme_image_url(readme: str, repo: str) -> str | None:
    found = [(m.start(), m.group(1)) for m in _MD_IMG.finditer(readme)]
    found += [(m.start(), m.group(1)) for m in _HTML_IMG.finditer(readme)]
    for _pos, url in sorted(found):
        if _BADGE.search(url):
            continue
        if url.startswith("//"):
            url = "https:" + url
        if not url.lower().startswith("http"):
            rel = re.sub(r"^\./", "", url).lstrip("/")
            url = f"https://raw.githubusercontent.com/{repo}/HEAD/{rel}"
        return url
    return None


def find_readme_image(session, repo: str) -> str | None:
    # raw.githubusercontent.com não conta no limite da API do GitHub
    for name in ("README.md", "readme.md", "Readme.md"):
        r = session.get(f"https://raw.githubusercontent.com/{repo}/HEAD/{name}",
                        headers={"User-Agent": UA}, timeout=15)
        if r.ok:
            return readme_image_url(r.text[:300_000], repo)
    return None


# ------------------------------------------------------------------ buscador
class CoverFinder:
    def __init__(self, session=None, sgdb_key: str = "", enabled: bool = True):
        self.session = session or requests.Session()
        self.sgdb_key = (sgdb_key or "").strip()
        self.enabled = enabled

    def configure(self, sgdb_key: str, enabled: bool) -> None:
        self.sgdb_key = (sgdb_key or "").strip()
        self.enabled = bool(enabled)

    def find(self, entry) -> tuple[bytes | None, bool]:
        """Devolve (imagem, houve_erro_de_rede). Erro de rede não vira 'sem capa'."""
        if not self.enabled:
            return None, True
        net_error = False

        def attempt(fn: Callable):
            nonlocal net_error
            try:
                return fn()
            except requests.RequestException:
                net_error = True
            except (ValueError, KeyError, TypeError, AttributeError, OSError):
                pass
            return None

        title = entry.cover_title or clean_title(entry.name)
        if entry.cover:
            data = attempt(lambda: self._custom(entry.cover))
            if data:
                return data, False

        sources: list[Callable[[], str | None]] = []
        if self.sgdb_key:
            sources.append(lambda: find_steamgriddb(self.session, self.sgdb_key, title))
        sources.append(lambda: find_wikipedia(self.session, title))
        sources.append(lambda: find_readme_image(self.session, entry.repo))
        for source in sources:
            url = attempt(source)
            if url:
                data = attempt(lambda u=url: download_image(self.session, u))
                if data:
                    return data, False
        return None, net_error

    def _custom(self, value: str) -> bytes | None:
        if value.lower().startswith("http"):
            return download_image(self.session, value)
        data = Path(value).expanduser().read_bytes()
        return data if sniff(data) and len(data) <= MAX_BYTES else None


# --------------------------------------------------------------------- cache
class CoverStore:
    """Capas em disco: <pasta>/<dono>_<repo>.img; '.none' marca busca sem resultado."""

    def __init__(self, folder: str | os.PathLike, finder: CoverFinder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.finder = finder

    @property
    def enabled(self) -> bool:
        return self.finder.enabled

    def _base(self, key: str) -> Path:
        return self.folder / re.sub(r"[^a-z0-9._-]", "_", key.lower())

    def _img(self, key: str) -> Path:
        return self._base(key).with_suffix(".img")

    def _none(self, key: str) -> Path:
        return self._base(key).with_suffix(".none")

    def cached(self, key: str) -> Path | None:
        p = self._img(key)
        return p if p.is_file() else None

    def needs_fetch(self, key: str) -> bool:
        if self.cached(key):
            return False
        marker = self._none(key)
        try:
            return time.time() - marker.stat().st_mtime >= RETRY_AFTER
        except OSError:
            return True

    def _write(self, key: str, data: bytes) -> Path:
        dest = self._img(key)
        tmp = dest.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, dest)
        self._remove(self._none(key))
        return dest

    @staticmethod
    def _remove(path: Path) -> None:
        try:
            path.unlink()
        except OSError:
            pass

    def fetch(self, entry) -> Path | None:
        data, net_error = self.finder.find(entry)
        if data:
            return self._write(entry.key, data)
        if not net_error:  # sem rede não deve bloquear novas tentativas
            self._none(entry.key).touch()
        return None

    def set_custom(self, key: str, source: str | os.PathLike) -> Path:
        data = Path(source).read_bytes()
        if not sniff(data):
            raise ValueError("O arquivo escolhido não é uma imagem PNG, JPG, WEBP ou GIF.")
        if len(data) > MAX_BYTES:
            raise ValueError("Imagem grande demais (limite de 8 MB).")
        return self._write(key, data)

    def clear(self, key: str) -> None:
        self._remove(self._img(key))
        self._remove(self._none(key))

    def clear_failures(self) -> None:
        for p in self.folder.glob("*.none"):
            self._remove(p)
