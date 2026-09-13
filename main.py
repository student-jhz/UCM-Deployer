# -*- coding: utf-8 -*-
"""UCM Deployer GUI 入口。

用法：
    UCM-Deployer.exe / python main.py     # 启动图形界面
    --smoke                               # 无头冒烟自检（实例化全部页面后退出）
    --selftest [--out 文件路径]            # 内置端到端自检（模拟服务器全流程），退出码 0/1
"""
import sys


def _run_selftest(argv) -> int:
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from ucm_deployer.mock.selftest import run_selftest

    out_path = None
    if "--out" in argv:
        try:
            out_path = argv[argv.index("--out") + 1]
        except IndexError:
            pass
    ok, report = run_selftest(progress=lambda m: None)
    text = "\n".join(report)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    else:
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            app = QApplication.instance() or QApplication([])
            app.setStyle("Fusion")
            from ucm_deployer.gui.theme import apply_light_theme
            apply_light_theme(app)
            QMessageBox.information(
                None, "UCM Deployer 自检",
                text + ("\n（使用 --out 文件路径 可导出报告）"))
        except Exception:
            print(text)
    return 0 if ok else 1


def main() -> int:
    if "--selftest" in sys.argv:
        return _run_selftest(sys.argv)

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
        from ucm_deployer.gui.theme import apply_light_theme

        app = QApplication.instance() or QApplication([])
        app.setStyle("Fusion")
        apply_light_theme(app)
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
