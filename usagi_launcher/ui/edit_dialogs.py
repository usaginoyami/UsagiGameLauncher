"""Janelas de edição: plataformas do jogo e busca automática do nome."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton, QVBoxLayout)

from .. import catalog
from ..covers import clean_title
from .tasks import TaskRunner


class PlatformDialog(QDialog):
    """Origem (console do jogo original) e destino (para onde o port foi feito)."""

    def __init__(self, platform: str, target: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Editar plataformas")
        self.setMinimumWidth(440)
        self.origin = QComboBox()
        self.origin.setEditable(True)
        self.origin.addItems(catalog.PLATFORMS)
        self.origin.setCurrentText(platform)
        self.dest = QComboBox()
        self.dest.setEditable(True)
        self.dest.addItems(catalog.TARGETS)
        self.dest.setCurrentText(target or catalog.DEFAULT_TARGET)

        hint = QLabel("Origem: o console do jogo original que foi recompilado.\n"
                      "Destino: onde o port roda (a maioria é Windows). Você pode digitar outro valor.")
        hint.setWordWrap(True)
        form = QFormLayout()
        form.addRow("Recompilado de", self.origin)
        form.addRow("Portado para", self.dest)
        ok, cancel = QPushButton("Salvar"), QPushButton("Cancelar")
        ok.setObjectName("primary")
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay = QVBoxLayout(self)
        lay.addWidget(hint)
        lay.addLayout(form)
        lay.addLayout(row)

    def values(self) -> tuple[str, str]:
        return (" ".join(self.origin.currentText().split()) or "Outros",
                " ".join(self.dest.currentText().split()) or catalog.DEFAULT_TARGET)


class NamePickerDialog(QDialog):
    """Procura o nome oficial do jogo (IGDB/Wikipedia) e deixa escolher. Resultado em `chosen`."""

    def __init__(self, entry, finder, runner: TaskRunner, parent=None):
        super().__init__(parent)
        self.entry, self.finder, self.runner = entry, finder, runner
        self.chosen = ""
        self._gen = 0
        self.setWindowTitle(f"Procurar nome: {entry.name}")
        self.resize(560, 440)

        base = entry.cover_title or clean_title(entry.repo.split("/")[-1].replace("-", " ").replace("_", " "))
        self.query = QLineEdit(base)
        self.query.returnPressed.connect(self.search)
        go = QPushButton("Buscar")
        go.clicked.connect(self.search)
        top = QHBoxLayout()
        top.addWidget(self.query, 1)
        top.addWidget(go)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _i: self.use())
        self.list.itemSelectionChanged.connect(lambda: self.use_btn.setEnabled(bool(self.list.selectedItems())))
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.use_btn = QPushButton("Usar este nome")
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

    def search(self) -> None:
        text = self.query.text().strip()
        if not text:
            return
        self._gen += 1
        gen = self._gen
        self.list.clear()
        self.use_btn.setEnabled(False)
        self.status.setText("Procurando…")
        platform = getattr(self.entry, "platform", "")

        def done(result) -> None:
            if gen != self._gen:
                return
            names, net_error = result
            if not names:
                msg = "Sem conexão com os serviços de nome." if net_error else \
                    "Nenhum nome encontrado. Tente outras palavras (o título original, em inglês)."
                if not self.finder.igdb:
                    msg += " Dica: com a chave da IGDB nas Configurações a busca acha muito mais."
                self.status.setText(msg)
                return
            self.status.setText(f"{len(names)} opção(ões). Os jogos da plataforma {platform or 'do card'} vêm primeiro.")
            for name, detail in names:
                item = QListWidgetItem(f"{name}    —    {detail}" if detail else name)
                item.setData(Qt.ItemDataRole.UserRole, name)
                self.list.addItem(item)

        self.runner.run(lambda task: self.finder.suggest_names(text, platform), done,
                        lambda e: self.status.setText(f"Falha na busca: {e}"))

    def use(self) -> None:
        items = self.list.selectedItems()
        if items:
            self.chosen = items[0].data(Qt.ItemDataRole.UserRole)
            self.accept()


class ScanDialog(QDialog):
    """Resultado da busca de jogos já instalados: o usuário confere e marca o que importar."""

    def __init__(self, found: list, choices: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem

        self.found = found
        self.setWindowTitle("Jogos encontrados no disco")
        self.resize(900, 460)
        self.table = QTableWidget(len(found), 4)
        self.table.setHorizontalHeaderLabels(["Importar", "Pasta no disco", "Versão", "É o jogo…"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.combos: list[QComboBox] = []
        self.checks: list[QTableWidgetItem] = []
        for i, f in enumerate(found):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(Qt.CheckState.Checked if f.match else Qt.CheckState.Unchecked)
            self.table.setItem(i, 0, chk)
            self.checks.append(chk)
            path = QTableWidgetItem(str(f.folder))
            path.setToolTip(str(f.folder))
            path.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(i, 1, path)
            ver = QTableWidgetItem(f.version if f.version != "manual" else "desconhecida")
            ver.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(i, 2, ver)
            combo = QComboBox()
            combo.addItem("— ignorar —", "")
            for key, label in choices:
                combo.addItem(label, key)
            if f.match:
                combo.setCurrentIndex(max(combo.findData(f.match), 0))
            elif f.alternatives:  # ambíguo: começa em "ignorar", o usuário escolhe
                combo.setToolTip("Mais de um jogo combina com esta pasta. Escolha o certo.")
            combo.currentIndexChanged.connect(lambda _i, n=i: self._combo_changed(n))
            self.table.setCellWidget(i, 3, combo)
            self.combos.append(combo)

        hint = QLabel(f"{len(found)} pasta(s) com jogo. Confira o nome sugerido, desmarque o que não quiser e "
                      "importe. Nada é copiado nem apagado: o launcher só passa a conhecer essas pastas. "
                      "Importados sem versão conhecida não avisam atualizações.")
        hint.setWordWrap(True)
        ok, cancel = QPushButton("Importar marcados"), QPushButton("Cancelar")
        ok.setObjectName("primary")
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay = QVBoxLayout(self)
        lay.addWidget(hint)
        lay.addWidget(self.table, 1)
        lay.addLayout(row)

    def _combo_changed(self, n: int) -> None:
        picked = bool(self.combos[n].currentData())
        self.checks[n].setCheckState(Qt.CheckState.Checked if picked else Qt.CheckState.Unchecked)

    def selected(self) -> list[tuple]:
        """[(Found, chave_do_jogo)] marcados, sem repetir o mesmo jogo duas vezes."""
        out, used = [], set()
        for i, f in enumerate(self.found):
            key = self.combos[i].currentData()
            if key and self.checks[i].checkState() == Qt.CheckState.Checked and key not in used:
                used.add(key)
                out.append((f, key))
        return out


class ExternalGameDialog(QDialog):
    """Jogo que não está no GitHub: nome, página de download, plataformas, descrição e um print."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Adicionar jogo de fora do GitHub")
        self.setMinimumWidth(500)
        self.cover: bytes | None = None
        self.name = QLineEdit()
        self.name.setPlaceholderText("Ex.: Azimuth")
        self.link = QLineEdit()
        self.link.setPlaceholderText("https://… (itch.io, site do projeto etc.)")
        self.origin = QComboBox()
        self.origin.setEditable(True)
        self.origin.addItems(catalog.PLATFORMS)
        self.dest = QComboBox()
        self.dest.setEditable(True)
        self.dest.addItems(catalog.TARGETS)
        self.desc = QLineEdit()
        self.desc.setPlaceholderText("Descrição curta (opcional)")
        self.pic = QLabel("Sem imagem")
        self.pic.setStyleSheet("color: #8b8b99;")
        pick = QPushButton("Escolher imagem…")
        paste = QPushButton("Colar print")
        pick.clicked.connect(self._pick)
        paste.clicked.connect(self._paste)
        pic_row = QHBoxLayout()
        pic_row.addWidget(self.pic, 1)
        pic_row.addWidget(pick)
        pic_row.addWidget(paste)

        form = QFormLayout()
        form.addRow("Nome", self.name)
        form.addRow("Link da página", self.link)
        form.addRow("Plataforma original", self.origin)
        form.addRow("Roda em", self.dest)
        form.addRow("Descrição", self.desc)
        form.addRow("Capa / print", pic_row)
        self.error = QLabel("")
        self.error.setStyleSheet("color: #f2b84b;")
        ok, cancel = QPushButton("Adicionar"), QPushButton("Cancelar")
        ok.setObjectName("primary")
        ok.clicked.connect(self._accept)
        cancel.clicked.connect(self.reject)
        btns = QHBoxLayout()
        btns.addWidget(self.error, 1)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addLayout(btns)

    def _set_image(self, data: bytes) -> None:
        from ..covers import MAX_BYTES, sniff
        if not sniff(data) or len(data) > MAX_BYTES:
            self.error.setText("Imagem inválida (use PNG, JPG, WEBP ou GIF de até 8 MB).")
            return
        self.cover = data
        self.error.setText("")
        self.pic.setText(f"Imagem pronta ({len(data) // 1024} KB)")

    def _pick(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        f, _ = QFileDialog.getOpenFileName(self, "Escolher imagem", "",
                                           "Imagens (*.png *.jpg *.jpeg *.webp *.gif)")
        if f:
            try:
                self._set_image(open(f, "rb").read())
            except OSError as e:
                self.error.setText(str(e))

    def _paste(self) -> None:
        data = clipboard_png()
        if data is None:
            self.error.setText("Não há imagem na área de transferência. Tire o print (Win+Shift+S) e tente de novo.")
        else:
            self._set_image(data)

    def _accept(self) -> None:
        if not self.name.text().strip():
            self.error.setText("Dê um nome ao jogo.")
            return
        link = self.link.text().strip()
        if link and not link.lower().startswith("https://"):
            self.error.setText("O link precisa começar com https://")
            return
        self.accept()

    def values(self) -> dict:
        return {"name": " ".join(self.name.text().split())[:80], "link": self.link.text().strip(),
                "platform": self.origin.currentText().strip() or "Outros",
                "target": self.dest.currentText().strip(),
                "description": self.desc.text().strip()[:600]}


def clipboard_png() -> bytes | None:
    """Imagem que está na área de transferência (um print), como PNG; None se não houver."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QGuiApplication
    img = QGuiApplication.clipboard().image()
    if img.isNull():
        return None
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)
