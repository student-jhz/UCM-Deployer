# -*- coding: utf-8 -*-
"""UCM Deployer GUI 入口。

用法：
    python main.py            # 启动图形界面
    python main.py --smoke    # 无头冒烟自检（实例化全部页面后退出）
"""
import sys


def main() -> int:
    if "--smoke" in sys.argv:
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
        app = QApplication.instance() or QApplication([])
        win = MainWindow()
        win.show()
        app.processEvents()
        # 逐页切换自检
        for i in range(5):
            win.nav.setCurrentRow(i)
            app.processEvents()
        win.close()
        print("GUI smoke test: OK")
        return 0

    from ucm_deployer.gui.app import run
    return run()


if __name__ == "__main__":
    sys.exit(main())
