"""Backup e migração de saves ao atualizar um jogo.

Cada versão fica em pasta própria (Jogo\\v1.0, Jogo\\v1.1). Projetos que guardam os saves
junto do executável perderiam o progresso ao trocar de pasta. Antes de atualizar, o launcher:
  1. compacta os arquivos de save e de configuração da versão antiga em Jogo\\_backups\\*.zip
  2. copia os saves para a pasta da versão nova, sem sobrescrever nada que ela já tenha

Saves que o jogo grava fora da pasta (ex.: %APPDATA%) não são tocados por atualizações.
Só os arquivos de save migram; configurações ficam apenas no backup para não trazer
ajustes de uma versão antiga para uma nova.
"""
from __future__ import annotations

import os
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

SAVE_DIRS = {"save", "saves", "savedata", "save_data", "saved", "savegames", "save_games",
             "profiles", "profile", "memcards", "memory_cards", "memorycards", "sstates", "states"}
CONFIG_DIRS = {"config", "configs", "mods", "settings", "user", "userdata", "controller_mappings"}
SAVE_EXT = {".sav", ".srm", ".eep", ".sra", ".fla", ".mpk", ".mcr", ".mcd", ".mc", ".bin.sav",
            ".state", ".savestate"}
CONFIG_EXT = {".ini", ".toml", ".cfg", ".conf"}
MAX_FILE = 256 * 1024 * 1024
MAX_TOTAL = 2 * 1024 ** 3
KEEP_BACKUPS = 5
BACKUP_DIR = "_backups"


def _walk(folder: Path):
    for dirpath, dirnames, filenames in os.walk(folder):
        # nunca entra em links simbólicos: um link poderia apontar para fora da pasta do jogo
        dirnames[:] = [d for d in dirnames if not (Path(dirpath) / d).is_symlink()]
        for name in filenames:
            p = Path(dirpath) / name
            if not p.is_symlink():
                yield p


def classify(rel: Path) -> str | None:
    """'save', 'config' ou None para um caminho relativo à pasta do jogo."""
    parts = [x.lower() for x in rel.parts]
    dirs, name = parts[:-1], parts[-1]
    if any(d in SAVE_DIRS for d in dirs):
        return "save"
    if Path(name).suffix in SAVE_EXT:
        return "save"
    if any(d in CONFIG_DIRS for d in dirs):
        return "config"
    if Path(name).suffix in CONFIG_EXT and len(parts) <= 2:
        return "config"
    return None


def collect(folder: Path) -> dict[str, list[Path]]:
    """Arquivos relativos a `folder`, separados em 'save' e 'config'."""
    out: dict[str, list[Path]] = {"save": [], "config": []}
    total = 0
    folder = Path(folder)
    if not folder.is_dir():
        return out
    for p in _walk(folder):
        rel = p.relative_to(folder)
        kind = classify(rel)
        if not kind:
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size > MAX_FILE or total + size > MAX_TOTAL:
            continue
        total += size
        out[kind].append(rel)
    return out


def make_backup(old_folder: Path, backups_dir: Path, label: str) -> Path | None:
    """Zip com saves e configurações da versão antiga. None se não havia nada."""
    files = collect(old_folder)
    rels = files["save"] + files["config"]
    if not rels:
        return None
    backups_dir = Path(backups_dir)
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in label)[:40] or "versao"
    dest = backups_dir / f"{stamp}_{safe}.zip"
    tmp = dest.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in rels:
            z.write(Path(old_folder) / rel, rel.as_posix())
    os.replace(tmp, dest)
    prune(backups_dir)
    return dest


def prune(backups_dir: Path, keep: int = KEEP_BACKUPS) -> None:
    zips = sorted(Path(backups_dir).glob("*.zip"), key=lambda p: p.name, reverse=True)
    for old in zips[keep:]:
        try:
            old.unlink()
        except OSError:
            pass


def carry_forward(old_folder: Path, new_folder: Path) -> int:
    """Copia os saves da versão antiga para a nova sem sobrescrever. Devolve quantos copiou."""
    old_folder, new_folder = Path(old_folder), Path(new_folder)
    if not old_folder.is_dir() or old_folder.resolve() == new_folder.resolve():
        return 0
    n = 0
    for rel in collect(old_folder)["save"]:
        dest = new_folder / rel
        try:
            dest.resolve().relative_to(new_folder.resolve())
        except ValueError:
            continue
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old_folder / rel, dest)
        n += 1
    return n


def restore(backup_zip: Path, target: Path) -> int:
    """Restaura um backup por cima de `target` (usa o mesmo cuidado com caminhos do instalador)."""
    target = Path(target)
    n = 0
    with zipfile.ZipFile(backup_zip) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            dest = (target / info.filename).resolve()
            try:
                dest.relative_to(target.resolve())
            except ValueError:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
            n += 1
    return n


def list_backups(backups_dir: Path) -> list[Path]:
    return sorted(Path(backups_dir).glob("*.zip"), key=lambda p: p.name, reverse=True)


# ------------------------------------------------------------ backup manual
class BackupError(Exception):
    pass


def folder_size(folder: Path, skip_backups: bool = True) -> int:
    total = 0
    for p in _walk(Path(folder)):
        if skip_backups and BACKUP_DIR in p.relative_to(folder).parts[:-1]:
            continue
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return total


def _check_dest(folder: Path, dest: Path, needed: int) -> None:
    folder, dest = Path(folder).resolve(), Path(dest).resolve()
    try:
        dest.relative_to(folder)
    except ValueError:
        pass
    else:
        raise BackupError("O backup não pode ser salvo dentro da pasta do próprio jogo.")
    if dest.suffix.lower() != ".zip":
        raise BackupError("O arquivo de backup precisa terminar em .zip.")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(dest.parent).free
    except OSError as e:
        raise BackupError(f"Não consegui usar essa pasta: {e}") from e
    if needed and free < needed:
        raise BackupError(f"Pouco espaço no destino: o backup pode precisar de até {needed // 2**20} MB "
                          f"e há {free // 2**20} MB livres.")


def make_manual_backup(folder: Path, dest: Path, full: bool, progress=None, cancel=None) -> tuple[Path, int]:
    """Backup sob demanda: só saves/configs ou a pasta inteira. Devolve (zip, nº de arquivos).

    Grava num .part e só renomeia no fim, então um cancelamento ou erro nunca deixa um zip pela metade.
    """
    folder, dest = Path(folder), Path(dest)
    if not folder.is_dir():
        raise BackupError("A pasta do jogo não existe mais.")
    if full:
        rels = [p.relative_to(folder) for p in _walk(folder)
                if BACKUP_DIR not in p.relative_to(folder).parts[:-1]]
    else:
        c = collect(folder)
        rels = c["save"] + c["config"]
    if not rels:
        raise BackupError("Não encontrei saves nem configurações nesta pasta. "
                          "Use “O jogo inteiro” se quiser guardar tudo.")
    sizes = {}
    for rel in rels:
        try:
            sizes[rel] = (folder / rel).stat().st_size
        except OSError:
            sizes[rel] = 0
    total = sum(sizes.values())
    _check_dest(folder, dest, total)
    tmp = dest.with_name(dest.name + ".part")
    done = n = 0
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=3) as z:
            for rel in rels:
                if cancel is not None and cancel.is_set():
                    raise BackupError("Backup cancelado.")
                try:
                    z.write(folder / rel, rel.as_posix())
                except OSError:
                    continue  # arquivo sumiu ou está em uso: segue com o resto
                n += 1
                done += sizes[rel]
                if progress:
                    progress("Compactando", done, total)
        os.replace(tmp, dest)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return dest, n
