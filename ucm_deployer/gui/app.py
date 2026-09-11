# -*- coding: utf-8 -*-
"""GUI 应用入口。"""
from __future__ import annotations

import sys


def run(argv=None) -> int:
    from PySide6.QtWidgets import QApplication

    from ..utils.log import setup_logging
    from .main_window import MainWindow

    setup_logging()
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("UCM Deployer")
    app.setOrganizationName("UCM-Deployer")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(run())
