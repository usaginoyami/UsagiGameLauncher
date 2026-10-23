from __future__ import annotations


class GitHubError(Exception):
    """Erro genérico ao falar com o GitHub."""


class NotFoundError(GitHubError):
    """Repositório ou release não encontrado."""


class RateLimitError(GitHubError):
    """Limite de requisições da API do GitHub atingido."""

    def __init__(self, reset: int = 0):
        self.reset = reset
        super().__init__(
            "Limite da API do GitHub atingido. Adicione um token em Configurações "
            "para aumentar o limite."
        )


class InstallError(Exception):
    """Falha ao baixar, verificar ou extrair um jogo."""


class Cancelled(Exception):
    """Operação cancelada pelo usuário."""
