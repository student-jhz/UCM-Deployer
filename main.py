# -*- coding: utf-8 -*-
"""UCM Deployer GUI 入口。

用法：
    UCM-Deployer.exe / python main.py     # 启动图形界面
    --smoke                               # 无头冒烟自检（实例化并逐页切换后退出）
"""
import sys


def _run_smoke() -> int:
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox

    # offscreen 下模态对话框会永久阻塞，自动应答
    for name in ("information", "warning", "critical", "about"):
        setattr(QMessageBox, name,
                staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    QMessageBox.question = staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.Yes)

    from ucm_deployer.gui.main_window import MainWindow
    from ucm_deployer.gui.theme import apply_light_theme

    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    apply_light_theme(app)
    win = MainWindow()
    win.show()
    app.processEvents()
    for i in range(len(win._nav_buttons)):
        win.goto_step(i)
        app.processEvents()
    win.close()
    print("GUI smoke test: OK")
    return 0


def main() -> int:
    if "--smoke" in sys.argv:
        return _run_smoke()

    from ucm_deployer.gui.app import run
    return run()


if __name__ == "__main__":
    sys.exit(main())
