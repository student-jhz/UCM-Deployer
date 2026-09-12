# -*- coding: utf-8 -*-
"""界面主题：全局 QSS 样式与程序图标（纯代码生成，无资源文件）。"""
from __future__ import annotations

_PRIMARY = "#2f6fed"
_PRIMARY_DARK = "#245cd0"
_BORDER = "#d7dee8"
_BG = "#f3f5f8"
_CARD = "#ffffff"
_TEXT = "#2d3748"
_TEXT_SUB = "#64748b"

QSS = f"""
* {{
    font-family: "Microsoft YaHei UI", "Segoe UI", "PingFang SC", sans-serif;
    font-size: 9pt;
    color: {_TEXT};
}}
QMainWindow, QDialog {{ background: {_BG}; }}
QWidget {{ background: transparent; }}
QLabel {{ background: transparent; color: {_TEXT}; }}
QLabel[role="hint"] {{ color: {_TEXT_SUB}; }}

/* ---------- 侧边导航 ---------- */
QListWidget#nav {{
    background: {_CARD};
    border: 1px solid {_BORDER};
    border-radius: 10px;
    padding: 6px;
    outline: none;
}}
QListWidget#nav::item {{
    height: 42px;
    margin: 3px 4px;
    padding: 8px 12px;
    border-radius: 8px;
    color: #475569;
}}
QListWidget#nav::item:hover {{ background: #eef2f8; }}
QListWidget#nav::item:selected {{
    background: {_PRIMARY};
    color: white;
    font-weight: 600;
}}

/* ---------- 分组卡片 ---------- */
QGroupBox {{
    background: {_CARD};
    border: 1px solid {_BORDER};
    border-radius: 10px;
    margin-top: 16px;
    padding: 10px 10px 8px 10px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 6px;
    color: {_TEXT};
    font-weight: 600;
}}

/* ---------- 按钮 ---------- */
QPushButton {{
    background: {_CARD};
    border: 1px solid {_BORDER};
    border-radius: 7px;
    padding: 6px 14px;
}}
QPushButton:hover {{ background: #eef2f8; border-color: #b7c3d4; }}
QPushButton:pressed {{ background: #e4e9f2; }}
QPushButton:disabled {{ color: #a8b3c4; border-color: #e8edf3; background: #f8fafc; }}
QPushButton[accent="true"] {{
    background: {_PRIMARY};
    border: 1px solid {_PRIMARY};
    color: white;
    font-weight: 600;
    padding: 7px 18px;
}}
QPushButton[accent="true"]:hover {{ background: {_PRIMARY_DARK}; }}
QPushButton[accent="true"]:disabled {{ background: #9db8ea; border-color: #9db8ea; }}

/* ---------- 输入控件 ---------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {_CARD};
    border: 1px solid {_BORDER};
    border-radius: 7px;
    padding: 4px 8px;
    min-height: 20px;
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {_CARD};
    border: 1px solid {_BORDER};
    selection-background-color: {_PRIMARY};
    selection-color: white;
}}
QTextEdit, QPlainTextEdit {{
    background: {_CARD};
    border: 1px solid {_BORDER};
    border-radius: 7px;
    selection-background-color: {_PRIMARY};
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus,
QTextEdit:focus, QPlainTextEdit:focus {{ border: 1px solid {_PRIMARY}; }}
QPlainTextEdit[role="code"], QTextEdit[role="code"] {{
    font-family: "Consolas", "Cascadia Mono", monospace;
    background: #fbfcfe;
}}

/* ---------- 表格 ---------- */
QTableWidget, QTableView {{
    background: {_CARD};
    alternate-background-color: #f8fafc;
    gridline-color: #eef2f7;
    border: 1px solid {_BORDER};
    border-radius: 8px;
    selection-background-color: #e7eefc;
    selection-color: {_TEXT};
}}
QHeaderView::section {{
    background: #f1f5f9;
    border: none;
    border-bottom: 1px solid {_BORDER};
    border-right: 1px solid #eef2f7;
    padding: 6px 8px;
    font-weight: 600;
    color: #475569;
}}
QTableCornerButton::section {{ background: #f1f5f9; border: none; }}

/* ---------- Tab / 进度条 / 滚动条 ---------- */
QTabWidget::pane {{ border: 1px solid {_BORDER}; border-radius: 8px; background: {_CARD}; }}
QTabBar::tab {{ padding: 6px 14px; color: #475569; }}
QTabBar::tab:selected {{ color: {_PRIMARY}; font-weight: 600; border-bottom: 2px solid {_PRIMARY}; }}
QTabBar::tab:hover {{ color: {_PRIMARY}; }}
QProgressBar {{
    border: 1px solid {_BORDER};
    border-radius: 6px;
    background: #f1f5f9;
    text-align: center;
    color: #475569;
    min-height: 16px;
}}
QProgressBar::chunk {{ background: {_PRIMARY}; border-radius: 5px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #cbd5e1; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #94a3b8; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #cbd5e1; border-radius: 5px; min-width: 30px; }}

/* ---------- 菜单 / 状态栏 / 提示 ---------- */
QMenuBar {{ background: {_CARD}; border-bottom: 1px solid {_BORDER}; }}
QMenuBar::item {{ padding: 5px 10px; border-radius: 6px; }}
QMenuBar::item:selected {{ background: #eef2f8; }}
QMenu {{ background: {_CARD}; border: 1px solid {_BORDER}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 24px 6px 14px; border-radius: 6px; }}
QMenu::item:selected {{ background: {_PRIMARY}; color: white; }}
QStatusBar {{ background: {_CARD}; border-top: 1px solid {_BORDER}; color: #475569; }}
QToolTip {{ background: #1e293b; color: white; border: none; padding: 5px 8px; border-radius: 4px; }}
QSplitter::handle {{ background: {_BORDER}; }}
QCheckBox, QRadioButton {{ spacing: 6px; background: transparent; }}
QListWidget {{ background: {_CARD}; border: 1px solid {_BORDER}; border-radius: 7px; }}
"""


def make_app_icon():
    """程序图标：蓝渐变圆角方块 + UCM 字样（代码绘制，免资源文件）。"""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPen, QPixmap

    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    grad = QLinearGradient(0, 0, 64, 64)
    grad.setColorAt(0, QColor("#2f6fed"))
    grad.setColorAt(1, QColor("#00b3d6"))
    p.setBrush(QBrush(grad))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(2, 2, 60, 60), 15, 15)
    p.setPen(QPen(QColor("white")))
    font = QFont("Segoe UI")
    font.setBold(True)
    font.setPixelSize(23)
    p.setFont(font)
    p.drawText(QRectF(0, 0, 64, 64), Qt.AlignCenter, "UCM")
    p.end()
    return pm
