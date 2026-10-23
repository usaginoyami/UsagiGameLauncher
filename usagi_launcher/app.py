from __future__ import annotations

import argparse
import logging
import sys

from . import APP_NAME, paths


class OfflineClient:
    """Cliente sem rede para o modo --offline (usa só o que já está no disco)."""

    rate_remaining = None

    def set_token(self, token): ...
    def flush(self): ...

    def latest_release(self, *a, **k):
        from .errors import GitHubError
        raise GitHubError("Modo offline.")

    repo_info = search_repos = latest_release


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=APP_NAME)
    ap.add_argument("--data-dir", help="usa uma pasta de dados separada (outro perfil)")
    ap.add_argument("--offline", action="store_true", help="não consulta o GitHub")
    ap.add_argument("--smoke-test", metavar="PNG", help="renderiza a janela, salva o PNG e sai")
    args = ap.parse_args(argv)

    if args.data_dir:
        paths.set_data_dir(args.data_dir)
    logging.basicConfig(filename=str(paths.data_dir() / "launcher.log"), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from .github_api import GitHubClient
    from .library import Library
    from .settings import Settings
    from .ui import theme
    from .ui.main_window import MainWindow, http_avatar

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setStyleSheet(theme.QSS)
    app.setWindowIcon(theme.app_icon())

    settings = Settings()
    if args.offline:
        client = OfflineClient()
    else:
        client = GitHubClient(settings.get("github_token"),
                              cache_path=paths.data_dir() / "api_cache.json")
    win = MainWindow(client, settings, Library(),
                     avatar_fetcher=None if args.offline else http_avatar,
                     auto_refresh=not args.smoke_test)
    win.show()

    if args.smoke_test:
        def shoot() -> None:
            win.grab().save(args.smoke_test)
            app.quit()
        QTimer.singleShot(900, shoot)
    return app.exec()
