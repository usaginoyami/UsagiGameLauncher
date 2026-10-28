"""Localiza jogos que já estão no disco e liga cada pasta a um jogo da lista.

Serve para quem já tinha o jogo instalado à mão, trocou de computador ou perdeu o
library.json: o launcher recupera o registro sem baixar nada de novo.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import installer

MAX_DEPTH = 3
MAX_DIRS = 5000
_SKIP_DIRS = re.compile(r"^(\.|\$|_backups$|node_modules$|__pycache__$|windows$|program files|"
                        r"programdata$|appdata$|system volume information$|\$recycle)", re.I)
_VERSION_DIR = re.compile(r"^v?\d+(\.\d+){0,3}([-_.][\w.]+)?$", re.I)
_NOISE = {"recompiled", "recompilation", "recomp", "port", "native", "windows", "win64", "win32",
          "x64", "x86", "pc", "portable", "release", "build", "game", "the", "of", "and", "re"}


@dataclass
class Found:
    folder: Path  # pasta onde está o jogo (a da versão)
    root: Path  # pasta que agrupa as versões
    version: str  # "v1.2.0" ou "manual"
    exe: Path | None
    label: str  # texto usado para casar com o catálogo
    match: str | None = None  # chave do jogo (repo em minúsculas)
    score: float = 0.0
    alternatives: list[str] = field(default_factory=list)


def compact(text: str) -> str:
    """Só letras e números, sem palavras de ruído: 'Zelda64Recompiled' -> 'zelda64'."""
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)  # BanjoRecomp -> Banjo Recomp
    tokens = [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _NOISE]
    return "".join(tokens)


def name_score(label: str, candidate: str) -> float:
    a, b = compact(label), compact(candidate)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    short, long_ = sorted((a, b), key=len)
    if len(short) >= 6 and short in long_:
        return 0.85 * len(short) / len(long_) + 0.1
    return 0.0


def entry_names(entry, orig_name: str = "") -> list[str]:
    names = [orig_name or entry.name, entry.name, entry.repo.split("/")[-1], entry.cover_title]
    return [n for n in names if n]


def describe(folder: Path) -> tuple[Path, str, str]:
    """(pasta-raiz, versão, rótulo) de uma pasta de jogo. 'Jogo/v1.2.0' -> raiz 'Jogo', 'v1.2.0'."""
    folder = Path(folder)
    if _VERSION_DIR.match(folder.name) and folder.parent != folder:
        return folder.parent, folder.name, folder.parent.name
    return folder.parent, "manual", folder.name


def _has_game_exe(folder: Path) -> bool:
    try:
        for name in os.listdir(folder):
            if name.lower().endswith(".exe") and not installer._IGNORED_EXE.search(name):
                return True
    except OSError:
        pass
    return False


def find_game_folders(bases: list[Path], skip: set[str] | None = None) -> list[Path]:
    """Pastas (até 3 níveis) com um executável de jogo. Não entra em links simbólicos."""
    skip = {str(Path(s).resolve()).lower() for s in (skip or set())}
    out: list[Path] = []
    seen: set[str] = set()
    visited = 0
    for base in bases:
        base = Path(base)
        if not base.is_dir():
            continue
        stack = [(base, 0)]
        while stack and visited < MAX_DIRS:
            d, depth = stack.pop()
            visited += 1
            key = str(d.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            if depth > 0 and key not in skip and _has_game_exe(d):
                out.append(d)
                continue  # não procura dentro de um jogo
            if depth >= MAX_DEPTH:
                continue
            try:
                children = sorted(os.scandir(d), key=lambda e: e.name.lower())
            except OSError:
                continue
            for c in children:
                try:
                    if c.is_dir(follow_symlinks=False) and not _SKIP_DIRS.match(c.name):
                        stack.append((Path(c.path), depth + 1))
                except OSError:
                    continue
    return out


def best_match(label: str, exe_stem: str, entries: list, orig_names: dict[str, str] | None = None):
    """(chave, nota, alternativas) do jogo que melhor combina; empate = sem escolha segura."""
    orig_names = orig_names or {}
    scored: list[tuple[float, str]] = []
    for e in entries:
        s = max((max(name_score(label, n), name_score(exe_stem, n) * 0.9)
                 for n in entry_names(e, orig_names.get(e.key, ""))), default=0.0)
        if s >= 0.6:
            scored.append((s, e.key))
    scored.sort(reverse=True)
    if not scored:
        return None, 0.0, []
    top = scored[0][0]
    ties = [k for s, k in scored if abs(s - top) < 0.02]
    if len(ties) > 1:
        return None, top, ties  # ambíguo: o usuário decide
    return scored[0][1], top, [k for _, k in scored[1:4]]


def scan(bases: list[Path], entries: list, known_folders: set[str] | None = None,
         orig_names: dict[str, str] | None = None) -> list[Found]:
    found: list[Found] = []
    seen_roots: dict[str, Found] = {}
    for folder in find_game_folders(bases, known_folders):
        root, version, label = describe(folder)
        exes = installer.find_executables(folder, label)
        exe = exes[0] if exes else None
        f = Found(folder, root, version, exe, label)
        f.match, f.score, f.alternatives = best_match(label, exe.stem if exe else "", entries, orig_names)
        # várias versões do mesmo jogo: fica a mais nova (pelo nome da pasta)
        prev = seen_roots.get(str(root).lower()) if version != "manual" else None
        if prev is not None:
            if _vkey(version) > _vkey(prev.version):
                found[found.index(prev)] = f
                seen_roots[str(root).lower()] = f
            continue
        if version != "manual":
            seen_roots[str(root).lower()] = f
        found.append(f)
    found.sort(key=lambda f: (f.match is None, f.label.casefold()))
    return found


def _vkey(version: str) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", version)[:4]) or (0,)
