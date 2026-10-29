"""Busca e cache de capas dos jogos.

Ordem das fontes:
  1. capa definida no catálogo (campo "cover": link ou arquivo local)
  2. IGDB (só com Client ID e Secret da Twitch), filtrando pela plataforma do jogo
  3. SteamGridDB (só se houver chave de API nas Configurações)
  4. Wikipedia (imagem da página do jogo, sem chave)
  5. primeira imagem do README do repositório

A busca envia apenas o nome do jogo (ou o repositório, para ler o README).
As chaves só vão para a API do próprio serviço (SteamGridDB; Twitch/IGDB), nunca para os
servidores de imagens.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import quote

import requests

UA = "UsagiGameLauncher/1.0 (+https://github.com/usaginoyami/UsagiGameLauncher)"
WIKI_API = "https://en.wikipedia.org/w/api.php"
SGDB_API = "https://www.steamgriddb.com/api/v2"
TWITCH_TOKEN = "https://id.twitch.tv/oauth2/token"
IGDB_API = "https://api.igdb.com/v4/games"
IGDB_IMG = "https://images.igdb.com/igdb/image/upload/t_cover_big/{}.jpg"
IGDB_THUMB = "https://images.igdb.com/igdb/image/upload/t_cover_small/{}.jpg"
# ids de plataforma da IGDB: a busca filtra por eles para não trazer o mesmo título de outro console
IGDB_PLATFORMS = {"N64": 4, "PS1": 7, "PS2": 8, "PS3": 9, "PS4": 48, "PSP": 38, "Xbox": 11,
                  "Xbox 360": 12, "Xbox One": 49, "Wii": 5, "GameCube": 21, "Switch": 130, "Dreamcast": 23,
                  "SNES": 19, "NES": 18, "Game Boy": 33, "GBA": 24, "DS": 20}
WIKI_PLATFORM = {"N64": "Nintendo 64", "PS1": "PlayStation", "PS2": "PlayStation 2",
                 "PS3": "PlayStation 3", "PS4": "PlayStation 4", "PSP": "PlayStation Portable",
                 "Xbox": "Xbox", "Xbox 360": "Xbox 360", "Xbox One": "Xbox One", "Wii": "Wii", "GameCube": "GameCube",
                 "Switch": "Nintendo Switch", "Dreamcast": "Dreamcast", "SNES": "Super Nintendo",
                 "NES": "Nintendo Entertainment System", "GBA": "Game Boy Advance"}
MAX_BYTES = 8 * 1024 * 1024
RETRY_AFTER = 7 * 24 * 3600  # depois de uma busca sem resultado, espera 7 dias
MIN_SIMILARITY = 0.6
MIN_MATCH = 0.8  # busca estrita: capa errada é pior que capa nenhuma
CACHE_VERSION = 2
_ROMAN = {"ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8", "ix": "9"}
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


def _match_tokens(text: str) -> set[str]:
    text = re.sub(r"\([^)]*\)", " ", text.lower())
    return {_ROMAN.get(t, t) for t in re.findall(r"[a-z0-9]+", text)} - _STOP


def match_score(query: str, title: str) -> float:
    """Parecença nos dois sentidos (Jaccard). Números de sequência precisam bater:
    'Gran Turismo 4' não casa com 'Gran Turismo 3' nem com 'Gran Turismo 4 Prologue'."""
    q, t = _match_tokens(query), _match_tokens(title)
    if not q or not t:
        return 0.0
    if {x for x in q if x.isdigit()} != {x for x in t if x.isdigit()}:
        return 0.0
    return len(q & t) / len(q | t)


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


def find_wikipedia(session, title: str, platform: str = "") -> str | None:
    hint = WIKI_PLATFORM.get(platform, "")
    r = session.get(WIKI_API, headers={"User-Agent": UA}, timeout=15, params={
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"{title} video game {hint}".strip(), "gsrlimit": 3,
        "prop": "pageimages", "piprop": "thumbnail", "pithumbsize": 600, "pilimit": 3,
    })
    if not r.ok:
        return None
    pages = ((r.json() or {}).get("query") or {}).get("pages") or {}
    for page in sorted(pages.values(), key=lambda p: p.get("index", 99)):
        thumb = (page.get("thumbnail") or {}).get("source")
        if thumb and match_score(title, page.get("title", "")) >= MIN_MATCH:
            return thumb
    return None


def find_steamgriddb(session, key: str, title: str) -> str | None:
    headers = {"Authorization": f"Bearer {key}", "User-Agent": UA}
    r = session.get(f"{SGDB_API}/search/autocomplete/{quote(title, safe='')}",
                    headers=headers, timeout=15)
    if not r.ok:
        return None
    game_id, best = None, 0.0
    for g in ((r.json() or {}).get("data") or [])[:5]:
        score = match_score(title, g.get("name", ""))
        if score >= MIN_MATCH and score > best:
            game_id, best = g.get("id"), score
    if game_id is None:
        return None
    r = session.get(f"{SGDB_API}/grids/game/{game_id}", headers=headers, timeout=15,
                    params={"dimensions": "460x215,920x430", "types": "static", "nsfw": "false"})
    if not r.ok:
        return None
    items = (r.json() or {}).get("data") or []
    return items[0].get("url") if items else None


@dataclass
class Candidate:
    """Uma opção de capa para o usuário escolher."""
    label: str
    thumb: str
    full: str
    source: str


class IGDB:
    """IGDB (Twitch): token de app com Client ID + Secret, válido por semanas, guardado em memória."""

    def __init__(self, session, client_id: str, secret: str):
        self.session, self.client_id, self.secret = session, client_id, secret
        self._token, self._expires = "", 0.0

    def _auth(self) -> str:
        if self._token and time.time() < self._expires - 60:
            return self._token
        r = self.session.post(TWITCH_TOKEN, headers={"User-Agent": UA}, timeout=15, params={
            "client_id": self.client_id, "client_secret": self.secret,
            "grant_type": "client_credentials"})
        if not r.ok:
            return ""
        data = r.json() or {}
        self._token = data.get("access_token", "")
        self._expires = time.time() + float(data.get("expires_in", 0))
        return self._token

    def _query(self, title: str, platform_id: int | None, limit: int, fields: str) -> list[dict] | None:
        token = self._auth()
        if not token:
            return None
        safe = re.sub(r"\([^)]*\)", " ", title).replace("\\", " ").replace('"', " ").strip()
        where = "version_parent = null"
        if platform_id:
            where += f" & platforms = ({int(platform_id)})"
        r = self.session.post(
            IGDB_API, timeout=15, data=f'search "{safe}"; fields {fields}; where {where}; limit {limit};',
            headers={"Client-ID": self.client_id, "Authorization": f"Bearer {token}",
                     "User-Agent": UA})
        if r.status_code == 401:  # token revogado: pede outro na próxima vez
            self._token = ""
            return None
        if not r.ok:
            return None
        data = r.json()
        return data if isinstance(data, list) else None

    def find(self, title: str, platform: str = "") -> str | None:
        """Melhor capa para o título. Com plataforma conhecida, só aceita jogos dela."""
        pid = IGDB_PLATFORMS.get(platform)
        best, best_score = None, -1.0
        for g in (self._query(title, pid, 10, "name,cover.image_id") or []):
            image_id = (g.get("cover") or {}).get("image_id")
            score = match_score(title, g.get("name", ""))
            if image_id and score >= MIN_MATCH and score > best_score:
                best, best_score = image_id, score
        return IGDB_IMG.format(best) if best else None

    def search_names(self, title: str, platform: str = "", limit: int = 10) -> list[tuple[str, str]]:
        """(nome oficial, detalhe) dos jogos que combinam; os da plataforma vêm primeiro."""
        pid = IGDB_PLATFORMS.get(platform)
        out: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for plat_id in ([pid, None] if pid else [None]):
            for g in (self._query(title, plat_id, limit, "name,first_release_date,platforms.abbreviation") or []):
                name = (g.get("name") or "").strip()
                year = str(time.gmtime(g["first_release_date"]).tm_year) if g.get("first_release_date") else ""
                if not name or (name, year) in seen:
                    continue
                seen.add((name, year))
                plats = ", ".join(p.get("abbreviation", "") for p in (g.get("platforms") or [])[:4]
                                  if p.get("abbreviation"))
                out.append((name, " · ".join(x for x in (year, plats) if x)))
        return out[:limit]

    def search_many(self, title: str, platform: str = "", limit: int = 12) -> list[Candidate]:
        """Opções para o usuário: primeiro jogos da plataforma, depois os demais."""
        pid = IGDB_PLATFORMS.get(platform)
        fields = "name,first_release_date,platforms.abbreviation,cover.image_id"
        seen: set[str] = set()
        out: list[Candidate] = []
        for plat_id in ([pid, None] if pid else [None]):
            for g in (self._query(title, plat_id, limit, fields) or []):
                image_id = (g.get("cover") or {}).get("image_id")
                if not image_id or image_id in seen:
                    continue
                seen.add(image_id)
                year = ""
                if g.get("first_release_date"):
                    year = f" ({time.gmtime(g['first_release_date']).tm_year})"
                plats = ", ".join(p.get("abbreviation", "") for p in (g.get("platforms") or [])[:4]
                                  if p.get("abbreviation"))
                label = f"{g.get('name', '?')}{year}" + (f"\n{plats}" if plats else "")
                out.append(Candidate(label, IGDB_THUMB.format(image_id), IGDB_IMG.format(image_id), "IGDB"))
        return out[:limit]


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
    def __init__(self, session=None, sgdb_key: str = "", enabled: bool = True,
                 igdb_id: str = "", igdb_secret: str = ""):
        self.session = session or requests.Session()
        self.enabled = enabled
        self.sgdb_key = ""
        self.igdb: IGDB | None = None
        self.configure(sgdb_key, enabled, igdb_id, igdb_secret)

    def configure(self, sgdb_key: str, enabled: bool, igdb_id: str = "", igdb_secret: str = "") -> None:
        self.sgdb_key = (sgdb_key or "").strip()
        self.enabled = bool(enabled)
        igdb_id, igdb_secret = (igdb_id or "").strip(), (igdb_secret or "").strip()
        self.igdb = IGDB(self.session, igdb_id, igdb_secret) if igdb_id and igdb_secret else None

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

        platform = getattr(entry, "platform", "")
        sources: list[Callable[[], str | None]] = []
        if self.igdb:  # sabe a plataforma, então erra menos que as outras
            sources.append(lambda: self.igdb.find(title, platform))
        if self.sgdb_key:
            sources.append(lambda: find_steamgriddb(self.session, self.sgdb_key, title))
        sources.append(lambda: find_wikipedia(self.session, title, platform))
        sources.append(lambda: find_readme_image(self.session, entry.repo))
        for source in sources:
            url = attempt(source)
            if url:
                data = attempt(lambda u=url: download_image(self.session, u))
                if data:
                    return data, False
        return None, net_error

    def candidates(self, query: str, platform: str = "", limit: int = 12) -> tuple[list[Candidate], bool]:
        """Opções de capa para o usuário escolher. Devolve (opções, houve_erro_de_rede)."""
        out: list[Candidate] = []
        net_error = False

        def attempt(fn):
            nonlocal net_error
            try:
                return fn() or []
            except requests.RequestException:
                net_error = True
            except (ValueError, KeyError, TypeError, AttributeError, OSError):
                pass
            return []

        if self.igdb:
            out += attempt(lambda: self.igdb.search_many(query, platform, limit))
        if self.sgdb_key:
            out += attempt(lambda: self._sgdb_candidates(query))
        out += attempt(lambda: self._wiki_candidates(query, platform))
        return out, net_error

    def _sgdb_candidates(self, query: str) -> list[Candidate]:
        headers = {"Authorization": f"Bearer {self.sgdb_key}", "User-Agent": UA}
        r = self.session.get(f"{SGDB_API}/search/autocomplete/{quote(query, safe='')}",
                             headers=headers, timeout=15)
        if not r.ok:
            return []
        games = ((r.json() or {}).get("data") or [])[:1]
        if not games:
            return []
        r = self.session.get(f"{SGDB_API}/grids/game/{games[0].get('id')}", headers=headers, timeout=15,
                             params={"dimensions": "460x215,920x430,600x900", "types": "static", "nsfw": "false"})
        if not r.ok:
            return []
        items = ((r.json() or {}).get("data") or [])[:4]
        return [Candidate(f"{games[0].get('name', query)}\nSteamGridDB", it.get("thumb") or it.get("url"),
                          it.get("url"), "SteamGridDB") for it in items if it.get("url")]

    def _wiki_candidates(self, query: str, platform: str) -> list[Candidate]:
        hint = WIKI_PLATFORM.get(platform, "")
        r = self.session.get(WIKI_API, headers={"User-Agent": UA}, timeout=15, params={
            "action": "query", "format": "json", "generator": "search",
            "gsrsearch": f"{query} video game {hint}".strip(), "gsrlimit": 4,
            "prop": "pageimages", "piprop": "thumbnail", "pithumbsize": 600, "pilimit": 4})
        if not r.ok:
            return []
        pages = ((r.json() or {}).get("query") or {}).get("pages") or {}
        out = []
        for page in sorted(pages.values(), key=lambda p: p.get("index", 99)):
            thumb = (page.get("thumbnail") or {}).get("source")
            if thumb:
                out.append(Candidate(f"{page.get('title', query)}\nWikipedia", thumb, thumb, "Wikipedia"))
        return out

    def suggest_names(self, query: str, platform: str = "", limit: int = 10) -> tuple[list[tuple[str, str]], bool]:
        """Nomes oficiais possíveis para o jogo. Devolve (lista de (nome, detalhe), erro_de_rede)."""
        out: list[tuple[str, str]] = []
        net_error = False
        try:
            if self.igdb:
                out += self.igdb.search_names(query, platform, limit)
        except requests.RequestException:
            net_error = True
        except (ValueError, KeyError, TypeError, AttributeError):
            pass
        try:
            hint = WIKI_PLATFORM.get(platform, "")
            r = self.session.get(WIKI_API, headers={"User-Agent": UA}, timeout=15, params={
                "action": "query", "format": "json", "list": "search",
                "srsearch": f"{query} video game {hint}".strip(), "srlimit": 5})
            if r.ok:
                known = {n.lower() for n, _ in out}
                for hit in (((r.json() or {}).get("query") or {}).get("search") or []):
                    name = re.sub(r"\s*\([^)]*\)\s*$", "", hit.get("title", "")).strip()
                    if name and name.lower() not in known and match_score(query, name) >= 0.5:
                        known.add(name.lower())
                        out.append((name, "Wikipedia"))
        except requests.RequestException:
            net_error = True
        except (ValueError, KeyError, TypeError, AttributeError):
            pass
        return out[:limit], net_error

    def download(self, url: str) -> bytes | None:
        return download_image(self.session, url)

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
        self._migrate()

    def _migrate(self) -> None:
        """Busca antiga aceitava capas de jogos parecidos: descarta as automáticas (mantém as suas)."""
        marker = self.folder / ".version"
        try:
            current = int(marker.read_text().strip())
        except (OSError, ValueError):
            current = 1
        if current >= CACHE_VERSION:
            return
        for img in self.folder.glob("*.img"):
            if not img.with_suffix(".custom").exists():
                self._remove(img)
        for none in self.folder.glob("*.none"):
            self._remove(none)
        try:
            marker.write_text(str(CACHE_VERSION))
        except OSError:
            pass

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
        self._remove(self._base(key).with_suffix(".custom"))
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

    def image_for(self, key: str) -> tuple[bytes | None, bool]:
        """Bytes da capa em cache e se foi escolhida por você (True) ou achada sozinha (False)."""
        img = self.cached(key)
        if img is None:
            return None, False
        try:
            return img.read_bytes(), self._base(key).with_suffix(".custom").exists()
        except OSError:
            return None, False

    def set_custom_bytes(self, key: str, data: bytes) -> Path:
        """Capa escolhida pelo usuário: fica protegida da limpeza das capas automáticas."""
        if not sniff(data):
            raise ValueError("Isso não é uma imagem PNG, JPG, WEBP ou GIF.")
        if len(data) > MAX_BYTES:
            raise ValueError("Imagem grande demais (limite de 8 MB).")
        path = self._write(key, data)
        path.with_suffix(".custom").touch()
        return path

    def set_custom(self, key: str, source: str | os.PathLike) -> Path:
        data = Path(source).read_bytes()
        if not sniff(data):
            raise ValueError("O arquivo escolhido não é uma imagem PNG, JPG, WEBP ou GIF.")
        if len(data) > MAX_BYTES:
            raise ValueError("Imagem grande demais (limite de 8 MB).")
        path = self._write(key, data)
        path.with_suffix(".custom").touch()
        return path

    def clear(self, key: str) -> None:
        self._remove(self._img(key))
        self._remove(self._base(key).with_suffix(".custom"))
        self._remove(self._none(key))

    def clear_failures(self) -> None:
        for p in self.folder.glob("*.none"):
            self._remove(p)
