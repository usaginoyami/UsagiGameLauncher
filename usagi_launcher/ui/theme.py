from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPixmap

# Identidade vermelho e preto do UsagiNoYami
BG = "#0c0c0f"
PANEL = "#131318"
CARD = "#1a1a21"
CARD_HOVER = "#22222b"
BORDER = "#2b2b36"
RED = "#e11d2e"
RED_DARK = "#a80f1d"
TEXT = "#ececf1"
MUTED = "#8b8b99"
GREEN = "#22c55e"
AMBER = "#f59e0b"
BLUE = "#5b8def"
GOLD = "#fbbf24"

QSS = f"""
QWidget {{ background: {BG}; color: {TEXT}; font-family: 'Segoe UI', 'Noto Sans', sans-serif; font-size: 10pt; }}
QLabel {{ background: transparent; }}
QMainWindow, QDialog {{ background: {BG}; }}
QFrame#panel {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#panel QLabel {{ background: transparent; }}
QLineEdit, QComboBox, QSpinBox, QTextBrowser {{
    background: {CARD}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 10px;
    selection-background-color: {RED};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 1px solid {RED}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {CARD}; border: 1px solid {BORDER}; selection-background-color: {RED}; }}
QPushButton {{
    background: {CARD}; border: 1px solid {BORDER}; border-radius: 8px; padding: 7px 14px;
}}
QPushButton:hover {{ background: {CARD_HOVER}; border-color: {RED}; }}
QPushButton:disabled {{ color: #55555f; border-color: #22222a; }}
QPushButton#primary {{ background: {RED}; border: 1px solid {RED}; color: white; font-weight: 600; }}
QPushButton#primary:hover {{ background: #f03a49; }}
QPushButton#primary:disabled {{ background: #3a1217; border-color: #3a1217; color: #80555a; }}
QPushButton#danger:hover {{ background: {RED_DARK}; }}
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget#sidebar::item {{ padding: 9px 12px; border-radius: 8px; margin: 1px 0; }}
QListWidget#sidebar::item:selected {{ background: {RED}; color: white; }}
QListWidget#sidebar::item:hover:!selected {{ background: {CARD}; }}
QProgressBar {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 6px; height: 12px; text-align: center; color: {TEXT}; font-size: 8pt; }}
QProgressBar::chunk {{ background: {RED}; border-radius: 5px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {RED}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QStatusBar {{ background: {PANEL}; color: {MUTED}; }}
QMenu {{ background: {CARD}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {RED}; }}
QToolTip {{ background: {CARD}; color: {TEXT}; border: 1px solid {RED}; }}
QCheckBox {{ background: transparent; spacing: 8px; }}
"""


def app_icon() -> QIcon:
    """Lua crescente vermelha sobre fundo escuro (a 'noite' do UsagiNoYami)."""
    icon = QIcon()
    for size in (16, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(BG)))
        p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)
        moon = QPainterPath()
        moon.addEllipse(QPointF(size * 0.5, size * 0.5), size * 0.31, size * 0.31)
        cut = QPainterPath()
        cut.addEllipse(QPointF(size * 0.62, size * 0.43), size * 0.27, size * 0.27)
        p.setBrush(QBrush(QColor(RED)))
        p.drawPath(moon.subtracted(cut))
        p.end()
        icon.addPixmap(pm)
    return icon
