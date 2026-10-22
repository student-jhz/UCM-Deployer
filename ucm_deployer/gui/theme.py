# -*- coding: utf-8 -*-
"""全局浅色主题（QSS）与程序图标。

注意 QSS 规则顺序陷阱：`QWidget{background:transparent}` 必须声明在所有
窗口级背景规则（QMainWindow/QDialog）之前——同等优先级时后者覆盖前者，
曾导致主窗口背景被覆盖为 transparent 而渲染大片黑色。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication

# 勾选框对勾图片占位（apply_light_theme 时替换为实际生成路径）
_CHECKMARK_PLACEHOLDER = "__UCM_CHECKMARK__"

QSS = """
/* —— 通配规则必须在窗口级背景规则之前（顺序陷阱，见模块 docstring）—— */
QWidget { background: transparent; }
QMainWindow, QDialog { background: #f3f5f8; }
#brandHeader { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; }
#brandTitle { font-size: 17px; font-weight: 600; color: #1e293b; background: transparent; }
#brandSub { color: #64748b; font-size: 12px; background: transparent; }

/* 复选框：加大加亮的勾选指示器（默认 Fusion 样式小且不显眼） */
QCheckBox { spacing: 8px; color: #1e293b; font-weight: 600; }
QCheckBox::indicator {
    width: 18px; height: 18px;
    border: 2px solid #94a3b8; border-radius: 4px; background: #ffffff;
}
QCheckBox::indicator:hover { border-color: #2f6fed; }
/* 选中：蓝色实心 + 对勾图片（图片缺失时自动回退为纯色块，仍醒目） */
QCheckBox::indicator:checked {
    background: #2f6fed; border-color: #2f6fed;
    image: url(%s);
}
QCheckBox::indicator:disabled { border-color: #e2e8f0; background: #f1f5f9; }
""" % _CHECKMARK_PLACEHOLDER + """
QPushButton#navBtn {
    text-align: left; padding: 6px 12px; border: 1px solid transparent;
    border-radius: 6px; background: transparent; color: #334155; font-size: 13px;
}
QPushButton#navBtn:hover { background: #e8eefb; }
QPushButton#navBtn:checked {
    background: #2f6fed; color: #ffffff; font-weight: 600;
    border: 1px solid #2f6fed;
}

QGroupBox {
    font-weight: 600; color: #1e293b; border: 1px solid #dde3ec;
    border-radius: 8px; margin-top: 8px; padding-top: 4px; background: #ffffff;
}
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }

QLineEdit, QComboBox, QPlainTextEdit, QSpinBox {
    background: #ffffff; border: 1px solid #cbd5e1; border-radius: 6px;
    padding: 5px 8px; color: #1e293b; selection-background-color: #2f6fed;
}
QLineEdit:disabled, QComboBox:disabled { background: #f1f5f9; color: #94a3b8; }
QLineEdit:focus, QComboBox:focus { border: 1px solid #2f6fed; }

/* 镜像下拉弹层与筛选补全列表：显式浅色（默认继承会黑底黑字不可读） */
QComboBox QAbstractItemView {
    background: #ffffff; color: #1e293b; border: 1px solid #cbd5e1;
    selection-background-color: #2f6fed; selection-color: #ffffff;
}
QListView, QListWidget {
    background: #ffffff; color: #1e293b; border: 1px solid #dde3ec;
    border-radius: 6px;
}
QListView::item, QListWidget::item { padding: 4px 8px; }
QListView::item:selected, QListWidget::item:selected {
    background: #2f6fed; color: #ffffff;
}
QListView#imageFilterPopup {
    background: #ffffff; color: #1e293b;
}

QPushButton {
    background: #ffffff; color: #1e293b; border: 1px solid #cbd5e1;
    border-radius: 6px; padding: 6px 14px; font-size: 13px;
}
QPushButton:hover { border-color: #2f6fed; color: #2f6fed; }
QPushButton:pressed { background: #e8eefb; }
QPushButton:disabled { color: #94a3b8; border-color: #e2e8f0; }
QPushButton[accent="true"] {
    background: #2f6fed; color: #ffffff; border: 1px solid #2f6fed;
    font-weight: 600;
}
QPushButton[accent="true"]:hover { background: #2560d6; }
QPushButton[accent="true"]:disabled { background: #9db8f4; border-color: #9db8f4; }

QLabel[role="hint"] { color: #64748b; font-size: 12px; background: transparent; }

QTableWidget {
    background: #ffffff; border: 1px solid #dde3ec; border-radius: 6px;
    gridline-color: #eef2f7; alternate-background-color: #f8fafc;
}
QHeaderView::section {
    background: #f1f5f9; color: #334155; font-weight: 600;
    border: none; border-bottom: 1px solid #dde3ec; padding: 6px 8px;
}

QProgressBar {
    background: #eef2f7; border: none; border-radius: 5px;
    text-align: center; color: #334155; height: 16px;
}
QProgressBar::chunk { background: #2f6fed; border-radius: 5px; }

QTabWidget::pane { border: 1px solid #dde3ec; border-radius: 6px; background: #ffffff; }
QTabBar::tab {
    padding: 6px 14px; background: #f1f5f9; color: #475569;
    border: 1px solid #dde3ec; border-bottom: none;
    border-top-left-radius: 6px; border-top-right-radius: 6px;
}
QTabBar::tab:selected { background: #ffffff; color: #2f6fed; font-weight: 600; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #cbd5e1; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #94a3b8; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: #cbd5e1; border-radius: 5px; min-width: 30px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

QMenu { background: #ffffff; border: 1px solid #dde3ec; border-radius: 6px; }
QMenu::item { padding: 6px 24px; color: #1e293b; }
QMenu::item:selected { background: #e8eefb; color: #2f6fed; }

QToolTip { background: #1e293b; color: #ffffff; border: none; padding: 4px 8px; }
QStatusBar { background: #ffffff; color: #475569; border-top: 1px solid #e2e8f0; }
"""


def make_app_icon() -> QPixmap:
    """程序图标：蓝渐变 UCM 方块（代码生成，无资源文件）。"""
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    grad = QLinearGradient(0, 0, 64, 64)
    grad.setColorAt(0, QColor("#4f8dfd"))
    grad.setColorAt(1, QColor("#2f6fed"))
    p.setBrush(grad)
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(4, 4, 56, 56, 12, 12)
    p.setPen(QColor("#ffffff"))
    font = p.font()
    font.setBold(True)
    font.setPixelSize(22)
    p.setFont(font)
    p.drawText(pm.rect(), Qt.AlignCenter, "UCM")
    p.end()
    return pm


def ensure_checkmark_png() -> str:
    """生成复选框对勾图片（白色，QSS 引用），返回文件路径；失败返回空串。

    QSS 不支持内嵌图片，对勾以代码生成写入 ~/.ucm_deployer/theme/；
    文件缺失或生成失败时 QSS 的 image 加载失败会自动回退为纯色块。
    """
    import sys

    try:
        base = Path.home() / ".ucm_deployer" / "theme"
        base.mkdir(parents=True, exist_ok=True)
        path = base / "checkmark-white.png"
        if not path.is_file() or path.stat().st_size == 0:
            pm = QPixmap(18, 18)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            pen = QPen(QColor("#ffffff"), 3)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            p.drawLine(4, 9, 8, 13)
            p.drawLine(8, 13, 14, 5)
            p.end()
            if not pm.save(str(path), "PNG"):
                return ""
        return str(path).replace("\\", "/")
    except Exception:
        return ""


def themed_qss() -> str:
    """带对勾图片路径的 QSS（apply_light_theme 使用）。"""
    url = ensure_checkmark_png()
    if url:
        return QSS.replace(_CHECKMARK_PLACEHOLDER, url)
    # 无图片时移除 image 行，保留纯色块样式
    return QSS.replace("    image: url(%s);\n" % _CHECKMARK_PLACEHOLDER, "")


def apply_light_theme(app: QApplication) -> None:
    """浅色 QPalette + QSS（防系统深色模式下 Qt 自动切换深色调色板）。"""
    from PySide6.QtGui import QPalette

    palette = QPalette()
    palette.setColor(QPalette.Window, QColor("#f3f5f8"))
    palette.setColor(QPalette.WindowText, QColor("#1e293b"))
    palette.setColor(QPalette.Base, QColor("#ffffff"))
    palette.setColor(QPalette.AlternateBase, QColor("#f8fafc"))
    palette.setColor(QPalette.Text, QColor("#1e293b"))
    palette.setColor(QPalette.Button, QColor("#ffffff"))
    palette.setColor(QPalette.ButtonText, QColor("#1e293b"))
    palette.setColor(QPalette.Highlight, QColor("#2f6fed"))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ToolTipBase, QColor("#1e293b"))
    palette.setColor(QPalette.ToolTipText, QColor("#ffffff"))
    app.setPalette(palette)
    app.setStyleSheet(themed_qss())
