from __future__ import annotations

import hashlib
import re
import shutil
import threading
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .errors import Cancelled, InstallError
from .github_api import Asset, GitHubClient, Release

Progress = Callable[[str, int, int], None]

MAX_UNPACKED_BYTES = 60 * 1024 ** 3  # trava contra zip bomb

_BAD_TOKENS = {
    "linux", "macos", "mac", "osx", "darwin", "android", "arm", "arm64", "aarch64",
    "appimage", "flatpak", "deb", "rpm", "dmg", "source", "src", "symbols", "pdb",
    "debug", "apk", "ios", "steamdeck", "tar", "gz", "xz",
}
_WIN_TOKENS = {"windows", "win", "win64", "win32", "w64"}
_EXT_SCORE = {".zip": 30, ".7z": 20, ".exe": 5}
_IGNORED_EXE = re.compile(
    r"(unins|uninst|crash|vc_?redist|vcredist|setup|installer|dxsetup|dotnet|"
    r"updater|reporter|helper|7z|ffmpeg)", re.I)


def safe_name(text: str) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).strip(" .")
    return s or "jogo"


def _tokens(name: str) -> set[str]:
    return set(re.split(r"[^a-z0-9]+", name.lower())) - {""}


def score_asset(asset: Asset, hint: str = "") -> int:
    """Pontua um arquivo da release como pacote Windows. <= 0 significa descartar."""
    ext = Path(asset.name).suffix.lower()
    if ext not in _EXT_SCORE:
        return -1
    toks = _tokens(asset.name)
    if toks & _BAD_TOKENS:
        return -1
    score = _EXT_SCORE[ext]
    if toks & _WIN_TOKENS or "windows" in asset.name.lower():
        score += 40
    if toks & {"x64", "amd64", "x86_64"} or "x86_64" in asset.name.lower():
        score += 5
    if hint:
        try:
            if re.search(hint, asset.name, re.I):
                score += 60
        except re.error:
            pass
    return score


def pick_asset(assets: list[Asset], hint: str = "") -> Asset | None:
    ranked = sorted(((score_asset(a, hint), a) for a in assets), key=lambda t: -t[0])
    if ranked and ranked[0][0] > 0:
        return ranked[0][1]
    return None


def parse_digest(digest: str) -> str:
    if digest and digest.lower().startswith("sha256:"):
        return digest.split(":", 1)[1].strip().lower()
    return ""


def sha256_file(path: Path, cancel: threading.Event | None = None) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            h.update(chunk)
    return h.hexdigest()


def _inside(base: Path, target: Path) -> bool:
    return target == base or base in target.parents


def safe_extract_zip(archive: Path, dest: Path, progress: Callable[[int, int], None] | None = None,
                     cancel: threading.Event | None = None) -> None:
    dest = Path(dest).resolve()
    with zipfile.ZipFile(archive) as zf:
        infos = zf.infolist()
        total = sum(i.file_size for i in infos)
        if total > MAX_UNPACKED_BYTES:
            raise InstallError("O pacote descompactado é grande demais para ser seguro.")
        done = 0
        for info in infos:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
                raise InstallError(f"Caminho inseguro dentro do ZIP: {info.filename}")
            target = (dest / name).resolve()
            if not _inside(dest, target):
                raise InstallError(f"Caminho inseguro dentro do ZIP: {info.filename}")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:  # symlink
                continue
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                while chunk := src.read(1 << 20):
                    if cancel is not None and cancel.is_set():
                        raise Cancelled()
                    out.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)


def extract(archive: Path, dest: Path, progress: Callable[[int, int], None] | None = None,
            cancel: threading.Event | None = None) -> None:
    ext = archive.suffix.lower()
    if ext == ".zip":
        safe_extract_zip(archive, dest, progress, cancel)
    elif ext == ".7z":
        try:
            import py7zr  # type: ignore
        except ImportError as e:
            raise InstallError("Este jogo usa .7z. Instale o suporte com: pip install py7zr") from e
        with py7zr.SevenZipFile(archive) as z:
            for n in z.getnames():
                n = n.replace("\\", "/")
                if n.startswith("/") or ".." in n.split("/") or re.match(r"^[A-Za-z]:", n):
                    raise InstallError(f"Caminho inseguro dentro do 7z: {n}")
            z.extractall(path=dest)
    elif ext == ".exe":
        shutil.copy2(archive, Path(dest) / archive.name)
    else:
        raise InstallError(f"Formato não suportado: {ext}")


def find_executables(folder: Path, game_name: str = "") -> list[Path]:
    """Lista executáveis candidatos, o mais provável primeiro."""
    folder = Path(folder)
    name_tokens = _tokens(game_name)
    scored: list[tuple[float, Path]] = []
    for p in folder.rglob("*.exe"):
        if not p.is_file() or _IGNORED_EXE.search(p.name):
            continue
        depth = len(p.relative_to(folder).parts) - 1
        overlap = len(_tokens(p.stem) & name_tokens)
        try:
            size_mb = p.stat().st_size / (1024 * 1024)
        except OSError:
            size_mb = 0
        scored.append((overlap * 20 - depth * 5 + min(size_mb, 50) / 5, p))
    scored.sort(key=lambda t: -t[0])
    return [p for _, p in scored]


def unique_dir(path: Path) -> Path:
    if not path.exists() or not any(path.iterdir()):
        return path
    i = 2
    while True:
        cand = path.with_name(f"{path.name}_{i}")
        if not cand.exists():
            return cand
        i += 1


@dataclass
class InstallResult:
    folder: Path
    root: Path
    exe: Path | None
    candidates: list[Path] = field(default_factory=list)
    asset_name: str = ""
    backup: Path | None = None  # zip com os saves da versão anterior
    carried: int = 0  # saves copiados para a pasta nova


def install(client: GitHubClient, release: Release, asset: Asset, game_name: str,
            install_root: Path, downloads_dir: Path, progress: Progress | None = None,
            cancel: threading.Event | None = None) -> InstallResult:
    """Baixa, verifica (SHA-256 quando publicado) e extrai em pasta própria da versão."""

    def rep(phase: str) -> Callable[[int, int], None]:
        return lambda d, t: progress(phase, d, t) if progress else None

    game_root = Path(install_root) / safe_name(game_name)
    folder = unique_dir(game_root / safe_name(release.tag or "versao"))
    archive = Path(downloads_dir) / safe_name(asset.name)

    client.download(asset.url, archive, rep("Baixando"), cancel, asset.size)
    try:
        expected = parse_digest(asset.digest)
        if expected:
            if progress:
                progress("Verificando", 0, 0)
            if sha256_file(archive, cancel) != expected:
                raise InstallError("O SHA-256 do arquivo baixado não confere. Instalação cancelada.")
        folder.mkdir(parents=True, exist_ok=True)
        try:
            extract(archive, folder, rep("Extraindo"), cancel)
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
    finally:
        try:
            archive.unlink()
        except OSError:
            pass

    candidates = find_executables(folder, game_name)
    return InstallResult(folder=folder, root=game_root,
                         exe=candidates[0] if candidates else None,
                         candidates=candidates, asset_name=asset.name)
