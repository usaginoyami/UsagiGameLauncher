"""Catálogo curado: o curador publica, o cliente recebe.

Formato (catalog/catalog.json no repositório):
    {"schema": 1, "updated": "...", "games": [{"repo", "name", "platform", "target", "description",
     "notes", "asset_hint", "homepage", "cover_title", "adult", "cover": "covers/x.jpg"}]}
`cover` é um caminho relativo (ao catalog.json) ou um link https.
"""
from __future__ import annotations

import base64
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

from . import catalog
from .jsonio import load_json, save_json

SCHEMA = 1
REPO = "usaginoyami/UsagiGameLauncher"
BRANCH = "main"
DIR = "catalog"
CATALOG_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{DIR}/catalog.json"
MAX_BYTES = 5 * 1024 * 1024
MAX_GAMES = 3000
_FIELDS = ("repo", "name", "platform", "target", "description", "notes", "asset_hint",
           "homepage", "cover_title", "link")
_SAFE_COVER = re.compile(r"^covers/[A-Za-z0-9._-]{1,80}\.(?:jpg|png|webp|gif)$")


class CatalogError(Exception):
    pass


def cover_file_name(key: str, ext: str) -> str:
    return "covers/" + re.sub(r"[^a-z0-9._-]", "_", key.lower())[:70] + "." + ext


def build(entries: list[catalog.Entry], covers: dict[str, str] | None = None) -> dict:
    """`covers` mapeia a chave do jogo ao caminho relativo da capa que será publicada."""
    covers = covers or {}
    games = []
    for e in entries:
        item = {f: getattr(e, f) for f in _FIELDS}
        item["adult"] = bool(e.adult)
        cover = covers.get(e.key) or (e.cover if e.cover.lower().startswith("https://") else "")
        if cover:
            item["cover"] = cover
        games.append(item)
    games.sort(key=lambda g: g["name"].casefold())
    return {"schema": SCHEMA, "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "games": games}


def write_folder(dest: Path, data: dict, cover_bytes: dict[str, bytes]) -> Path:
    """Grava catalog.json e covers/ numa pasta pronta para copiar para o repositório."""
    dest = Path(dest)
    (dest / "covers").mkdir(parents=True, exist_ok=True)
    for rel, blob in cover_bytes.items():
        if not _SAFE_COVER.match(rel):
            continue
        (dest / rel).write_bytes(blob)
    save_json(dest / "catalog.json", data)
    return dest / "catalog.json"


# ---------------------------------------------------------------- leitura (cliente)
def _clean_cover(value: object, base_url: str) -> str:
    if not isinstance(value, str) or not value:
        return ""
    url = urljoin(base_url, value) if _SAFE_COVER.match(value) else value
    parsed = urlparse(url)
    return url if parsed.scheme == "https" and parsed.netloc else ""


def parse(raw: object, base_url: str = CATALOG_URL) -> list[catalog.Entry]:
    """Valida o JSON recebido e devolve as entradas. Ignora o que estiver malformado."""
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        raise CatalogError("Formato de catálogo desconhecido.")
    games = raw.get("games")
    if not isinstance(games, list) or len(games) > MAX_GAMES:
        raise CatalogError("Catálogo inválido.")
    out: list[catalog.Entry] = []
    seen: set[str] = set()
    for g in games:
        if not isinstance(g, dict):
            continue
        repo = catalog.parse_repo(str(g.get("repo", "")))
        if not repo or repo.lower() in seen:
            continue
        seen.add(repo.lower())
        hint = str(g.get("asset_hint", ""))[:100]
        try:
            re.compile(hint)
        except re.error:
            hint = ""
        e = catalog.Entry(
            repo=repo,
            name=str(g.get("name", ""))[:80],
            platform=str(g.get("platform") or "Outros")[:30],
            target=str(g.get("target", ""))[:30],
            description=str(g.get("description", ""))[:600],
            notes=str(g.get("notes", ""))[:400],
            asset_hint=hint,
            homepage=str(g.get("homepage", ""))[:200] if str(g.get("homepage", "")).startswith("https://") else "",
            cover_title=str(g.get("cover_title", ""))[:80],
            link=str(g.get("link", ""))[:300] if str(g.get("link", "")).startswith("https://") else "",
            cover=_clean_cover(g.get("cover"), base_url),
            adult=bool(g.get("adult")),
            source="catalog",
        )
        out.append(e)
    return out


class RemoteCatalog:
    """Baixa o catálogo publicado, guarda cópia em disco e usa a cópia se estiver sem rede."""

    def __init__(self, cache_path: Path, url: str = CATALOG_URL, session=None):
        self.cache_path = Path(cache_path)
        self.url = url
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", "UsagiGameLauncher")

    def cached(self) -> tuple[list[catalog.Entry], str]:
        data = load_json(self.cache_path, {})
        try:
            return parse(data.get("catalog"), self.url), str(data.get("updated", ""))
        except (CatalogError, AttributeError):
            return [], ""

    def refresh(self, force: bool = False) -> tuple[list[catalog.Entry] | None, str]:
        """(entradas, mensagem). entradas=None quando nada mudou ou deu erro."""
        stored = load_json(self.cache_path, {})
        headers = {}
        if not force and isinstance(stored, dict) and stored.get("etag"):
            headers["If-None-Match"] = stored["etag"]
        try:
            r = self.session.get(self.url, headers=headers, timeout=20)
        except requests.RequestException as e:
            return None, f"Sem conexão para atualizar o catálogo ({e.__class__.__name__})."
        if r.status_code == 304:
            return None, "Catálogo em dia."
        if r.status_code == 404:
            return None, "O catálogo ainda não foi publicado."
        if not r.ok:
            return None, f"Não consegui baixar o catálogo (HTTP {r.status_code})."
        if len(r.content) > MAX_BYTES:
            return None, "Catálogo grande demais; ignorado."
        try:
            data = r.json()
            entries = parse(data, self.url)
        except (ValueError, CatalogError) as e:
            return None, f"Catálogo recebido é inválido: {e}"
        save_json(self.cache_path, {"etag": r.headers.get("ETag", ""), "ts": time.time(),
                                    "updated": str(data.get("updated", "")), "catalog": data})
        return entries, f"Catálogo atualizado: {len(entries)} jogos."


# ------------------------------------------------------------ publicação (curador)
def publish_to_github(token: str, files: dict[str, bytes], message: str,
                      repo: str = REPO, branch: str = BRANCH, session=None) -> int:
    """Envia os arquivos (caminho no repositório → bytes) pela API de conteúdo do GitHub.
    Devolve quantos arquivos mudaram. Precisa de um token com permissão de escrita em Contents."""
    if not token:
        raise CatalogError("Informe um token do GitHub com permissão de escrita (Configurações).")
    s = session or requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                      "User-Agent": "UsagiGameLauncher"})
    changed = 0
    for path, blob in files.items():
        url = f"https://api.github.com/repos/{repo}/contents/{path}"
        r = s.get(url, params={"ref": branch}, timeout=20)
        sha = ""
        if r.status_code == 200:
            info = r.json()
            sha = info.get("sha", "")
            try:
                if base64.b64decode(info.get("content", "")) == blob:
                    continue  # igual ao que já está publicado
            except ValueError:
                pass
        elif r.status_code not in (404,):
            raise CatalogError(f"GitHub respondeu {r.status_code} ao ler {path}.")
        body = {"message": message, "content": base64.b64encode(blob).decode(), "branch": branch}
        if sha:
            body["sha"] = sha
        w = s.put(url, json=body, timeout=60)
        if w.status_code == 401 or w.status_code == 403:
            raise CatalogError("O GitHub recusou a gravação: confira se o token tem permissão de "
                               "escrita em Contents deste repositório.")
        if not w.ok:
            raise CatalogError(f"Falha ao publicar {path} (HTTP {w.status_code}).")
        changed += 1
    return changed
