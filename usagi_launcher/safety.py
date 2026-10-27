"""Avaliação de risco antes de baixar e executar um pacote de um repositório.

O launcher baixa e roda programas de terceiros. Nada aqui prova que um projeto é seguro ou
perigoso: são sinais para pedir confirmação ao usuário quando algo foge do normal.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

LOW, MEDIUM, HIGH = 1, 2, 3

_EXEC_EXT = (".exe", ".msi", ".bat", ".cmd", ".scr", ".com", ".vbs", ".ps1", ".jar", ".lnk")
_DOUBLE_EXT = re.compile(r"\.(pdf|jpe?g|png|txt|docx?|zip|rar|mp4)\.(exe|scr|bat|cmd|com|lnk)$", re.I)
_BAD_WORDS = re.compile(r"(crack|keygen|key-?gen|activator|cheat|hack(?:ed)?|loader|injector|"
                        r"unlocker|patcher|bypass)", re.I)
# conta com cara de gerada: palavras coladas + número no fim (ex.: "cynthyfibrillose74")
_GENERATED_OWNER = re.compile(r"^[a-z]{9,}\d{2,4}$", re.I)
# nomes típicos de "port nativo" falso de jogos muito famosos
_FAKE_PORT_NAME = re.compile(r"(native|pc|unity)[-_ ]?(port|rebuild)", re.I)


@dataclass(frozen=True)
class Finding:
    level: int
    text: str


def _age_days(iso: str) -> int | None:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max((datetime.now(timezone.utc) - dt).days, 0)


def assess(entry, release=None, asset=None) -> list[Finding]:
    """Devolve os sinais encontrados, do mais grave ao menos grave."""
    out: list[Finding] = []
    owner, repo_name = entry.repo.split("/", 1) if "/" in entry.repo else ("", entry.repo)

    if asset is not None:
        name = asset.name
        if _DOUBLE_EXT.search(name):
            out.append(Finding(HIGH, f"O arquivo “{name}” tem extensão dupla, um truque comum de malware."))
        if _BAD_WORDS.search(name):
            out.append(Finding(HIGH, f"O nome do pacote (“{name}”) lembra crack, cheat ou loader."))
        if name.lower().endswith(_EXEC_EXT):
            out.append(Finding(MEDIUM, f"O pacote é um programa direto (“{name}”), não um zip com o jogo."))
        if not getattr(asset, "digest", ""):
            out.append(Finding(LOW, "A release não publica SHA-256, então não dá para conferir o arquivo."))

    if entry.source == "discovered":
        out.append(Finding(MEDIUM, "Projeto encontrado pela busca automática, não conferido por você."))
        if entry.stars < 25:
            out.append(Finding(MEDIUM, f"Poucas estrelas ({entry.stars}): pouca gente usou ou revisou."))
        age = _age_days(getattr(entry, "created_at", ""))
        if age is not None and age < 30:
            out.append(Finding(MEDIUM, f"Repositório criado há {age} dias."))

    if _GENERATED_OWNER.match(owner):
        level = MEDIUM if entry.source == "discovered" else LOW
        out.append(Finding(level, f"O nome da conta (“{owner}”) parece gerado automaticamente."))
    if _FAKE_PORT_NAME.search(repo_name) and entry.source == "discovered":
        out.append(Finding(MEDIUM, "Nome no padrão de “port nativo” de jogo famoso, comum em repositórios falsos."))
    if "pequeno ou de conta pouco conhecida" in (entry.notes or ""):
        out.append(Finding(LOW, "Projeto pequeno ou de conta pouco conhecida."))

    if release is not None and getattr(release, "prerelease", False):
        out.append(Finding(LOW, "Esta é uma pré-release."))

    out.sort(key=lambda f: -f.level)
    return out


def top_level(findings: list[Finding]) -> int:
    return max((f.level for f in findings), default=0)


def needs_confirmation(findings: list[Finding]) -> bool:
    """Só interrompe o usuário por sinais médios ou graves; os leves ficam no painel."""
    return top_level(findings) >= MEDIUM


def summary(findings: list[Finding]) -> str:
    return "\n".join(f"• {f.text}" for f in findings if f.level >= LOW)
