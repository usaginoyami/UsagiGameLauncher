from __future__ import annotations

import shutil
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import requests
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, QUrl, Signal, QObject
from PySide6.QtGui import (QKeySequence, QShortcut, QColor, QDesktopServices, QFont, QFontMetrics, QGuiApplication,
                           QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QSystemTrayIcon, QCheckBox, QComboBox, QDialog, QFileDialog,
                               QFormLayout, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListView, QListWidget, QListWidgetItem, QMainWindow, QMenu,
                               QMessageBox, QProgressBar, QPushButton, QSpinBox, QStyle,
                               QStyledItemDelegate, QTextBrowser, QVBoxLayout, QWidget)

from .. import (APP_NAME, __version__, backup, catalog, covers, installer, launcher, paths,
                safety, scanner, selfupdate, sorting, stars)
from ..catalog import Entry
from ..errors import Cancelled, GitHubError, RateLimitError
from ..github_api import GitHubClient, Release
from ..library import Library
from ..settings import Settings
from . import theme
from .cover_picker import CoverPickerDialog
from .edit_dialogs import NamePickerDialog, PlatformDialog, ScanDialog
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
    orig_name: str = ""  # nome do catálogo, antes de qualquer renomeação do usuário
    orig_platform: str = ""
    orig_target: str = ""

    @property
    def installed(self) -> bool:
        return self.rec is not None

    @property
    def has_update(self) -> bool:
        return bool(self.rec and self.release
                    and self.rec.get("version") not in ("manual", self.release.tag))


# ------------------------------------------------------------------- delegate
def safety_name(text: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in text).strip("_")[:60] or "backup"


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
        if row.entry.stars:
            label = "★ " + stars.format_stars(row.entry.stars)
            f.setPointSizeF(8)
            f.setBold(True)
            p.setFont(f)
            sw = QFontMetrics(f).horizontalAdvance(label)
            p.setPen(QColor(theme.GOLD))
            p.drawText(QRectF(r.right() - 8 - sw, y, sw, 20), Qt.AlignmentFlag.AlignVCenter, label)
            self._limit = r.right() - 14 - sw
            f.setBold(False)
            p.setFont(f)
        x += self._chip(p, x, y, row.entry.platform, "#33333f")
        if row.entry.target and row.entry.target.lower() != "windows":
            x += self._chip(p, x, y, "→ " + row.entry.target, "#2a3a55", "#a9c1ff")
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
    def _mark_restore(self) -> None:
        self._restore = True
        self.restore_btn.setEnabled(False)
        self.restore_btn.setText("Serão restaurados ao salvar")

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

        self._restore = False
        n_hidden = len(settings.get("hidden") or [])
        self.restore_btn = QPushButton(f"Restaurar jogos removidos ({n_hidden})")
        self.restore_btn.setEnabled(n_hidden > 0)
        self.restore_btn.clicked.connect(self._mark_restore)
        form.addRow("Lista", self.restore_btn)

        self.show_adult = QCheckBox("Mostrar jogos adultos ou sugestivos (escondidos por padrão)")
        self.show_adult.setChecked(bool(settings.get("show_adult")))
        form.addRow("", self.show_adult)

        self.safety = QCheckBox("Pedir confirmação antes de baixar projetos suspeitos ou desconhecidos")
        self.safety.setChecked(bool(settings.get("safety_confirm")))
        form.addRow("", self.safety)

        self.backup = QCheckBox("Fazer backup dos saves e levá-los para a versão nova ao atualizar")
        self.backup.setChecked(bool(settings.get("backup_saves")))
        form.addRow("", self.backup)

        self.notify = QCheckBox("Avisar quando um jogo instalado tiver atualização (faixa e aviso do sistema)")
        self.notify.setChecked(bool(settings.get("notify_updates")))
        form.addRow("", self.notify)
        self.check_hours = QSpinBox()
        self.check_hours.setRange(1, 72)
        self.check_hours.setSuffix(" h")
        self.check_hours.setValue(int(settings.get("update_check_hours") or 6))
        form.addRow("Verificar atualizações a cada", self.check_hours)

        self.self_update = QCheckBox("Avisar quando houver versão nova do launcher")
        self.self_update.setChecked(bool(settings.get("check_self_update")))
        form.addRow("", self.self_update)

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
            "show_adult": self.show_adult.isChecked(),
            **({"hidden": []} if self._restore else {}),
            "safety_confirm": self.safety.isChecked(),
            "backup_saves": self.backup.isChecked(),
            "notify_updates": self.notify.isChecked(),
            "update_check_hours": self.check_hours.value(),
            "check_self_update": self.self_update.isChecked(),
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
        self.fetching_stars: set[str] = set()
        self.stars = stars.StarCache(paths.data_dir() / "stars.json")
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
        self.b_self = QPushButton("")
        self.b_self.setObjectName("primary")
        self.b_self.hide()
        self.b_self.clicked.connect(self.apply_self_update)
        self.statusBar().addPermanentWidget(self.b_self)
        self._self_update: tuple | None = None
        self._f2 = QShortcut(QKeySequence("F2"), self.list)
        self._f2.activated.connect(lambda: self.rename_game(self.current))
        self._del = QShortcut(QKeySequence("Delete"), self.list)
        self._del.activated.connect(lambda: self.remove_game(self.current))
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self.check_installed_updates)
        if auto_refresh:
            hours = max(float(self.settings.get("update_check_hours") or 6), 0.5)
            self._update_timer.start(int(hours * 3600 * 1000))
            QTimer.singleShot(150, self.startup_refresh)
            QTimer.singleShot(1500, self.check_self_update)

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
        self.btn_locate = QPushButton("Localizar instalados")
        self.btn_settings = QPushButton("Configurações")
        self.btn_locate.clicked.connect(self.locate_installed)
        self.btn_refresh.clicked.connect(lambda: self.refresh_releases(force=True))
        self.btn_discover.clicked.connect(lambda: self.start_discover(manual=True))
        self.btn_add.clicked.connect(self.add_repo)
        self.btn_settings.clicked.connect(self.open_settings)
        for b in (self.btn_refresh, self.btn_discover, self.btn_add, self.btn_locate, self.btn_settings):
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
        self.sort = QComboBox()
        self.sort.setMinimumWidth(190)
        for mode, label in sorting.MODES:
            self.sort.addItem("Ordem: " + label.split(" (")[0] if mode == "default" else "Ordem: " + label, mode)
        saved = self.settings.get("sort_mode")
        self.sort.setCurrentIndex(max(self.sort.findData(saved), 0))
        self.sort.currentIndexChanged.connect(self._sort_changed)
        top.addWidget(self.sort)
        self._resort_timer = QTimer(self)
        self._resort_timer.setSingleShot(True)
        self._resort_timer.setInterval(500)
        self._resort_timer.timeout.connect(lambda: self.apply_filters(keep_scroll=True))
        center.addLayout(top)

        self.banner = QFrame()
        self.banner.setObjectName("panel")
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(14, 8, 10, 8)
        self.banner_text = QLabel("")
        self.banner_text.setWordWrap(True)
        self.banner_text.setTextFormat(Qt.TextFormat.RichText)
        self.banner_see = QPushButton("Ver")
        self.banner_all = QPushButton("Atualizar todos")
        self.banner_all.setObjectName("primary")
        self.banner_close = QPushButton("✕")
        self.banner_close.setFixedWidth(34)
        self.banner_see.clicked.connect(lambda: (self.sidebar.setCurrentRow(F_UPD), self.banner.hide()))
        self.banner_all.clicked.connect(self.update_all)
        self.banner_close.clicked.connect(self.banner.hide)
        bl.addWidget(self.banner_text, 1)
        bl.addWidget(self.banner_see)
        bl.addWidget(self.banner_all)
        bl.addWidget(self.banner_close)
        self.banner.hide()
        center.addWidget(self.banner)

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
        self.b_backup = QPushButton("Backup…")
        self.b_backup.clicked.connect(lambda: self.backups_dialog(self.current))
        self.b_have = QPushButton("Já tenho este jogo instalado…")
        self.b_have.clicked.connect(lambda: self.have_installed(self.current))
        r3 = QHBoxLayout()
        r3.addWidget(self.b_exe, 2)
        r3.addWidget(self.b_remove, 1)
        for r in (r1, r2, r3):
            dl.addLayout(r)
        dl.addWidget(self.b_backup)
        dl.addWidget(self.b_clean)
        dl.addWidget(self.b_have)
        lay.addWidget(self.detail)

        self.statusBar().showMessage("Pronto.")
        self.select(None)

    # ------------------------------------------------------------- dados
    def reload_entries(self) -> None:
        discovered, _ts = catalog.load_discovered()
        entries = catalog.merge(catalog.load_user(), catalog.load_bundled(), discovered,
                                set(self.settings.get("hidden")),
                                show_adult=bool(self.settings.get("show_adult")),
                                keep={r["repo"].lower() for r in self.library.all()})
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
        names = self.settings.get("custom_names") or {}
        for k, r in self.rows.items():
            r.rec = self.library.get(r.entry.repo)
            r.orig_name, r.orig_platform, r.orig_target = r.entry.name, r.entry.platform, r.entry.target
            if names.get(k):
                r.entry.name = str(names[k])
            ov = (self.settings.get("custom_platforms") or {}).get(k)
            if isinstance(ov, dict):
                r.entry.platform = ov.get("platform") or r.entry.platform
                r.entry.target = ov.get("target", r.entry.target)
            if not r.entry.stars:
                r.entry.stars = self.stars.get(k) or 0
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

    def apply_filters(self, keep_scroll: bool = False) -> None:
        scroll = self.list.verticalScrollBar().value() if keep_scroll else 0
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
        shown = sorting.sort_rows(shown, self.sort.currentData() or "default",
                                  lambda k: self.settings.has("favorites", k))
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
        if keep_scroll:
            self.list.verticalScrollBar().setValue(scroll)
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
                  self.b_clean, self.b_remove, self.b_have, self.b_backup):
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
        star_txt = f" · ★ {e.stars:,} no GitHub".replace(",", ".") if e.stars else ""
        self.d_repo.setText(f"<a style='color:{theme.RED}' href='{e.url}'>{e.repo}</a>{star_txt}")
        self.d_desc.setText(e.description or "Sem descrição.")
        self.d_note.setText(e.notes)
        self.d_note.setVisible(bool(e.notes))
        if key not in self.fetching and not row.loaded:
            self.fetch_release(key)  # sob demanda
        self.fetch_stars(key)
        self.render_detail()

    def render_detail(self) -> None:
        row = self.rows.get(self.current) if self.current else None
        if row is None:
            return
        key, e, rel = row.entry.key, row.entry, row.release
        route = e.platform if not e.target else f"{e.platform} → {e.target}"
        chips = [f"<span style='color:{theme.MUTED}'>{route}</span>"]
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
            if row.rec.get("version") == "manual":
                lines.append(f"<span style='color:{theme.MUTED}'>Instalado por você: a versão é desconhecida, "
                             "então não há aviso de atualização. “Instalar a última release” baixa numa pasta nova, "
                             "sem mexer nesta.</span>")
            if n > 1:
                lines.append(f"{n} versões mantidas no disco.")
            if row.rec.get("last_played"):
                lines.append(f"<b>Último jogo:</b> {fmt_date(row.rec['last_played'])}")
        self.d_info.setText("<br>".join(lines))
        self.d_notes.setMarkdown(rel.body if rel and rel.body else "")

        busy = key in self.active
        self.b_main.setText("Jogar" if row.installed else "Instalar")
        self.b_main.setEnabled(not busy and (row.installed or asset is not None))
        manual = bool(row.installed and row.rec.get("version") == "manual" and rel is not None)
        self.b_update.setText("Instalar a última release" if manual and not row.has_update else "Atualizar")
        self.b_update.setVisible(row.has_update or manual)
        self.b_update.setEnabled(not busy and asset is not None)
        self.b_web.setEnabled(True)
        self.b_folder.setVisible(row.installed)
        self.b_have.setVisible(not row.installed)
        self.b_backup.setVisible(row.installed)
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
                self.fetch_stars(key, force)

    def fetch_stars(self, key: str, force: bool = False) -> None:
        row = self.rows.get(key)
        if row is None or key in self.fetching_stars:
            return
        if not force and row.entry.stars and not self.stars.is_stale(key):
            return
        self.fetching_stars.add(key)
        repo = row.entry.repo

        def ok(info) -> None:
            self.fetching_stars.discard(key)
            n = int((info or {}).get("stargazers_count") or 0) if isinstance(info, dict) else 0
            if n or isinstance(info, dict):
                self.stars.set(key, n)
                row.entry.stars = n
                self._row_changed(key)

        def err(_e: Exception) -> None:
            self.fetching_stars.discard(key)

        self.runner.run(lambda task: self.client.repo_info(repo), ok, err)

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

    def _sort_changed(self, _i: int) -> None:
        self.settings.set("sort_mode", self.sort.currentData() or "default")
        self.settings.save()
        self.apply_filters()

    def _resort_later(self) -> None:
        """Estrelas e releases chegam aos poucos: reordena uma vez, sem a lista pular a cada resposta."""
        if (self.sort.currentData() or "default") in sorting.LIVE_MODES and not self._resort_timer.isActive():
            self._resort_timer.start()

    def _row_changed(self, key: str) -> None:
        self._resort_later()
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
        self.notify_updates()

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
        min_stars = int(self.settings.get("discover_min_stars"))
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

        self.runner.run(lambda task: catalog.discover(self.client, topics, min_stars), ok, err)

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
        target = (row.entry.target or "").strip()
        if target and target.lower() not in ("windows", "pc", "windows/linux") and not row.installed:
            if QMessageBox.question(
                    self, "Alvo diferente de Windows",
                    f"“{name}” foi portado para {target}, não para Windows. O pacote pode não rodar neste "
                    "computador.\n\nBaixar mesmo assim?") != QMessageBox.StandardButton.Yes:
                return
        if not self._confirm_safety(row, rel, asset):
            return
        downloads = paths.sub_dir("downloads")
        prev = dict(row.rec) if row.rec else None
        grouped = bool(prev and prev.get("version") != "manual" and prev.get("root"))
        root = Path(prev["root"]).parent if grouped else self.settings.install_dir()
        do_backup = bool(self.settings.get("backup_saves"))

        # pasta curta e estável (nome do repositório): renomear ou mudar o título não muda onde instala
        folder_name = Path(prev["root"]).name if grouped else row.entry.repo.split("/")[-1]

        def work(task: Task):
            res = installer.install(self.client, rel, asset, name, root, downloads,
                                    progress=task.report, cancel=task.cancel_event,
                                    folder_name=folder_name)
            old = Path(prev["folder"]) if prev and prev.get("folder") else None
            if do_backup and old and old.is_dir() and old.resolve() != res.folder.resolve():
                try:
                    task.report("Salvando backup", 0, 0)
                    res.backup = backup.make_backup(old, res.root / backup.BACKUP_DIR,
                                                    prev.get("version") or "anterior")
                    res.carried = backup.carry_forward(old, res.folder)
                except OSError:  # backup nunca pode impedir a atualização
                    pass
            return res

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
            extra = ""
            if res.carried:
                extra = f" · {res.carried} arquivo(s) de save levados para a versão nova"
            if res.backup:
                extra += f" · backup em {res.backup.name}"
            self.statusBar().showMessage(f"{name} {rel.tag} instalado em {res.folder}{extra}")
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

    # ------------------------------------------------- atualização do launcher
    def check_self_update(self, force: bool = False) -> None:
        if not force and not self.settings.get("check_self_update"):
            return
        last = float(self.settings.get("self_update_checked") or 0)
        if not force and time.time() - last < selfupdate.CHECK_EVERY and self._self_update is None:
            return

        def ok(found) -> None:
            self.settings.set("self_update_checked", time.time())
            self.settings.save()
            self._self_update = found
            if found:
                self.b_self.setText(f"Atualizar o launcher para {found[0].tag}")
                self.b_self.show()
            else:
                self.b_self.hide()

        self.runner.run(lambda task: selfupdate.find_update(self.client), ok, lambda e: None)

    def apply_self_update(self) -> None:
        if not self._self_update:
            return
        rel, asset = self._self_update
        if asset is None or not selfupdate.can_self_replace():
            QDesktopServices.openUrl(QUrl(rel.url))  # fonte ou pasta sem permissão: baixa à mão
            return
        if QMessageBox.question(
                self, "Atualizar o launcher",
                f"Baixar {rel.tag} e reiniciar o launcher agora?\n\nSeus jogos, saves e "
                "configurações não são alterados.") != QMessageBox.StandardButton.Yes:
            return
        work = paths.sub_dir("update")
        self.b_self.setEnabled(False)

        def work_fn(task: Task):
            return selfupdate.download_update(self.client, asset, work,
                                              lambda ph, d, t: task.report(ph, d, t), task.cancel_event)

        def on_progress(phase: str, done: int, total: int) -> None:
            pct = f" {done * 100 // total}%" if total else ""
            self.b_self.setText(f"{phase}{pct}")

        def ok(new_dir: Path) -> None:
            try:
                selfupdate.start_updater(new_dir, work)
            except OSError as e:
                self.b_self.setEnabled(True)
                QMessageBox.warning(self, "Falha ao atualizar", str(e))
                return
            QApplication.quit()

        def err(e: Exception) -> None:
            self.b_self.setEnabled(True)
            self.b_self.setText(f"Atualizar o launcher para {rel.tag}")
            QMessageBox.warning(self, "Falha ao atualizar", f"{e}\n\nNada foi alterado.")

        self.runner.run(work_fn, ok, err, on_progress)

    def _confirm_safety(self, row: "Row", rel: Release, asset) -> bool:
        """Pergunta antes de baixar quando há sinais de risco; 'Sim, confiar' lembra a escolha."""
        if not self.settings.get("safety_confirm"):
            return True
        key = row.entry.key
        if self.settings.has("trusted", key) or row.installed:
            return True
        findings = safety.assess(row.entry, rel, asset)
        if not safety.needs_confirmation(findings):
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Confirmar antes de baixar")
        box.setText(f"“{row.entry.name}” tem sinais que merecem atenção:")
        box.setInformativeText(
            safety.summary(findings)
            + f"\n\nO launcher vai baixar e abrir programas de {row.entry.repo}. Só continue se você "
              "conferiu o repositório e confia no autor.")
        go = box.addButton("Baixar mesmo assim", QMessageBox.ButtonRole.AcceptRole)
        repo_btn = box.addButton("Ver repositório", QMessageBox.ButtonRole.HelpRole)
        cancel = box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is repo_btn:
            QDesktopServices.openUrl(QUrl(row.entry.url))
            return False
        if clicked is go:
            self.settings.add_to("trusted", key)
            self.settings.save()
            return True
        return False

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
        if row.installed:
            menu.addAction("Fazer backup…", lambda: self.backup_now(key))
            menu.addAction("Backups e restauração…", lambda: self.backups_dialog(key))
        else:
            menu.addAction("Já tenho este jogo instalado…", lambda: self.have_installed(key))
        menu.addAction("Renomear…  (F2)", lambda: self.rename_game(key))
        menu.addAction("Procurar nome automático…", lambda: self.find_name(key))
        menu.addAction("Editar plataformas…", lambda: self.edit_platforms(key))
        if key in (self.settings.get("custom_names") or {}):
            menu.addAction("Restaurar nome original", lambda: self.rename_game(key, reset=True))
        menu.addAction("Escolher capa online…", lambda: self.pick_cover(key))
        menu.addAction("Definir capa de um arquivo…", lambda: self.set_cover(key))
        menu.addAction("Buscar capa de novo", lambda: self.refetch_cover(key))
        menu.addSeparator()
        if row.entry.source == "discovered":
            menu.addAction("Adicionar ao meu catálogo", lambda: self._adopt(row))
        menu.addAction("Remover da lista  (Del)", lambda: self.remove_game(key))
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def backups_dialog(self, key: str | None) -> None:
        row = self.rows.get(key) if key else None
        rec = row.rec if row else None
        if rec is None:
            return
        bdir = Path(rec.get("root", "")) / backup.BACKUP_DIR
        zips = backup.list_backups(bdir) if bdir.is_dir() else []
        box = QMessageBox(self)
        box.setWindowTitle("Backups")
        if zips:
            box.setText(f"{len(zips)} backup(s) automático(s) de {row.entry.name}. O mais recente: {zips[0].name}")
        else:
            box.setText(f"Ainda não há backups automáticos de {row.entry.name}. Eles são criados sozinhos a "
                        "cada atualização (se a opção estiver ligada nas Configurações).")
        box.setInformativeText("Fazer backup agora salva em um .zip no lugar que você escolher. Restaurar "
                               "copia os arquivos do .zip para a versão instalada, substituindo os atuais.")
        now = box.addButton("Fazer backup agora…", QMessageBox.ButtonRole.AcceptRole)
        restore = box.addButton("Restaurar o mais recente", QMessageBox.ButtonRole.ActionRole)
        restore.setEnabled(bool(zips))
        from_file = box.addButton("Restaurar de um arquivo…", QMessageBox.ButtonRole.ActionRole)
        open_dir = box.addButton("Abrir pasta", QMessageBox.ButtonRole.HelpRole)
        open_dir.setEnabled(bdir.is_dir())
        box.addButton("Fechar", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is now:
            self.backup_now(key)
        elif clicked is open_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(bdir)))
        elif clicked is restore:
            self._restore_zip(row, zips[0])
        elif clicked is from_file:
            f, _ = QFileDialog.getOpenFileName(self, "Restaurar backup", self.settings.get("last_backup_dir") or "",
                                               "Backups (*.zip)")
            if f:
                self._restore_zip(row, Path(f))

    def _restore_zip(self, row: "Row", zip_path: Path) -> None:
        if QMessageBox.question(
                self, "Restaurar backup",
                f"Restaurar {zip_path.name} em {row.entry.name}?\n\nOs arquivos do backup substituem os "
                "atuais na pasta instalada.") != QMessageBox.StandardButton.Yes:
            return
        try:
            n = backup.restore(zip_path, Path(row.rec["folder"]))
        except (OSError, ValueError, zipfile.BadZipFile) as e:
            QMessageBox.warning(self, "Falha ao restaurar", str(e))
            return
        self.statusBar().showMessage(f"{n} arquivo(s) restaurado(s) de {zip_path.name}.")

    def backup_now(self, key: str | None) -> None:
        """Backup sob demanda: só saves e configurações, ou o jogo inteiro, no destino escolhido."""
        row = self.rows.get(key) if key else None
        rec = row.rec if row else None
        if rec is None:
            return
        folder = Path(rec.get("folder", ""))
        if not folder.is_dir():
            QMessageBox.warning(self, "Backup", "A pasta do jogo não foi encontrada.")
            return
        size = backup.folder_size(folder)
        box = QMessageBox(self)
        box.setWindowTitle("Fazer backup")
        box.setText(f"O que guardar de {row.entry.name}?")
        box.setInformativeText(f"Só saves e configs: arquivo pequeno e rápido.\n"
                               f"O jogo inteiro: toda a pasta da versão instalada (cerca de {fmt_size(size)} "
                               "antes de compactar).")
        saves = box.addButton("Só saves e configs", QMessageBox.ButtonRole.AcceptRole)
        full_btn = box.addButton("O jogo inteiro", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked not in (saves, full_btn):
            return
        full = clicked is full_btn
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        tag = safety_name(f"{row.entry.name}_{rec.get('version', '')}_{'completo' if full else 'saves'}")
        start = str(Path(self.settings.get("last_backup_dir") or Path.home()) / f"{tag}_{stamp}.zip")
        dest, _ = QFileDialog.getSaveFileName(self, "Salvar backup como", start, "Backup (*.zip)")
        if not dest:
            return
        dest = Path(dest if dest.lower().endswith(".zip") else dest + ".zip")
        self.settings.set("last_backup_dir", str(dest.parent))
        self.settings.save()
        name = row.entry.name

        def work(task: Task):
            return backup.make_manual_backup(folder, dest, full, task.report, task.cancel_event)

        def prog(phase: str, done: int, total: int) -> None:
            pct = f" {done * 100 // total}%" if total else ""
            self.statusBar().showMessage(f"Backup de {name}: {phase}{pct}")

        def ok(res) -> None:
            self._backup_task = None
            path, n = res
            self.statusBar().showMessage(f"Backup de {name} salvo: {path} ({n} arquivos, {fmt_size(path.stat().st_size)})")
            if QMessageBox.question(self, "Backup pronto",
                                    f"{n} arquivo(s) salvos em:\n{path}\n\nAbrir a pasta?"
                                    ) == QMessageBox.StandardButton.Yes:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

        def err(e: Exception) -> None:
            self._backup_task = None
            self.statusBar().showMessage(f"Backup não concluído: {e}")
            QMessageBox.warning(self, "Backup não concluído", str(e))

        self._backup_task = self.runner.run(work, ok, err, prog)
        self.statusBar().showMessage(f"Fazendo backup de {name}…")

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

    def rename_game(self, key: str | None, reset: bool = False) -> None:
        """Nome de exibição só seu: não muda o repositório, o catálogo nem os jogos instalados."""
        row = self.rows.get(key) if key else None
        if row is None:
            return
        if reset:
            self._set_name(key, "")
            return
        new, ok = QInputDialog.getText(self, "Renomear jogo", "Nome do jogo (vazio = nome original):",
                                       text=row.entry.name)
        if ok:
            self._set_name(key, new)

    # ------------------------------------------------- jogos já instalados
    def _register(self, row: "Row", found: "scanner.Found") -> None:
        e = row.entry
        self.library.record_install(e.repo, e.name, found.version, str(found.folder),
                                    str(found.exe) if found.exe else None, str(found.root))
        row.rec = self.library.get(e.repo)

    def have_installed(self, key: str | None) -> None:
        """Liga um jogo da lista a uma pasta que já existe no disco."""
        row = self.rows.get(key) if key else None
        if row is None or row.installed:
            return
        start = str(self.settings.install_dir())
        folder = QFileDialog.getExistingDirectory(
            self, f"Pasta onde “{row.entry.name}” já está instalado", start)
        if not folder:
            return
        folder = Path(folder)
        if not scanner._has_game_exe(folder):
            inner = scanner.find_game_folders([folder])
            if inner:  # escolheu a pasta que agrupa as versões: usa a mais nova
                inner.sort(key=lambda p: scanner._vkey(p.name))
                folder = inner[-1]
        root, version, label = scanner.describe(folder)
        exes = installer.find_executables(folder, row.entry.name) if folder.is_dir() else []
        exe = exes[0] if exes else None
        if exe is None:
            f, _ = QFileDialog.getOpenFileName(self, "Escolher o executável do jogo", str(folder),
                                               "Executáveis (*.exe *.bat *.lnk);;Todos (*)")
            exe = Path(f) if f else None
        self._register(row, scanner.Found(folder, root, version, exe, label, row.entry.key, 1.0))
        self.reload_entries()
        self.apply_filters_keep_selection()
        self.render_detail()
        self.statusBar().showMessage(f"{row.entry.name} localizado em {folder}")

    def locate_installed(self) -> None:
        """Procura jogos no disco e deixa o usuário conferir antes de importar."""
        start = str(self.settings.install_dir()) if self.settings.install_dir().is_dir() \
            else str(Path.home())
        base = QFileDialog.getExistingDirectory(
            self, "Pasta onde estão seus jogos (procuro dentro dela, até 3 níveis)", start)
        if not base:
            return
        entries = [r.entry for r in self.rows.values() if not r.installed]
        known = {rec.get("folder", "") for rec in self.library.all()}
        orig = {k: r.orig_name for k, r in self.rows.items()}
        self.btn_locate.setEnabled(False)
        self.statusBar().showMessage("Procurando jogos no disco…")

        def ok(found: list) -> None:
            self.btn_locate.setEnabled(True)
            if not found:
                self.statusBar().showMessage("Nenhum jogo novo encontrado nessa pasta.")
                QMessageBox.information(self, "Localizar instalados",
                                        "Não achei pastas de jogo novas aí. Dica: escolha a pasta que contém as "
                                        "pastas dos jogos, ou use “Já tenho este jogo instalado…” no jogo.")
                return
            choices = sorted(((r.entry.key, r.entry.name) for r in self.rows.values() if not r.installed),
                             key=lambda t: t[1].casefold())
            dlg = ScanDialog(found, choices, self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            n = 0
            for f, key in dlg.selected():
                row = self.rows.get(key)
                if row is not None and not row.installed:
                    self._register(row, f)
                    n += 1
            self.reload_entries()
            self.apply_filters_keep_selection()
            self.render_detail()
            self.statusBar().showMessage(f"{n} jogo(s) importado(s) do disco.")

        def err(e: Exception) -> None:
            self.btn_locate.setEnabled(True)
            QMessageBox.warning(self, "Falha ao procurar", str(e))

        self.runner.run(lambda task: scanner.scan([Path(base)], entries, known, orig), ok, err)

    # ------------------------------------------- aviso de atualizações
    def _tray(self):
        if getattr(self, "_tray_icon", None) is None and QSystemTrayIcon.isSystemTrayAvailable():
            self._tray_icon = QSystemTrayIcon(theme.app_icon(), self)
            self._tray_icon.activated.connect(lambda _r: (self.showNormal(), self.activateWindow()))
            self._tray_icon.show()
        return getattr(self, "_tray_icon", None)

    def notify_updates(self) -> None:
        """Avisa uma vez por versão nova: faixa na janela e, se ela não está em foco, aviso do sistema."""
        if not self.settings.get("notify_updates"):
            return
        seen = dict(self.settings.get("notified_updates") or {})
        ups = [r for r in self.rows.values() if r.has_update]
        fresh = [r for r in ups if seen.get(r.entry.key) != r.release.tag]
        if not fresh:
            if not ups:
                self.banner.hide()
            return
        for r in fresh:
            seen[r.entry.key] = r.release.tag
        self.settings.set("notified_updates", seen)
        self.settings.save()
        names = ", ".join(f"<b>{r.entry.name}</b> ({r.rec.get('version', '?')} → {r.release.tag})"
                          for r in fresh[:3])
        more = f" e mais {len(fresh) - 3}" if len(fresh) > 3 else ""
        self.banner_text.setText(f"🔔 {len(ups)} jogo(s) com atualização: {names}{more}")
        self.banner.show()
        if not self.isActiveWindow():
            tray = self._tray()
            if tray is not None:
                tray.showMessage(f"{APP_NAME}: atualizações",
                                 "\n".join(f"{r.entry.name} → {r.release.tag}" for r in fresh[:5]),
                                 QSystemTrayIcon.MessageIcon.Information, 8000)

    def update_all(self) -> None:
        todo = [r.entry.key for r in self.rows.values() if r.has_update and r.entry.key not in self.active]
        if not todo:
            return
        if QMessageBox.question(self, "Atualizar todos",
                                f"Baixar e instalar {len(todo)} atualização(ões)?\n\n"
                                "Cada versão nova vai para uma pasta própria e os saves são copiados "
                                "(se o backup estiver ligado)."
                                ) != QMessageBox.StandardButton.Yes:
            return
        self.banner.hide()
        for key in todo:
            self.start_install(key)

    def check_installed_updates(self) -> None:
        """Verificação periódica enquanto o launcher fica aberto: só os jogos instalados."""
        for key, row in self.rows.items():
            if row.installed:
                self.fetch_release(key, force=True)

    def find_name(self, key: str) -> None:
        row = self.rows.get(key)
        if row is None:
            return
        dlg = NamePickerDialog(row.entry, self.cover_finder, self.runner, self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.chosen:
            self._set_name(key, dlg.chosen)

    def edit_platforms(self, key: str | None) -> None:
        row = self.rows.get(key) if key else None
        if row is None:
            return
        dlg = PlatformDialog(row.entry.platform, row.entry.target, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        platform, target = dlg.values()
        if target.lower() == "windows":
            target = ""
        over = dict(self.settings.get("custom_platforms") or {})
        if platform == row.orig_platform and target == row.orig_target:
            over.pop(key, None)  # igual ao original: não guarda nada
        else:
            over[key] = {"platform": platform, "target": target}
        self.settings.set("custom_platforms", over)
        self.settings.save()
        row.entry.platform, row.entry.target = platform, target
        self.reload_entries()
        self.apply_filters_keep_selection()
        if key == self.current:
            self.render_detail()
        self.statusBar().showMessage(f"{row.entry.name}: {platform} → {target or 'Windows'}")

    def remove_game(self, key: str | None) -> None:
        """Tira da lista. O que está instalado precisa ser desinstalado antes."""
        row = self.rows.get(key) if key else None
        if row is None:
            return
        if row.installed:
            QMessageBox.information(self, "Jogo instalado",
                                    f"“{row.entry.name}” está instalado. Use “Desinstalar” antes de removê-lo da lista.")
            return
        if QMessageBox.question(
                self, "Remover da lista",
                f"Remover “{row.entry.name}” da lista?\n\nNada é apagado do disco. Para trazer de volta, "
                "use “Restaurar jogos removidos” nas Configurações.") != QMessageBox.StandardButton.Yes:
            return
        if row.entry.source == "user":
            self._drop_user(row)
        else:
            self._hide(row)
        self.statusBar().showMessage(f"{row.entry.name} removido da lista.")

    def _set_name(self, key: str, new: str) -> None:
        row = self.rows[key]
        names = dict(self.settings.get("custom_names") or {})
        new = " ".join(new.split())[:80]
        if new and new != row.orig_name:
            names[key] = new
        else:
            names.pop(key, None)
        self.settings.set("custom_names", names)
        self.settings.save()
        row.entry.name = names.get(key) or row.orig_name
        self.list.viewport().update()
        self.apply_filters_keep_selection()
        if key == self.current:
            self.render_detail()
            self.d_title.setText(row.entry.name)
        self.statusBar().showMessage(f"Nome: {row.entry.name}")

    def pick_cover(self, key: str) -> None:
        row = self.rows.get(key)
        if row is None:
            return
        dlg = CoverPickerDialog(row.entry, self.cover_finder, self.runner, self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.chosen:
            try:
                self.covers.store.set_custom_bytes(key, dlg.chosen)
            except (ValueError, OSError) as e:
                QMessageBox.warning(self, "Capa inválida", str(e))
                return
            self._cover_changed(key)
            self.statusBar().showMessage(f"Capa de {row.entry.name} atualizada.")

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
        old_adult = bool(self.settings.get("show_adult"))
        old_hidden = list(self.settings.get("hidden") or [])
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
            if bool(self.settings.get("show_adult")) != old_adult or old_hidden != list(self.settings.get("hidden") or []):
                self.reload_entries()
                self.apply_filters()
            self.statusBar().showMessage("Configurações salvas.")

    # -------------------------------------------------------------- saída
    def closeEvent(self, event) -> None:
        for task in self.active.values():
            task.cancel_event.set()
        self.client.flush()
        self.settings.save()
        self.runner.wait(3000)
        super().closeEvent(event)
