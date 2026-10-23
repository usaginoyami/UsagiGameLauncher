from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path


def split_args(args: str) -> list[str]:
    if not args.strip():
        return []
    return shlex.split(args, posix=(os.name != "nt"))


def launch(exe: str | os.PathLike, args: str = "") -> subprocess.Popen | None:
    """Abre o jogo na pasta dele. Se o Windows exigir administrador (erro 740),
    delega ao ShellExecute, que mostra o pedido de elevação."""
    exe = Path(exe)
    try:
        return subprocess.Popen([str(exe), *split_args(args)], cwd=str(exe.parent))
    except OSError as e:
        if sys.platform == "win32" and getattr(e, "winerror", 0) == 740:
            os.startfile(str(exe))  # type: ignore[attr-defined]
            return None
        raise
