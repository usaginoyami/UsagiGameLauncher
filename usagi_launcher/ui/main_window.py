from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import requests
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, QUrl, Signal, QObject
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QFontMetrics, QGuiApplication,
                           QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog,
                               QFormLayout, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListView, QListWidget, QListWidgetItem, QMainWindow, QMenu,
                               QMessageBox, QProgressBar, QPushButton, QSpinBox, QStyle,
                               QStyledItemDelegate, QTextBrowser, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__, catalog, covers, installer, launcher, paths
from ..catalog import Entry
from ..errors import Cancelled, GitHubError, RateLimitError
from ..github_api import GitHubClient, Release
from ..library import Library
from ..settings import Settings
from . import theme
from .covers_ui import CoverCache, CoverView, paint_cover
from .tasks import Task, TaskRunner

CARD_W, CARD_H = 212, 196
COVER_H = 108

F_ALL, F_LIB, F_UPD, F_FAV, F_DISC = range(5)
FILTERS = [("Todos", F_ALL), ("Biblioteca", F_LIB), ("Atualizações", F_UPD),
           ("Favoritos", F_FAV), ("Descobertos", F_DISC)]


def fmt_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def fmt_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%d/%m/%Y")
    except ValueError:
        return ""


@dataclass
class Row:
    entry: Entry
    release: Release | None = None
    loaded: bool = False
    error: str = ""
    rec: dict | None = None

    @property
    def installed(self) -> bool:
        return self.rec is not None

    @property
    def has_update(self) -> bool:
        return bool(self.rec and self.release
                    and self.rec.get("version") not in ("manual", self.release.tag))


# ------------------------------------------------------------------- delegate
class CardDelegate(QStyledItemDelegate):
    def __init__(self, window: "MainWindow"):
        super().__init__(window.list)
        self.w = window

    def sizeHint(self, option, index) -> QSize:
        return QSize(CARD_W, CARD_H)

    _limit = 1e9  # borda direita: selos que não cabem no card são omitidos

    def _chip(self, p: QPainter, x: float, y: float, text: str, bg: str, fg: str = "white") -> float:
        f = QFont(p.font())
        f.setPointSizeF(8)
        f.setBold(True)
        p.setFont(f)
        w = QFontMetrics(f).horizontalAdvance(text) + 14
        if x + w > self._limit:
            return 0
        rect = QRectF(x, y, w, 18)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(bg))
        p.drawRoundedRect(rect, 9, 9)
        p.setPen(QColor(fg))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        return w + 6

    def paint(self, p: QPainter, option, index) -> None:
        key = index.data(Qt.ItemDataRole.UserRole)
        row = self.w.rows.get(key)
        if row is None:
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(option.rect).adjusted(4, 4, -4, -4)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        p.setBrush(QColor(theme.CARD_HOVER if hover or selected else theme.CARD))
        p.setPen(QPen(QColor(theme.RED if selected else theme.BORDER), 1.6 if selected else 1))
        p.drawRoundedRect(r, 10, 10)

        cover = QRectF(r.left() + 6, r.top() + 6, r.width() - 12, COVER_H)
        paint_cover(p, cover, self.w.covers, row.entry)

        tx = r.left() + 8
        tw = r.width() - 16
        f = QFont(p.font())
        f.setPointSizeF(10)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(theme.TEXT))
        name = QFontMetrics(f).elidedText(row.entry.name, Qt.TextElideMode.ElideRight, int(tw))
        p.drawText(QRectF(tx, cover.bottom() + 6, tw, 20), Qt.AlignmentFlag.AlignVCenter, name)

        f.setBold(False)
        f.setPointSizeF(8)
        p.setFont(f)
        p.setPen(QColor(theme.MUTED))
        sub = QFontMetrics(f).elidedText(row.entry.repo, Qt.TextElideMode.ElideRight, int(tw))
        p.drawText(QRectF(tx, cover.bottom() + 26, tw, 16), Qt.AlignmentFlag.AlignVCenter, sub)

        self._limit = r.right() - 8
        x, y = tx, cover.bottom() + 46
        x += self._chip(p, x, y, row.entry.platform, "#33333f")
        if key in self.w.active:
            x += self._chip(p, x, y, "Instalando…", theme.BLUE)
        elif row.has_update:
            x += self._chip(p, x, y, "Atualizar", theme.AMBER, "#1a1200")
        elif row.installed:
            x += self._chip(p, x, y, "Instalado", theme.GREEN, "#04210f")
        elif row.loaded and row.release is None and not row.error:
            x += self._chip(p, x, y, "Sem release", "#33333f", theme.MUTED)
        elif row.entry.source == "discovered":
            x += self._chip(p, x, y, "Descoberto", "#27324f", "#a9c1ff")
        if row.release is not None and row.release.prerelease and not row.installed:
            x += self._chip(p, x, y, "Pré-release", "#3a2a12", theme.AMBER)

        if self.w.settings.has("favorites", key):
            badge = QRectF(cover.right() - 26, cover.top() + 4, 22, 22)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 170))
            p.drawEllipse(badge)
            f.setPointSizeF(11)
            p.setFont(f)
            p.setPen(QColor(theme.GOLD))
            p.drawText(badge, Qt.AlignmentFlag.AlignCenter, "★")
        p.restore()


# ----------------------------------------------------------------- configurações
class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configurações")
        self.setMinimumWidth(520)
        self.s = settings
        form = QFormLayout()
        form.setSpacing(10)

        self.token = QLineEdit(settings.get("github_token"))
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("Opcional: sem permissões especiais, só aumenta o limite da API")
        form.addRow("Token do GitHub", self.token)

        self.install_dir = QLineEdit(str(settings.install_dir()))
        browse = QPushButton("Procurar…")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.install_dir, 1)
        row.addWidget(browse)
        form.addRow("Pasta dos jogos", row)

        self.pre = QCheckBox("Considerar pré-releases quando não houver versão estável")
        self.pre.setChecked(bool(settings.get("include_prereleases")))
        form.addRow("", self.pre)

        self.auto = QCheckBox("Descobrir novos projetos recomp no GitHub automaticamente")
        self.auto.setChecked(bool(settings.get("auto_discover")))
        form.addRow("", self.auto)

        self.topics = QLineEdit(", ".join(settings.get("discover_topics")))
        form.addRow("Tópicos da busca", self.topics)

        self.stars = QSpinBox()
        self.stars.setRange(0, 5000)
        self.stars.setValue(int(settings.get("discover_min_stars")))
        form.addRow("Mínimo de estrelas", self.stars)

        self.covers_on = QCheckBox("Buscar capas dos jogos (envia só o nome do jogo à Wikipedia)")
        self.covers_on.setChecked(bool(settings.get("covers_enabled")))
        form.addRow("", self.covers_on)

        self.sgdb = QLineEdit(settings.get("steamgriddb_key"))
        self.sgdb.setEchoMode(QLineEdit.EchoMode.Password)
        self.sgdb.setPlaceholderText("Opcional: capas melhores via steamgriddb.com")
        form.addRow("Chave SteamGridDB", self.sgdb)

        self.igdb_id = QLineEdit(settings.get("igdb_client_id"))
        self.igdb_id.setPlaceholderText("Opcional: Client ID da Twitch (capas via IGDB)")
        form.addRow("IGDB Client ID", self.igdb_id)
        self.igdb_secret = QLineEdit(settings.get("igdb_client_secret"))
        self.igdb_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.igdb_secret.setPlaceholderText("Client Secret (dev.twitch.tv/console, grátis)")
        form.addRow("IGDB Client Secret", self.igdb_secret)

        self.minimize = QCheckBox("Minimizar o launcher ao abrir um jogo")
        self.minimize.setChecked(bool(settings.get("minimize_on_launch")))
        form.addRow("", self.minimize)

        ok, cancel = QPushButton("Salvar"), QPushButton("Cancelar")
        ok.setObjectName("primary")
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        btns = QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addSpacing(8)
        lay.addLayout(btns)

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Pasta dos jogos", self.install_dir.text())
        if d:
            self.install_dir.setText(d)

    def values(self) -> dict:
        topics = [t.strip() for t in self.topics.text().split(",") if t.strip()]
        return {
            "github_token": self.token.text().strip(),
            "install_dir": self.install_dir.text().strip(),
            "include_prereleases": self.pre.isChecked(),
            "auto_discover": self.auto.isChecked(),
            "discover_topics": topics or ["recomp"],
            "discover_min_stars": self.stars.value(),
            "minimize_on_launch": self.minimize.isChecked(),
            "covers_enabled": self.covers_on.isChecked(),
            "steamgriddb_key": self.sgdb.text().strip(),
            "igdb_client_id": self.igdb_id.text().strip(),
            "igdb_client_secret": self.igdb_secret.text().strip(),
        }


# ------------------------------------------------------------------ janela
class MainWindow(QMainWindow):
    def __init__(self, client: GitHubClient, settings: Settings, library: Library,
                 runner: TaskRunner | None = None, cover_finder=None,
                 auto_refresh: bool = True):
        super().__init__()
        self.client, self.settings, self.library = client, settings, library
        self.runner = runner or TaskRunner()
        self.rows: dict[str, Row] = {}
        self.active: dict[str, Task] = {}
        self.progress: dict[str, tuple[str, int, int]] = {}
        self.fetching: set[str] = set()
        self.pending = 0
        self.discovering = False
        self.current: str | None = None
        self.cover_finder = cover_finder or covers.CoverFinder(
            sgdb_key=settings.get("steamgriddb_key"), enabled=bool(settings.get("covers_enabled")),
            igdb_id=settings.get("igdb_client_id"), igdb_secret=settings.get("igdb_client_secret"))
        self.covers = CoverCache(covers.CoverStore(paths.sub_dir("covers"), self.cover_finder),
                                 self.runner)
        self.covers.updated.connect(lambda _k: (self.list.viewport().update(), self.d_cover.update()))

        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.setWindowIcon(theme.app_icon())
        self.resize(1320, 820)
        self.setMinimumSize(1020, 640)
        self._build_ui()
        self.reload_entries()
        self.apply_filters()
        if auto_refresh:
            QTimer.singleShot(150, self.startup_refresh)

    # -------------------------------------------------------------- layout
    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        lay = QHBoxLayout(root)
        lay.setContentsMargins(14, 14, 14, 10)
        lay.setSpacing(14)

        # barra lateral
        side = QFrame()
        side.setObjectName("panel")
        side.setFixedWidth(220)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(12, 14, 12, 12)
        logo = QLabel("USAGI<br><span style='color:%s'>GAME LAUNCHER</span>" % theme.RED)
        logo.setTextFormat(Qt.TextFormat.RichText)
        logo.setStyleSheet("font-size: 14pt; font-weight: 800;")
        sl.addWidget(logo)
        sub = QLabel("Jogos recomp do GitHub")
        sub.setStyleSheet(f"color:{theme.MUTED}; font-size: 8.5pt;")
        sl.addWidget(sub)
        sl.addSpacing(10)
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        for label, _ in FILTERS:
            self.sidebar.addItem(label)
        self.sidebar.setCurrentRow(0)
        self.sidebar.currentRowChanged.connect(lambda _r: self.apply_filters())
        sl.addWidget(self.sidebar, 1)
        self.btn_refresh = QPushButton("Atualizar releases")
        self.btn_discover = QPushButton("Descobrir no GitHub")
        self.btn_add = QPushButton("Adicionar repositório")
        self.btn_settings = QPushButton("Configurações")
        self.btn_refresh.clicked.connect(lambda: self.refresh_releases(force=True))
        self.btn_discover.clicked.connect(lambda: self.start_discover(manual=True))
        self.btn_add.clicked.connect(self.add_repo)
        self.btn_settings.clicked.connect(self.open_settings)
        for b in (self.btn_refresh, self.btn_discover, self.btn_add, self.btn_settings):
            sl.addWidget(b)
        lay.addWidget(side)

        # centro
        center = QVBoxLayout()
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar por nome, repositório ou plataforma…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self.apply_filters())
        self.platform = QComboBox()
        self.platform.setMinimumWidth(190)
        self.platform.currentIndexChanged.connect(lambda _i: self.apply_filters())
        top.addWidget(self.search, 1)
        top.addWidget(self.platform)
        center.addLayout(top)

        self.list = QListWidget()
        self.list.setViewMode(QListView.ViewMode.IconMode)
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setMovement(QListView.Movement.Static)
        self.list.setUniformItemSizes(True)
        self.list.setGridSize(QSize(CARD_W, CARD_H))
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setMouseTracking(True)
        self.list.setItemDelegate(CardDelegate(self))
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self.item_menu)
        self.list.currentItemChanged.connect(lambda cur, _prev: self.select(
            cur.data(Qt.ItemDataRole.UserRole) if cur else None))
        self.list.itemDoubleClicked.connect(lambda it: self.primary_action(
            it.data(Qt.ItemDataRole.UserRole)))
        self.empty = QLabel("")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setStyleSheet(f"color:{theme.MUTED};")
        center.addWidget(self.list, 1)
        center.addWidget(self.empty)
        lay.addLayout(center, 1)

        # detalhes
        self.detail = QFrame()
        self.detail.setObjectName("panel")
        self.detail.setFixedWidth(380)
        dl = QVBoxLayout(self.detail)
        dl.setContentsMargins(18, 18, 18, 16)
        dl.setSpacing(8)
        self.d_cover = CoverView(self.covers)
        self.d_title = QLabel("Selecione um jogo")
        self.d_title.setWordWrap(True)
        self.d_title.setStyleSheet("font-size: 16pt; font-weight: 700;")
        self.d_repo = QLabel()
        self.d_repo.setOpenExternalLinks(True)
        self.d_repo.setStyleSheet(f"color:{theme.MUTED};")
        self.d_chips = QLabel()
        self.d_chips.setTextFormat(Qt.TextFormat.RichText)
        self.d_desc = QLabel()
        self.d_desc.setWordWrap(True)
        self.d_note = QLabel()
        self.d_note.setWordWrap(True)
        self.d_note.setStyleSheet(f"color:{theme.AMBER}; font-size: 9pt;")
        self.d_info = QLabel()
        self.d_info.setWordWrap(True)
        self.d_info.setTextFormat(Qt.TextFormat.RichText)
        self.d_notes = QTextBrowser()
        self.d_notes.setOpenExternalLinks(True)
        self.d_notes.setPlaceholderText("As notas da release aparecem aqui.")
        dl.addWidget(self.d_cover)
        for wdg in (self.d_title, self.d_repo, self.d_chips, self.d_desc, self.d_note, self.d_info):
            dl.addWidget(wdg)
        dl.addWidget(self.d_notes, 1)

        self.d_phase = QLabel()
        self.d_phase.setStyleSheet(f"color:{theme.MUTED}; font-size: 9pt;")
        self.d_bar = QProgressBar()
        self.d_cancel = QPushButton("Cancelar")
        self.d_cancel.clicked.connect(self.cancel_install)
        prow = QHBoxLayout()
        prow.addWidget(self.d_bar, 1)
        prow.addWidget(self.d_cancel)
        self.d_progress = QWidget()
        pl = QVBoxLayout(self.d_progress)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(4)
        pl.addWidget(self.d_phase)
        pl.addLayout(prow)
        dl.addWidget(self.d_progress)

        self.b_main = QPushButton("Instalar")
        self.b_main.setObjectName("primary")
        self.b_main.setMinimumHeight(40)
        self.b_main.clicked.connect(lambda: self.primary_action(self.current))
        self.b_update = QPushButton("Atualizar")
        self.b_update.clicked.connect(lambda: self.start_install(self.current))
        self.b_folder = QPushButton("Abrir pasta")
        self.b_folder.clicked.connect(self.open_folder)
        self.b_web = QPushButton("Releases ↗")
        self.b_web.clicked.connect(self.open_releases)
        self.b_fav = QPushButton("★ Favorito")
        self.b_fav.clicked.connect(self.toggle_favorite)
        self.b_exe = QPushButton("Escolher executável")
        self.b_exe.clicked.connect(self.choose_exe)
        self.b_clean = QPushButton("Limpar versões antigas")
        self.b_clean.clicked.connect(self.clean_old)
        self.b_remove = QPushButton("Desinstalar")
        self.b_remove.setObjectName("danger")
        self.b_remove.clicked.connect(self.uninstall)

        r1 = QHBoxLayout()
        r1.addWidget(self.b_main, 2)
        r1.addWidget(self.b_update, 1)
        r2 = QHBoxLayout()
        r2.addWidget(self.b_folder)
        r2.addWidget(self.b_web)
        r2.addWidget(self.b_fav)
        r3 = QHBoxLayout()
        r3.addWidget(self.b_exe, 2)
        r3.addWidget(self.b_remove, 1)
        for r in (r1, r2, r3):
            dl.addLayout(r)
        dl.addWidget(self.b_clean)
        lay.addWidget(self.detail)

        self.statusBar().showMessage("Pronto.")
        self.select(None)

    # ------------------------------------------------------------- dados
    def reload_entries(self) -> None:
        discovered, _ts = catalog.load_discovered()
        entries = catalog.merge(catalog.load_user(), catalog.load_bundled(), discovered,
                                set(self.settings.get("hidden")))
        old = self.rows
        self.rows = {}
        for e in entries:
            r = old.get(e.key) or Row(e)
            r.entry = e
            self.rows[e.key] = r
        for rec in self.library.all():  # instalados que saíram de todos os catálogos
            k = rec["repo"].lower()
            if k not in self.rows:
                self.rows[k] = Row(Entry(repo=rec["repo"], name=rec.get("name", ""), source="user"))
        for r in self.rows.values():
            r.rec = self.library.get(r.entry.repo)
        plats = sorted({r.entry.platform for r in self.rows.values()},
                       key=lambda p: catalog.PLATFORMS.index(p) if p in catalog.PLATFORMS else 99)
        current = self.platform.currentText()
        self.platform.blockSignals(True)
        self.platform.clear()
        self.platform.addItem("Todas as plataformas")
        self.platform.addItems(plats)
        idx = self.platform.findText(current)
        self.platform.setCurrentIndex(max(idx, 0))
        self.platform.blockSignals(False)

    def _passes(self, row: Row, flt: int) -> bool:
        key = row.entry.key
        return (flt == F_ALL or (flt == F_LIB and row.installed)
                or (flt == F_UPD and row.has_update)
                or (flt == F_FAV and self.settings.has("favorites", key))
                or (flt == F_DISC and row.entry.source == "discovered"))

    def apply_filters(self) -> None:
        flt = FILTERS[max(self.sidebar.currentRow(), 0)][1]
        plat = self.platform.currentText()
        text = self.search.text().strip().lower()
        shown = []
        for row in self.rows.values():
            e = row.entry
            if not self._passes(row, flt):
                continue
            if plat and not plat.startswith("Todas") and e.platform != plat:
                continue
            if text and text not in f"{e.name} {e.repo} {e.platform} {e.description}".lower():
                continue
            shown.append(row)
        shown.sort(key=lambda r: (not r.has_update, not r.installed,
                                  not self.settings.has("favorites", r.entry.key),
                                  r.entry.source == "discovered", r.entry.name.casefold()))
        keep = self.current
        self.list.blockSignals(True)
        self.list.clear()
        sel_item = None
        for row in shown:
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, row.entry.key)
            it.setSizeHint(QSize(CARD_W, CARD_H))
            it.setToolTip(row.entry.description or row.entry.repo)
            self.list.addItem(it)
            if row.entry.key == keep:
                sel_item = it
        self.list.blockSignals(False)
        if sel_item is not None:
            self.list.setCurrentItem(sel_item)
        elif shown:
            self.list.setCurrentRow(0)
        else:
            self.select(None)
        self.empty.setText("" if shown else self._empty_text(flt))
        self.empty.setVisible(not shown)
        self._update_counts()

    def _empty_text(self, flt: int) -> str:
        return {F_LIB: "Nenhum jogo instalado ainda. Escolha um jogo e clique em Instalar.",
                F_UPD: "Tudo em dia.",
                F_FAV: "Marque jogos com ★ Favorito para vê-los aqui.",
                F_DISC: "Nada descoberto ainda. Use “Descobrir no GitHub”."}.get(
            flt, "Nenhum jogo encontrado com esses filtros.")

    def _update_counts(self) -> None:
        for i, (label, flt) in enumerate(FILTERS):
            n = sum(1 for r in self.rows.values() if self._passes(r, flt))
            self.sidebar.item(i).setText(f"{label}   {n}")

    # ----------------------------------------------------------- detalhes
    def select(self, key: str | None) -> None:
        self.current = key
        row = self.rows.get(key) if key else None
        self.d_cover.setVisible(row is not None)
        for w in (self.b_main, self.b_update, self.b_folder, self.b_web, self.b_fav, self.b_exe,
                  self.b_clean, self.b_remove):
            w.setVisible(row is not None)
        if row is None:
            self.d_title.setText("Selecione um jogo")
            for w in (self.d_repo, self.d_chips, self.d_desc, self.d_note, self.d_info):
                w.setText("")
            self.d_notes.clear()
            self.d_progress.hide()
            return
        e = row.entry
        self.d_cover.set_entry(e)
        self.d_title.setText(e.name)
        stars = f" · ★ {e.stars:,}".replace(",", ".") if e.stars else ""
        self.d_repo.setText(f"<a style='color:{theme.RED}' href='{e.url}'>{e.repo}</a>{stars}")
        self.d_desc.setText(e.description or "Sem descrição.")
        self.d_note.setText(e.notes)
        self.d_note.setVisible(bool(e.notes))
        if key not in self.fetching and not row.loaded:
            self.fetch_release(key)  # sob demanda
        self.render_detail()

    def render_detail(self) -> None:
        row = self.rows.get(self.current) if self.current else None
        if row is None:
            return
        key, e, rel = row.entry.key, row.entry, row.release
        chips = [f"<span style='color:{theme.MUTED}'>{e.platform}</span>"]
        if row.installed:
            chips.append(f"<span style='color:{theme.GREEN}'>Instalado {row.rec.get('version', '')}</span>")
        if row.has_update:
            chips.append(f"<span style='color:{theme.AMBER}'>Atualização: {rel.tag}</span>")
        self.d_chips.setText("  ·  ".join(chips))

        lines = []
        asset = None
        if rel is not None:
            asset = installer.pick_asset(rel.assets, e.asset_hint)
            kind = "pré-release" if rel.prerelease else "estável"
            lines.append(f"<b>Última release:</b> {rel.tag} ({kind}) · {fmt_date(rel.published_at)}")
            if asset:
                lines.append(f"<b>Pacote Windows:</b> {asset.name} · {fmt_size(asset.size)}")
            else:
                lines.append(f"<span style='color:{theme.AMBER}'>Nenhum pacote Windows reconhecido. "
                             "Use “Releases ↗” para baixar manualmente.</span>")
        elif row.error:
            lines.append(f"<span style='color:{theme.AMBER}'>{row.error}</span>")
        elif row.loaded:
            lines.append(f"<span style='color:{theme.MUTED}'>Este repositório ainda não publicou releases.</span>")
        elif key in self.fetching:
            lines.append(f"<span style='color:{theme.MUTED}'>Consultando o GitHub…</span>")
        if row.installed:
            n = len(row.rec.get("versions", []))
            lines.append(f"<b>Pasta:</b> {row.rec.get('folder', '')}")
            if n > 1:
                lines.append(f"{n} versões mantidas no disco.")
            if row.rec.get("last_played"):
                lines.append(f"<b>Último jogo:</b> {fmt_date(row.rec['last_played'])}")
        self.d_info.setText("<br>".join(lines))
        self.d_notes.setMarkdown(rel.body if rel and rel.body else "")

        busy = key in self.active
        self.b_main.setText("Jogar" if row.installed else "Instalar")
        self.b_main.setEnabled(not busy and (row.installed or asset is not None))
        self.b_update.setVisible(row.has_update)
        self.b_update.setEnabled(not busy and asset is not None)
        self.b_web.setEnabled(True)
        self.b_folder.setVisible(row.installed)
        self.b_exe.setVisible(row.installed)
        self.b_remove.setVisible(row.installed)
        self.b_clean.setVisible(bool(row.installed and len(row.rec.get("versions", [])) > 1))
        fav = self.settings.has("favorites", key)
        self.b_fav.setText("★ Favorito" if fav else "☆ Favoritar")
        self.d_progress.setVisible(busy)
        if busy:
            self._show_progress(key)

    def _show_progress(self, key: str) -> None:
        phase, done, total = self.progress.get(key, ("Preparando", 0, 0))
        self.d_phase.setText(f"{phase}…" + (f"  {fmt_size(done)} / {fmt_size(total)}"
                                            if total and phase == "Baixando" else ""))
        if total > 0:
            self.d_bar.setRange(0, 100)
            self.d_bar.setValue(int(done * 100 / total))
        else:
            self.d_bar.setRange(0, 0)

    # ------------------------------------------------------------ releases
    def startup_refresh(self) -> None:
        self.refresh_releases()
        hours = float(self.settings.get("discover_hours") or 24)
        _items, ts = catalog.load_discovered()
        if self.settings.get("auto_discover") and time.time() - ts > hours * 3600:
            self.start_discover()

    def refresh_releases(self, force: bool = False) -> None:
        # Sem token o limite é 60 req/h: com um catálogo grande, só busca o que importa
        # (instalados, favoritos, o selecionado). O resto carrega ao clicar.
        has_token = "Authorization" in self.client.session.headers
        favs = {str(f).lower() for f in (self.settings.get("favorites") or [])}
        for key, row in self.rows.items():
            if force or has_token or row.installed or key in favs or key == self.current:
                self.fetch_release(key, force)

    def fetch_release(self, key: str, force: bool = False) -> None:
        row = self.rows.get(key)
        if row is None or key in self.fetching or (row.loaded and not force):
            return
        self.fetching.add(key)
        self.pending += 1
        allow = bool(self.settings.get("include_prereleases"))
        repo = row.entry.repo

        def done() -> None:
            self.fetching.discard(key)
            self.pending -= 1
            row.loaded = True
            if self.pending <= 0:
                self.client.flush()
                self._after_refresh()

        def ok(rel: Release | None) -> None:
            row.release, row.error = rel, ""
            done()
            self._row_changed(key)

        def err(e: Exception) -> None:
            row.error = str(e)
            done()
            if isinstance(e, RateLimitError):
                self.statusBar().showMessage(str(e))
            self._row_changed(key)

        self.runner.run(lambda task: self.client.latest_release(repo, allow), ok, err)
        self.statusBar().showMessage("Consultando releases no GitHub…")

    def _row_changed(self, key: str) -> None:
        self.list.viewport().update()
        if key == self.current:
            self.render_detail()
        self._update_counts()

    def _after_refresh(self) -> None:
        ups = sum(1 for r in self.rows.values() if r.has_update)
        msg = f"{ups} atualização(ões) disponível(is)." if ups else "Releases em dia."
        if self.client.rate_remaining is not None and self.client.rate_remaining < 10:
            msg += f"  Limite da API quase no fim ({self.client.rate_remaining})."
        self.statusBar().showMessage(msg)
        self.apply_filters_keep_selection()

    def apply_filters_keep_selection(self) -> None:
        if self.sidebar.currentRow() in (F_UPD,):
            self.apply_filters()
        else:
            self._update_counts()

    # ----------------------------------------------------------- descoberta
    def start_discover(self, manual: bool = False) -> None:
        if self.discovering:
            return
        self.discovering = True
        self.btn_discover.setEnabled(False)
        topics = list(self.settings.get("discover_topics"))
        stars = int(self.settings.get("discover_min_stars"))
        self.statusBar().showMessage("Procurando projetos recomp no GitHub…")

        def finish() -> None:
            self.discovering = False
            self.btn_discover.setEnabled(True)
            self.client.flush()

        def ok(res) -> None:
            items, error = res
            finish()
            old, _ts = catalog.load_discovered()
            merged = {e.key: e for e in old}
            merged.update({e.key: e for e in items})
            if items:
                catalog.save_discovered(sorted(merged.values(), key=lambda e: -e.stars))
                self.reload_entries()
                self.apply_filters()
            msg = f"{len(items)} projeto(s) encontrado(s)."
            if error is not None:
                msg = f"{msg} Busca incompleta: {error}"
            self.statusBar().showMessage(msg)

        def err(e: Exception) -> None:
            finish()
            self.statusBar().showMessage(f"Falha na busca: {e}")

        self.runner.run(lambda task: catalog.discover(self.client, topics, stars), ok, err)

    # ------------------------------------------------------------- ações
    def primary_action(self, key: str | None) -> None:
        row = self.rows.get(key) if key else None
        if row is None:
            return
        if row.installed:
            self.play(key)
        else:
            self.start_install(key)

    def start_install(self, key: str | None) -> None:
        row = self.rows.get(key) if key else None
        if row is None or key in self.active or row.release is None:
            return
        asset = installer.pick_asset(row.release.assets, row.entry.asset_hint)
        if asset is None:
            QMessageBox.information(self, "Sem pacote Windows",
                                    "Não encontrei um pacote Windows nesta release. "
                                    "Abra a página de releases para baixar manualmente.")
            return
        rel, name, repo = row.release, row.entry.name, row.entry.repo
        root = self.settings.install_dir()
        downloads = paths.sub_dir("downloads")

        def work(task: Task):
            return installer.install(self.client, rel, asset, name, root, downloads,
                                     progress=task.report, cancel=task.cancel_event)

        def on_progress(phase: str, done: int, total: int) -> None:
            self.progress[key] = (phase, done, total)
            if key == self.current:
                self._show_progress(key)

        def finish() -> None:
            self.active.pop(key, None)
            self.progress.pop(key, None)

        def ok(res: installer.InstallResult) -> None:
            finish()
            self.library.record_install(repo, name, rel.tag, str(res.folder),
                                        str(res.exe) if res.exe else None, str(res.root))
            row.rec = self.library.get(repo)
            self.statusBar().showMessage(f"{name} {rel.tag} instalado em {res.folder}")
            self.reload_entries()
            self.apply_filters()
            if res.exe is None:
                QMessageBox.information(
                    self, "Quase lá",
                    "O jogo foi instalado, mas não achei um executável. Use “Escolher executável” "
                    "(alguns projetos pedem para preparar os arquivos do jogo antes).")

        def err(e: Exception) -> None:
            finish()
            if isinstance(e, Cancelled):
                self.statusBar().showMessage("Instalação cancelada.")
            else:
                self.statusBar().showMessage(f"Falha ao instalar: {e}")
                QMessageBox.warning(self, "Falha ao instalar", str(e))
            self._row_changed(key)

        self.progress[key] = ("Preparando", 0, 0)
        self.active[key] = self.runner.run(work, ok, err, on_progress)
        self.statusBar().showMessage(f"Instalando {name}…")
        self._row_changed(key)

    def cancel_install(self) -> None:
        task = self.active.get(self.current or "")
        if task:
            task.cancel_event.set()

    def play(self, key: str) -> None:
        row = self.rows[key]
        rec = row.rec
        if rec is None:
            return
        exe = Path(rec.get("exe") or "")
        if not exe.is_file():
            folder = Path(rec.get("folder", ""))
            found = installer.find_executables(folder, rec.get("name", "")) if folder.is_dir() else []
            if not found:
                QMessageBox.warning(self, "Executável não encontrado",
                                    "Use “Escolher executável” para indicar o arquivo do jogo.")
                return
            exe = found[0]
            self.library.set_exe(rec["repo"], str(exe))
        try:
            launcher.launch(exe, rec.get("args", ""))
        except OSError as e:
            QMessageBox.critical(self, "Não foi possível abrir o jogo", str(e))
            return
        self.library.mark_played(rec["repo"])
        self.statusBar().showMessage(f"Abrindo {row.entry.name}…")
        if self.settings.get("minimize_on_launch"):
            self.showMinimized()
        self.render_detail()

    def open_folder(self) -> None:
        row = self.rows.get(self.current or "")
        if row and row.rec and Path(row.rec.get("folder", "")).is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(row.rec["folder"]))

    def open_releases(self) -> None:
        row = self.rows.get(self.current or "")
        if row:
            QDesktopServices.openUrl(QUrl(row.release.html_url if row.release else row.entry.url + "/releases"))

    def toggle_favorite(self) -> None:
        if self.current:
            self.settings.toggle("favorites", self.current)
            self.settings.save()
            self.list.viewport().update()
            self.render_detail()
            self._update_counts()

    def choose_exe(self) -> None:
        row = self.rows.get(self.current or "")
        if not row or not row.rec:
            return
        start = row.rec.get("folder", "")
        f, _ = QFileDialog.getOpenFileName(self, "Escolher executável", start,
                                           "Executáveis (*.exe *.bat *.lnk);;Todos (*)")
        if f:
            self.library.set_exe(row.rec["repo"], f)
            self.render_detail()

    def clean_old(self) -> None:
        row = self.rows.get(self.current or "")
        if not row or not row.rec:
            return
        old = [v["folder"] for v in row.rec.get("versions", [])
               if v.get("version") != row.rec.get("version")]
        if not old:
            return
        ans = QMessageBox.question(
            self, "Limpar versões antigas",
            "Apagar as pastas das versões antigas?\n\n" + "\n".join(old) +
            "\n\nSaves e configurações guardados dentro delas serão perdidos.",
            defaultButton=QMessageBox.StandardButton.No)
        if ans == QMessageBox.StandardButton.Yes:
            for folder in self.library.drop_old_versions(row.rec["repo"]):
                shutil.rmtree(folder, ignore_errors=True)
            self.render_detail()

    def uninstall(self) -> None:
        row = self.rows.get(self.current or "")
        if not row or not row.rec:
            return
        root = row.rec.get("root") or row.rec.get("folder", "")
        ans = QMessageBox.question(
            self, "Desinstalar",
            f"Apagar {row.entry.name} do disco?\n\n{root}\n\n"
            "Saves e configurações guardados dentro dessa pasta serão perdidos.",
            defaultButton=QMessageBox.StandardButton.No)
        if ans != QMessageBox.StandardButton.Yes:
            return
        if root and Path(root).is_dir():
            shutil.rmtree(root, ignore_errors=True)
        self.library.remove(row.entry.repo)
        row.rec = None
        self.statusBar().showMessage(f"{row.entry.name} removido.")
        self.reload_entries()
        self.apply_filters()

    # --------------------------------------------------- catálogo do usuário
    def add_repo(self) -> None:
        text, ok = QInputDialog.getText(self, "Adicionar repositório",
                                        "Cole o link do GitHub ou escreva dono/repositório:")
        if not ok or not text.strip():
            return
        repo = catalog.parse_repo(text)
        if not repo:
            QMessageBox.warning(self, "Repositório inválido",
                                "Use o formato dono/repositório ou um link github.com.")
            return
        if repo.lower() in self.rows and self.rows[repo.lower()].entry.source != "discovered":
            self.statusBar().showMessage("Esse repositório já está na lista.")
            return

        def ok_cb(info: dict) -> None:
            text = " ".join([info.get("name", ""), info.get("description") or "",
                             " ".join(info.get("topics") or [])])
            e = Entry(repo=info.get("full_name", repo), name=catalog.pretty_name(info.get("name", repo)),
                      platform=catalog.guess_platform(text), description=info.get("description") or "",
                      source="user", stars=int(info.get("stargazers_count") or 0),
                      pushed_at=info.get("pushed_at") or "")
            user = [u for u in catalog.load_user() if u.key != e.key] + [e]
            catalog.save_user(user)
            self.reload_entries()
            self.apply_filters()
            self.statusBar().showMessage(f"{e.name} adicionado ao catálogo.")
            self.fetch_release(e.key)

        def err_cb(e: Exception) -> None:
            QMessageBox.warning(self, "Não foi possível adicionar", str(e))

        self.runner.run(lambda task: self.client.repo_info(repo), ok_cb, err_cb)

    def item_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        row = self.rows[key]
        menu = QMenu(self)
        if row.installed:
            menu.addAction("Jogar", lambda: self.play(key))
        else:
            menu.addAction("Instalar", lambda: self.start_install(key))
        fav = self.settings.has("favorites", key)
        menu.addAction("Remover dos favoritos" if fav else "Favoritar", lambda: self._fav(key))
        menu.addAction("Abrir no GitHub", lambda: QDesktopServices.openUrl(QUrl(row.entry.url)))
        menu.addAction("Copiar link", lambda: QGuiApplication.clipboard().setText(row.entry.url))
        menu.addSeparator()
        menu.addAction("Definir capa…", lambda: self.set_cover(key))
        menu.addAction("Buscar capa de novo", lambda: self.refetch_cover(key))
        if row.entry.source == "discovered":
            menu.addSeparator()
            menu.addAction("Adicionar ao meu catálogo", lambda: self._adopt(row))
            menu.addAction("Ocultar", lambda: self._hide(row))
        elif row.entry.source == "user" and not row.installed:
            menu.addSeparator()
            menu.addAction("Remover do meu catálogo", lambda: self._drop_user(row))
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def _fav(self, key: str) -> None:
        self.settings.toggle("favorites", key)
        self.settings.save()
        self.list.viewport().update()
        self._update_counts()
        if key == self.current:
            self.render_detail()

    def _adopt(self, row: Row) -> None:
        row.entry.source = "user"
        user = [u for u in catalog.load_user() if u.key != row.entry.key] + [row.entry]
        catalog.save_user(user)
        self.reload_entries()
        self.apply_filters()

    def _hide(self, row: Row) -> None:
        self.settings.add_to("hidden", row.entry.key)
        self.settings.save()
        self.reload_entries()
        self.apply_filters()

    def _drop_user(self, row: Row) -> None:
        catalog.save_user([u for u in catalog.load_user() if u.key != row.entry.key])
        self.reload_entries()
        self.apply_filters()

    def set_cover(self, key: str) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Escolher capa", "",
                                           "Imagens (*.png *.jpg *.jpeg *.webp *.gif)")
        if not f:
            return
        try:
            self.covers.store.set_custom(key, f)
        except (ValueError, OSError) as e:
            QMessageBox.warning(self, "Capa inválida", str(e))
            return
        self._cover_changed(key)

    def refetch_cover(self, key: str) -> None:
        self.covers.store.clear(key)
        self._cover_changed(key)
        self.statusBar().showMessage("Buscando a capa de novo…")

    def _cover_changed(self, key: str) -> None:
        self.covers.reset(key)
        self.list.viewport().update()
        self.d_cover.update()

    def open_settings(self) -> None:
        def cover_cfg():
            return (self.settings.get("steamgriddb_key"), bool(self.settings.get("covers_enabled")),
                    self.settings.get("igdb_client_id"), self.settings.get("igdb_client_secret"))

        old = cover_cfg()
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.settings.update(dlg.values())
            self.settings.save()
            self.client.set_token(self.settings.get("github_token"))
            new = cover_cfg()
            self.cover_finder.configure(*new)
            if new != old:  # nova chave ou capas ligadas: tenta de novo o que falhou antes
                self.covers.store.clear_failures()
                self.covers.reset_all()
                self.list.viewport().update()
            self.statusBar().showMessage("Configurações salvas.")

    # -------------------------------------------------------------- saída
    def closeEvent(self, event) -> None:
        for task in self.active.values():
            task.cancel_event.set()
        self.client.flush()
        self.settings.save()
        self.runner.wait(3000)
        super().closeEvent(event)
