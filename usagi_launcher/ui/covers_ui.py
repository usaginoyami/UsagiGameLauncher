from __future__ import annotations

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from ..covers import CoverStore
from . import theme
from .tasks import TaskRunner


def compose(pm: QPixmap, w: int, h: int) -> QPixmap:
    """Encaixa a imagem no retângulo: banner preenche; capa vertical ganha fundo desfocado."""
    out = QPixmap(w, h)
    out.fill(QColor(theme.BG))
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    if pm.width() / max(pm.height(), 1) >= 1.25:
        s = pm.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                      Qt.TransformationMode.SmoothTransformation)
        p.drawPixmap((w - s.width()) // 2, (h - s.height()) // 2, s)
    else:
        tiny = pm.scaled(10, 10, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
        p.drawPixmap(0, 0, tiny.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation))
        p.fillRect(0, 0, w, h, QColor(0, 0, 0, 130))
        fit = pm.scaled(max(w - 12, 1), max(h - 8, 1), Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)
        p.drawPixmap((w - fit.width()) // 2, (h - fit.height()) // 2, fit)
    p.end()
    return out


class CoverCache(QObject):
    """Entrega capas já prontas e busca as que faltam em segundo plano, só das que aparecem."""

    updated = Signal(str)

    def __init__(self, store: CoverStore, runner: TaskRunner):
        super().__init__()
        self.store, self.runner = store, runner
        self._raw: dict[str, QPixmap] = {}
        self._comp: dict[tuple[str, int, int], QPixmap] = {}
        self._busy: set[str] = set()

    def reset(self, key: str) -> None:
        self._raw.pop(key, None)
        for k in [k for k in self._comp if k[0] == key]:
            del self._comp[k]

    def reset_all(self) -> None:
        self._raw.clear()
        self._comp.clear()

    def pixmap(self, entry) -> QPixmap | None:
        key = entry.key
        pm = self._raw.get(key)
        if pm is not None:
            return pm
        path = self.store.cached(key)
        if path is not None:
            pm = QPixmap(str(path))
            if not pm.isNull():
                self._raw[key] = pm
                return pm
            self.store.clear(key)  # arquivo corrompido: busca de novo
        if key not in self._busy and self.store.enabled and self.store.needs_fetch(key):
            self._busy.add(key)
            self.runner.run(lambda task, e=entry: self.store.fetch(e),
                            ok=lambda _p, k=key: self._done(k),
                            err=lambda _e, k=key: self._done(k))
        return None

    def _done(self, key: str) -> None:
        self._busy.discard(key)
        self.reset(key)
        self.updated.emit(key)

    def composed(self, entry, w: int, h: int) -> QPixmap | None:
        pm = self.pixmap(entry)
        if pm is None or w <= 0 or h <= 0:
            return None
        k = (entry.key, w, h)
        out = self._comp.get(k)
        if out is None:
            out = self._comp[k] = compose(pm, w, h)
        return out


def paint_cover(p: QPainter, rect: QRectF, cache: CoverCache, entry, radius: float = 8) -> None:
    p.save()
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    p.setClipPath(path)
    pm = cache.composed(entry, int(rect.width()), int(rect.height()))
    if pm is not None:
        p.drawPixmap(rect.topLeft(), pm)
    else:
        p.fillRect(rect, QColor(theme.BG))
        f = QFont(p.font())
        f.setPointSize(26)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(theme.RED))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, (entry.name[:1] or "?").upper())
    p.restore()
    p.save()
    p.setPen(QPen(QColor(theme.BORDER), 1))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(rect, radius, radius)
    p.restore()


class CoverView(QWidget):
    """Capa grande do painel de detalhes."""

    def __init__(self, cache: CoverCache, height: int = 170):
        super().__init__()
        self.cache, self.entry = cache, None
        self.setFixedHeight(height)

    def set_entry(self, entry) -> None:
        self.entry = entry
        self.update()

    def paintEvent(self, _event) -> None:
        if self.entry is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_cover(p, QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), self.cache, self.entry, 10)
        p.end()
