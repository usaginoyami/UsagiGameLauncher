"""Suporte ao GitLab (gitlab.com): projetos entram como "gl:grupo/projeto".

Só usa a API pública e leitura. Os dados são convertidos para o mesmo formato do GitHub
(Release, Asset, e um dict parecido com o de /repos) para o resto do programa não precisar saber.
"""
from __future__ import annotations

import re
from urllib.parse import quote

HOST = "https://gitlab.com"
API = HOST + "/api/v4"
PREFIX = "gl:"
_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def is_gitlab(repo: str) -> bool:
    return (repo or "").lower().startswith(PREFIX)


def path_of(repo: str) -> str:
    return repo[len(PREFIX):] if is_gitlab(repo) else repo


def web_url(repo: str) -> str:
    return f"{HOST}/{path_of(repo)}"


def project_path(repo: str) -> str:
    """Caminho da API: /projects/<grupo%2Fprojeto>."""
    return "/projects/" + quote(path_of(repo), safe="")


def parse(text: str) -> str | None:
    """Aceita um link do gitlab.com (com subgrupos) ou 'gl:grupo/projeto'."""
    text = (text or "").strip()
    if text.lower().startswith(PREFIX):
        path = text[len(PREFIX):]
    elif "gitlab.com" in text.lower():
        path = re.split(r"gitlab\.com[/:]", text, maxsplit=1, flags=re.I)[-1]
    else:
        return None
    path = re.split(r"[?#]", path)[0].split("/-/")[0]
    parts = [p for p in path.split("/") if p]
    if parts and parts[-1].lower().endswith(".git"):
        parts[-1] = parts[-1][:-4]
    if len(parts) < 2 or not all(_SEGMENT.fullmatch(p) for p in parts):
        return None
    return PREFIX + "/".join(parts)


def to_repo_info(d: dict) -> dict:
    """Mesmas chaves que o GitHub devolve em /repos/{dono}/{repo}."""
    path = d.get("path_with_namespace", "")
    return {
        "full_name": PREFIX + path, "name": d.get("path") or path.split("/")[-1],
        "description": d.get("description") or "", "topics": d.get("topics") or d.get("tag_list") or [],
        "stargazers_count": int(d.get("star_count") or 0), "pushed_at": d.get("last_activity_at") or "",
        "homepage": "", "archived": bool(d.get("archived")),
    }


def parse_release(d: dict):
    from .github_api import Asset, Release

    links = (d.get("assets") or {}).get("links") or []
    assets = []
    for ln in links:
        url = ln.get("direct_asset_url") or ln.get("url") or ""
        if url.startswith("https://") and ln.get("name"):
            assets.append(Asset(name=str(ln["name"]), size=0, url=url))
    tag = d.get("tag_name", "")
    return Release(
        tag=tag, name=d.get("name") or tag,
        published_at=d.get("released_at") or d.get("created_at") or "",
        prerelease=bool(d.get("upcoming_release")),
        html_url=(d.get("_links") or {}).get("self") or "", body=d.get("description") or "",
        assets=assets)


def pick_release(releases: list[dict], allow_prerelease: bool = True):
    for r in releases:
        if not r.get("upcoming_release"):
            return parse_release(r)
    if allow_prerelease:
        for r in releases:
            return parse_release(r)
    return None
