from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import paths
from .errors import RateLimitError
from .jsonio import load_json, save_json

PLATFORMS = [
    "N64", "Xbox 360", "Xbox", "PS3", "PS2", "PS1", "GameCube", "Wii",
    "Switch", "Game Boy", "GBA", "DS", "Outros",
]

_PLATFORM_PATTERNS: list[tuple[str, str]] = [
    ("N64", r"\bn64\b|nintendo ?64"),
    ("Xbox 360", r"xbox ?360|\bx360\b|xenon"),
    ("PS3", r"\bps3\b|playstation ?3"),
    ("PS2", r"\bps2\b|playstation ?2"),
    ("PS1", r"\bps1\b|\bpsx\b|playstation ?1|\bpsone\b"),
    ("GameCube", r"game ?cube|\bgcn\b"),
    ("Wii", r"\bwii\b"),
    ("Switch", r"nintendo switch|\bswitch\b"),
    ("GBA", r"\bgba\b|game ?boy advance"),
    ("Game Boy", r"game ?boy|\bgbc\b"),
    ("DS", r"\bnds\b|nintendo ds"),
    ("Xbox", r"\bxbox\b"),
]

_TOOL_RE = re.compile(
    r"(launcher|recompiler|toolkit|\btools?\b|library|framework|runtime|plugin|"
    r"mod ?manager|awesome|\blist\b|decomp(?:ilation)? (?:tool|project of)|template)",
    re.I,
)
_KNOWN_TOOLS = {"n64recomp", "xenonrecomp", "xenia", "rexglue", "ps3recomp", "n64modernruntime"}


@dataclass
class Entry:
    repo: str
    name: str = ""
    platform: str = "Outros"
    description: str = ""
    notes: str = ""
    source: str = "catalog"  # catalog | user | discovered
    asset_hint: str = ""
    stars: int = 0
    pushed_at: str = ""
    homepage: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            self.name = pretty_name(self.repo.split("/")[-1])

    @property
    def key(self) -> str:
        return self.repo.lower()

    @property
    def owner(self) -> str:
        return self.repo.split("/")[0]

    @property
    def url(self) -> str:
        return f"https://github.com/{self.repo}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Entry | None":
        if not isinstance(d, dict) or not parse_repo(str(d.get("repo", ""))):
            return None
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


def pretty_name(raw: str) -> str:
    s = raw.replace("_", " ").replace("-", " ")
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)
    s = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_repo(text: str) -> str | None:
    """Aceita 'owner/repo' ou uma URL do GitHub e devolve 'owner/repo'."""
    text = (text or "").strip()
    if not text:
        return None
    if "github.com" in text.lower():
        path = re.split(r"github\.com[/:]", text, maxsplit=1, flags=re.I)[-1]
    else:
        path = text
    parts = [p for p in re.split(r"[/?#]", path) if p]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1]
    repo = re.sub(r"\.git$", "", repo)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", owner):
        return None
    if not re.fullmatch(r"[A-Za-z0-9._-]+", repo):
        return None
    return f"{owner}/{repo}"


def guess_platform(text: str) -> str:
    low = text.lower()
    for name, pattern in _PLATFORM_PATTERNS:
        if re.search(pattern, low):
            return name
    return "Outros"


def is_tool_repo(name: str, description: str = "") -> bool:
    if name.lower() in _KNOWN_TOOLS:
        return True
    return bool(_TOOL_RE.search(f"{name} {description}"))


# ----------------------------------------------------------------- fontes
def load_bundled() -> list[Entry]:
    data = load_json(paths.resource_path("data/catalog.json"), {})
    return _entries(data.get("games", []), "catalog")


def user_catalog_path() -> Path:
    return paths.data_dir() / "catalog.user.json"


def load_user() -> list[Entry]:
    data = load_json(user_catalog_path(), {})
    return _entries(data.get("games", []), "user")


def save_user(entries: list[Entry]) -> None:
    save_json(user_catalog_path(), {"version": 1, "games": [e.to_dict() for e in entries]})


def discovered_path() -> Path:
    return paths.data_dir() / "discovered.json"


def load_discovered() -> tuple[list[Entry], float]:
    data = load_json(discovered_path(), {})
    return _entries(data.get("items", []), "discovered"), float(data.get("ts", 0))


def save_discovered(entries: list[Entry]) -> None:
    save_json(discovered_path(), {"ts": time.time(), "items": [e.to_dict() for e in entries]})


def _entries(raw: list, source: str) -> list[Entry]:
    out = []
    for d in raw if isinstance(raw, list) else []:
        e = Entry.from_dict(d)
        if e:
            e.source = source
            out.append(e)
    return out


def merge(user: list[Entry], bundled: list[Entry], discovered: list[Entry],
          hidden: set[str] | None = None) -> list[Entry]:
    """Usuário sobrepõe o catálogo, que sobrepõe os descobertos. Sem duplicatas."""
    hidden = hidden or set()
    out: dict[str, Entry] = {}
    for group in (user, bundled, discovered):
        for e in group:
            if e.key in out:
                continue
            if e.source == "discovered" and e.key in hidden:
                continue
            out[e.key] = e
    return list(out.values())


# -------------------------------------------------------------- descoberta
def discover(client, topics: list[str], min_stars: int = 10) -> tuple[list[Entry], Exception | None]:
    """Procura repositórios por tópico no GitHub. Retorna (entradas, erro_parcial)."""
    found: dict[str, Entry] = {}
    error: Exception | None = None
    for topic in topics:
        topic = topic.strip()
        if not topic:
            continue
        try:
            items = client.search_repos(topic, min_stars)
        except RateLimitError as e:
            error = e
            break
        except Exception as e:  # rede etc.: segue com o que já achou
            error = e
            break
        for it in items:
            full = it.get("full_name") or ""
            if not full or full.lower() in found:
                continue
            if it.get("archived") or it.get("fork"):
                continue
            name = it.get("name", "")
            desc = it.get("description") or ""
            if is_tool_repo(name, desc):
                continue
            text = " ".join([name, desc, " ".join(it.get("topics") or [])])
            found[full.lower()] = Entry(
                repo=full,
                name=pretty_name(name),
                platform=guess_platform(text),
                description=desc,
                source="discovered",
                stars=int(it.get("stargazers_count") or 0),
                pushed_at=it.get("pushed_at") or "",
                homepage=it.get("homepage") or "",
            )
    return sorted(found.values(), key=lambda e: -e.stars), error
