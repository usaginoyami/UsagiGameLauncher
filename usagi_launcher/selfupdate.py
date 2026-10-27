"""Atualização do próprio launcher pelas releases do repositório dele no GitHub.

No .exe (PyInstaller), baixa o zip, confere o SHA-256 quando publicado, extrai numa pasta
temporária e entrega a troca para um script .bat que espera o launcher fechar, copia os
arquivos por cima e abre a versão nova. Rodando pelo código-fonte, só abre a página da release.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from typing import Callable

from . import __version__, installer
from .errors import InstallError
from .github_api import Asset, GitHubClient, Release

REPO = "usaginoyami/UsagiGameLauncher"
EXE_NAME = "UsagiGameLauncher.exe"
CHECK_EVERY = 12 * 3600


def parse_version(tag: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", tag.split("-")[0])
    return tuple(int(n) for n in nums[:4]) or (0,)


def is_newer(tag: str, current: str = __version__) -> bool:
    a, b = parse_version(tag), parse_version(current)
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def find_update(client: GitHubClient) -> tuple[Release, Asset | None] | None:
    """Release estável mais nova que a versão em uso, com o pacote Windows (se houver)."""
    rel = client.latest_release(REPO, allow_prerelease=False)
    if rel is None or not is_newer(rel.tag):
        return None
    return rel, installer.pick_asset(rel.assets, "windows|win")


def can_self_replace() -> bool:
    """Só no .exe empacotado e com permissão de escrita na pasta do programa."""
    if not getattr(sys, "frozen", False):
        return False
    app_dir = Path(sys.executable).resolve().parent
    try:
        with tempfile.NamedTemporaryFile(dir=app_dir, prefix=".w", delete=True):
            pass
    except OSError:
        return False
    return True


def download_update(client: GitHubClient, asset: Asset, work_dir: Path,
                    progress: Callable[[str, int, int], None] | None = None,
                    cancel: threading.Event | None = None) -> Path:
    """Baixa e extrai; devolve a pasta que contém o executável novo."""
    work_dir = Path(work_dir)
    shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    archive = work_dir / installer.safe_name(asset.name)
    client.download(asset.url, archive, (lambda d, t: progress("Baixando", d, t)) if progress else None,
                    cancel, asset.size)
    expected = installer.parse_digest(asset.digest)
    if expected and installer.sha256_file(archive, cancel) != expected:
        raise InstallError("O SHA-256 da atualização não confere. Nada foi alterado.")
    out = work_dir / "novo"
    out.mkdir()
    installer.extract(archive, out, (lambda d, t: progress("Extraindo", d, t)) if progress else None, cancel)
    archive.unlink(missing_ok=True)
    for exe in out.rglob(EXE_NAME):
        return exe.parent
    raise InstallError(f"A atualização não contém {EXE_NAME}. Nada foi alterado.")


def build_updater_script(new_dir: Path, app_dir: Path, pid: int) -> str:
    """Script .bat: espera o launcher fechar, copia por cima, reabre e limpa."""
    new_dir, app_dir = Path(new_dir), Path(app_dir)
    exe = app_dir / EXE_NAME
    return "\r\n".join([
        "@echo off",
        "chcp 65001 >nul",
        "setlocal",
        f'set "SRC={new_dir}"',
        f'set "DST={app_dir}"',
        ":wait",
        f'tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul',
        "if not errorlevel 1 (",
        "  timeout /t 1 /nobreak >nul",
        "  goto wait",
        ")",
        'xcopy "%SRC%\\*" "%DST%\\" /E /Y /I /Q >nul',
        "if errorlevel 1 (",
        '  echo A atualizacao falhou ao copiar os arquivos. > "%DST%\\update_failed.txt"',
        ")",
        f'start "" "{exe}"',
        'rmdir /S /Q "%SRC%" >nul 2>&1',
        '(goto) 2>nul & del "%~f0"',
        "",
    ])


def start_updater(new_dir: Path, work_dir: Path) -> None:
    """Grava e dispara o .bat (só Windows). O chamador deve fechar o launcher logo depois."""
    import subprocess

    app_dir = Path(sys.executable).resolve().parent
    bat = Path(work_dir) / "aplicar_atualizacao.bat"
    bat.write_text(build_updater_script(new_dir, app_dir, os.getpid()), encoding="utf-8")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(["cmd.exe", "/c", str(bat)], creationflags=flags, close_fds=True)
