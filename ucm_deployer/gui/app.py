# -*- coding: utf-8 -*-
"""GUI 应用入口（QApplication 初始化 + 主题 + 主窗口循环）。"""
from __future__ import annotations

import sys


def run() -> int:
    from PySide6.QtWidgets import QApplication

    from ..utils.log import setup_logging
    from .main_window import MainWindow
    from .theme import apply_light_theme, make_app_icon

    setup_logging()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("UCM-Deployer")
    app.setWindowIcon(make_app_icon())
    apply_light_theme(app)

    win = MainWindow()
    win.show()
    return app.exec()
