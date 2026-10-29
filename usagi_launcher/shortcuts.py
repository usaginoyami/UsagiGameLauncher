"""Atalhos: .lnk no desktop e entrada de jogo fora da Steam (shortcuts.vdf).

Só funciona no Windows (PowerShell e registro). A lógica do arquivo da Steam é pura e testável.
"""
from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import sys
import zlib
from datetime import datetime
from pathlib import Path


class ShortcutError(Exception):
    pass


def _safe_file_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", name).strip(" .")
    return re.sub(r"\s+", " ", cleaned)[:80] or "Jogo"


# ------------------------------------------------------------------ desktop
LNK_SCRIPT = """$ErrorActionPreference = 'Stop'
$folder = [Environment]::GetFolderPath('Desktop')
$path = Join-Path $folder ($env:USAGI_NAME + '.lnk')
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($path)
$s.TargetPath = $env:USAGI_TARGET
$s.WorkingDirectory = $env:USAGI_DIR
$s.Arguments = $env:USAGI_ARGS
$s.IconLocation = $env:USAGI_TARGET
$s.Save()
Write-Output $path
"""


def create_desktop_shortcut(name: str, exe: str | os.PathLike, args: str = "") -> Path:
    if sys.platform != "win32":
        raise ShortcutError("Atalhos do desktop só funcionam no Windows.")
    exe = Path(exe)
    if not exe.is_file():
        raise ShortcutError("O executável do jogo não foi encontrado.")
    env = dict(os.environ, USAGI_NAME=_safe_file_name(name), USAGI_TARGET=str(exe),
               USAGI_DIR=str(exe.parent), USAGI_ARGS=args)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", LNK_SCRIPT],
                           env=env, capture_output=True, text=True, timeout=30, creationflags=flags)
    except (OSError, subprocess.SubprocessError) as e:
        raise ShortcutError(f"Não consegui rodar o PowerShell: {e}") from e
    out = (r.stdout or "").strip().splitlines()
    if r.returncode != 0 or not out:
        raise ShortcutError((r.stderr or "O Windows recusou criar o atalho.").strip()[:300])
    return Path(out[-1].strip())


# ------------------------------------------------------------------- Steam
# Formato binário do VDF: 0x00 = bloco, 0x01 = texto, 0x02 = inteiro de 32 bits, 0x08 = fim do bloco.
def _read_cstr(data: bytes, pos: int) -> tuple[str, int]:
    end = data.index(b"\x00", pos)
    return data[pos:end].decode("utf-8", "replace"), end + 1


def parse_vdf(data: bytes) -> dict:
    def block(pos: int) -> tuple[dict, int]:
        out: dict = {}
        while pos < len(data):
            kind = data[pos]
            pos += 1
            if kind == 8:
                return out, pos
            key, pos = _read_cstr(data, pos)
            if kind == 0:
                out[key], pos = block(pos)
            elif kind == 1:
                out[key], pos = _read_cstr(data, pos)
            elif kind == 2:
                out[key] = struct.unpack_from("<I", data, pos)[0]
                pos += 4
            else:
                raise ShortcutError("Arquivo de atalhos da Steam em formato desconhecido.")
        return out, pos

    try:
        parsed, _ = block(0)
    except (ValueError, struct.error, IndexError) as e:
        raise ShortcutError("Não consegui ler o arquivo de atalhos da Steam.") from e
    return parsed


def dump_vdf(d: dict) -> bytes:
    out = bytearray()

    def block(m: dict) -> None:
        for key, val in m.items():
            if isinstance(val, dict):
                out.append(0)
                out.extend(key.encode("utf-8") + b"\x00")
                block(val)
            elif isinstance(val, int):
                out.append(2)
                out.extend(key.encode("utf-8") + b"\x00" + struct.pack("<I", val & 0xFFFFFFFF))
            else:
                out.append(1)
                out.extend(key.encode("utf-8") + b"\x00" + str(val).encode("utf-8") + b"\x00")
        out.append(8)

    block(d)
    return bytes(out)


def steam_app_id(exe: str, name: str) -> int:
    return (zlib.crc32((exe + name).encode("utf-8")) & 0xFFFFFFFF) | 0x80000000


def add_to_vdf(data: bytes | None, name: str, exe: str, start_dir: str, args: str = "") -> tuple[bytes, bool]:
    """Acrescenta o jogo. Devolve (novo conteúdo, se acrescentou). Não duplica o mesmo jogo."""
    parsed = parse_vdf(data) if data else {}
    key = next((k for k in parsed if k.lower() == "shortcuts"), "shortcuts")
    shortcuts = parsed.get(key)
    if not isinstance(shortcuts, dict):
        shortcuts = {}
    quoted = f'"{exe}"'
    for item in shortcuts.values():
        if not isinstance(item, dict):
            continue
        have = {k.lower(): v for k, v in item.items()}
        if have.get("exe") == quoted and have.get("appname") == name:
            return (data or dump_vdf({key: shortcuts})), False
    nxt = max((int(k) for k in shortcuts if str(k).isdigit()), default=-1) + 1
    shortcuts[str(nxt)] = {
        "appid": steam_app_id(quoted, name), "AppName": name, "Exe": quoted,
        "StartDir": f'"{start_dir}"', "icon": exe, "ShortcutPath": "", "LaunchOptions": args,
        "IsHidden": 0, "AllowDesktopConfig": 1, "AllowOverlay": 1, "OpenVR": 0, "Devkit": 0,
        "DevkitGameID": "", "DevkitOverrideAppID": 0, "LastPlayTime": 0, "FlatpakAppID": "",
        "tags": {},
    }
    parsed[key] = shortcuts
    return dump_vdf(parsed), True


def steam_dir() -> Path | None:
    if sys.platform != "win32":
        return None
    try:
        import winreg  # type: ignore

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            value, _ = winreg.QueryValueEx(k, "SteamPath")
        p = Path(value)
        return p if p.is_dir() else None
    except OSError:
        return None


def steam_running() -> bool:
    if sys.platform != "win32":
        return False
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq steam.exe", "/NH"], capture_output=True,
                           text=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return False
    return "steam.exe" in (r.stdout or "").lower()


def add_to_steam(name: str, exe: str | os.PathLike, args: str = "", base: Path | None = None,
                 running: bool | None = None) -> list[Path]:
    """Adiciona o jogo à biblioteca da Steam de cada conta deste PC. Devolve os arquivos alterados."""
    exe = Path(exe)
    if not exe.is_file():
        raise ShortcutError("O executável do jogo não foi encontrado.")
    base = base or steam_dir()
    if base is None:
        raise ShortcutError("Não encontrei a Steam neste computador.")
    if (steam_running() if running is None else running):
        raise ShortcutError("Feche a Steam antes de adicionar o jogo: ela regrava esse arquivo ao sair "
                            "e apagaria a mudança.")
    users = [d for d in (base / "userdata").glob("*") if d.is_dir() and d.name.isdigit() and d.name != "0"]
    if not users:
        raise ShortcutError("Não achei nenhuma conta da Steam neste computador (abra a Steam uma vez).")
    changed: list[Path] = []
    for user in users:
        target = user / "config" / "shortcuts.vdf"
        data = target.read_bytes() if target.is_file() else None
        new, added = add_to_vdf(data, name, str(exe), str(exe.parent), args)
        if not added:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if data is not None:
            shutil.copy2(target, target.with_name(f"shortcuts.vdf.usagi-{datetime.now():%Y%m%d-%H%M%S}.bak"))
        tmp = target.with_suffix(".tmp")
        tmp.write_bytes(new)
        os.replace(tmp, target)
        changed.append(target)
    return changed
