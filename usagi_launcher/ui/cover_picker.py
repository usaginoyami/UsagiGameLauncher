"""Janela para escolher a capa certa entre várias opções (IGDB, SteamGridDB, Wikipedia)."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget,
                               QListWidgetItem, QPushButton, QVBoxLayout)

from ..covers import Candidate, clean_title
from .tasks import TaskRunner

THUMB = QSize(120, 160)


class CoverPickerDialog(QDialog):
    """Mostra opções para o jogo; o resultado fica em `chosen` (bytes da imagem) ao aceitar."""

    def __init__(self, entry, finder, runner: TaskRunner, parent=None):
        super().__init__(parent)
        self.entry, self.finder, self.runner = entry, finder, runner
        self.chosen: bytes | None = None
        self._gen = 0  # descarta respostas de buscas antigas
        self.setWindowTitle(f"Escolher capa: {entry.name}")
        self.resize(760, 560)

        self.query = QLineEdit(entry.cover_title or clean_title(entry.name))
        self.query.setPlaceholderText("Nome do jogo original (ex.: The Legend of Zelda: The Wind Waker)")
        self.query.returnPressed.connect(self.search)
        go = QPushButton("Buscar")
        go.clicked.connect(self.search)
        top = QHBoxLayout()
        top.addWidget(self.query, 1)
        top.addWidget(go)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.list = QListWidget()
        self.list.setViewMode(QListView.ViewMode.IconMode)
        self.list.setIconSize(THUMB)
        self.list.setGridSize(QSize(150, 214))
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setMovement(QListView.Movement.Static)
        self.list.setWordWrap(True)
        self.list.setSpacing(4)
        self.list.itemDoubleClicked.connect(lambda _i: self.use())
        self.list.itemSelectionChanged.connect(self._sel)

        self.use_btn = QPushButton("Usar esta capa")
        self.use_btn.setObjectName("primary")
        self.use_btn.setEnabled(False)
        self.use_btn.clicked.connect(self.use)
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        bottom.addWidget(cancel)
        bottom.addWidget(self.use_btn)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.list, 1)
        lay.addLayout(bottom)
        self.search()

    # ------------------------------------------------------------ busca
    def search(self) -> None:
        text = self.query.text().strip()
        if not text:
            return
        self._gen += 1
        gen = self._gen
        self.list.clear()
        self.use_btn.setEnabled(False)
        self.status.setText("Buscando opções…")
        platform = getattr(self.entry, "platform", "")

        def done(result) -> None:
            if gen != self._gen:
                return
            cands, net_error = result
            if not cands:
                msg = "Sem conexão com os serviços de capa." if net_error else \
                    "Nenhuma opção encontrada. Tente outro nome (o do jogo original, em inglês)."
                if not self.finder.igdb:
                    msg += " Dica: com a chave da IGDB nas Configurações aparecem muito mais opções."
                self.status.setText(msg)
                return
            self.status.setText(f"{len(cands)} opção(ões). Clique na certa e em “Usar esta capa”.")
            for c in cands:
                self._add(c, gen)

        self.runner.run(lambda task: self.finder.candidates(text, platform), done,
                        lambda e: self.status.setText(f"Falha na busca: {e}"))

    def _add(self, cand: Candidate, gen: int) -> None:
        item = QListWidgetItem(cand.label)
        item.setData(Qt.ItemDataRole.UserRole, cand)
        item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.list.addItem(item)

        def fetch(task):
            return self.finder.download(cand.thumb)

        def show(data) -> None:
            if gen != self._gen or not data:
                return
            pm = QPixmap()
            if pm.loadFromData(data):
                item.setIcon(QIcon(pm.scaled(THUMB, Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation)))

        self.runner.run(fetch, show, lambda e: None)

    def _sel(self) -> None:
        self.use_btn.setEnabled(bool(self.list.selectedItems()))

    # ------------------------------------------------------------ escolha
    def use(self) -> None:
        items = self.list.selectedItems()
        if not items:
            return
        cand: Candidate = items[0].data(Qt.ItemDataRole.UserRole)
        self.use_btn.setEnabled(False)
        self.status.setText("Baixando a capa…")

        def fetch(task):
            return self.finder.download(cand.full)

        def ok(data) -> None:
            if not data:
                self.status.setText("Não consegui baixar essa imagem. Escolha outra.")
                self._sel()
                return
            self.chosen = data
            self.accept()

        def err(e) -> None:
            self.status.setText(f"Falha ao baixar: {e}")
            self._sel()

        self.runner.run(fetch, ok, err)
