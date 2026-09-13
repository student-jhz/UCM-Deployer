# -*- coding: utf-8 -*-
"""主窗口：左侧品牌区+步骤按钮（直接平铺） + 右侧步骤页面 + 状态栏/菜单。"""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..version import APP_NAME, __version__
from .state import AppContext
from .theme import make_app_icon
from .widgets.container_page import ContainerPage
from .widgets.deploy_page import DeployPage
from .widgets.image_page import ImagePage
from .widgets.launch_page import LaunchPage
from .widgets.server_page import ServerPage

_STEPS = [
    ("步骤1：服务器管理", "选择/管理要部署的服务器，校验设备型号"),
    ("步骤2：镜像构建", "基础镜像 + UCM whl -> 带 UCM 的引擎镜像"),
    ("步骤3：容器创建", "docker run（kvcache 挂载/模型映射/设备映射）"),
    ("步骤4：部署配置", "PD 拓扑 / vllm/sglang 参数 / 生成启动脚本"),
    ("步骤5：拉起服务", "部署脚本、按序拉起、健康检查、日志"),
]

# 用户手册（Markdown）渲染后的富文本样式（QTextBrowser 支持的 CSS 子集）
_MANUAL_CSS = """
body { font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif; }
h1 { color: #1e293b; }
h2 { color: #2f6fed; }
h3 { color: #1e293b; }
code { background-color: #f1f5f9; font-family: "Consolas", monospace; }
pre { background-color: #f8fafc; }
table { border-collapse: collapse; }
th { background-color: #f1f5f9; }
th, td { border: 1px solid #d7dee8; padding: 3px 8px; }
blockquote { color: #64748b; }
"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{__version__}")
        self.resize(1340, 880)
        self.ctx = AppContext()
        self._active_row = 0      # 当前已进入的步骤（导航回退基准）

        # ---------- 左侧：品牌区 + 步骤按钮（平铺直显，非滚动列表）
        brand = QFrame()
        brand.setObjectName("brandHeader")
        icon_lbl = QLabel()
        icon_lbl.setPixmap(make_app_icon().scaled(
            40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        title = QLabel(APP_NAME)
        title.setObjectName("brandTitle")
        sub = QLabel(f"v{__version__} · UCM 一键部署")
        sub.setObjectName("brandSub")
        btext = QVBoxLayout()
        btext.setContentsMargins(0, 0, 0, 0)
        btext.setSpacing(0)
        btext.addWidget(title)
        btext.addWidget(sub)
        brow = QHBoxLayout(brand)
        brow.setContentsMargins(14, 10, 14, 10)
        brow.addWidget(icon_lbl)
        brow.addSpacing(10)
        brow.addLayout(btext)
        brow.addStretch(1)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self._nav_buttons = []
        nav_layout = QVBoxLayout()
        nav_layout.setSpacing(6)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        for i, (title_, tip) in enumerate(_STEPS):
            btn = QPushButton(title_)
            btn.setObjectName("navBtn")
            btn.setCheckable(True)
            btn.setToolTip(tip)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(40)
            self.nav_group.addButton(btn, i)
            nav_layout.addWidget(btn)
            self._nav_buttons.append(btn)
        self._nav_buttons[0].setChecked(True)
        # idClicked 仅在用户点击时发射（程序化 setChecked 不触发），
        # 因此导航回退/程序切换不会造成信号重入。
        self.nav_group.idClicked.connect(self._switch)

        prev_btn = QPushButton("← 上一步")
        next_btn = QPushButton("下一步 →")
        next_btn.setProperty("accent", True)
        prev_btn.clicked.connect(self._prev)
        next_btn.clicked.connect(self._next)
        nav_btns = QHBoxLayout()
        nav_btns.addWidget(prev_btn)
        nav_btns.addStretch(1)
        nav_btns.addWidget(next_btn)

        left = QVBoxLayout()
        left.setSpacing(10)
        left.addWidget(brand)
        left.addLayout(nav_layout)
        left.addStretch(1)
        left.addLayout(nav_btns)
        left_widget = QWidget()
        left_widget.setFixedWidth(240)
        left_widget.setLayout(left)

        # ---------- 页面
        self.pages = QStackedWidget()
        self.server_page = ServerPage(self.ctx)
        self.image_page = ImagePage(self.ctx)
        self.container_page = ContainerPage(self.ctx)
        self.deploy_page = DeployPage(self.ctx)
        self.launch_page = LaunchPage(self.ctx)
        for p in (self.server_page, self.image_page, self.container_page,
                  self.deploy_page, self.launch_page):
            self.pages.addWidget(p)

        center = QHBoxLayout()
        center.setContentsMargins(12, 12, 12, 12)
        center.setSpacing(12)
        center.addWidget(left_widget)
        center.addWidget(self.pages, 1)
        container = QWidget()
        container.setLayout(center)
        self.setCentralWidget(container)

        self._build_menus()
        self._restore_geometry()

        # 状态栏 1s 刷新（服务器/镜像/容器/脚本/后台任务汇总）
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._update_status)
        self._status_timer.start(1000)
        self._update_status()

    # ------------------------------------------------------------ 菜单
    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("文件(&F)")
        act_quit = QAction("退出", self)
        act_quit.setShortcut(QKeySequence.Quit)
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        help_menu = self.menuBar().addMenu("帮助(&H)")
        act_manual = QAction("用户手册", self)
        act_manual.triggered.connect(self._open_manual)
        act_logs = QAction("打开日志目录", self)
        act_logs.triggered.connect(lambda: self._open_dir(self._log_dir()))
        act_conf = QAction("打开配置目录", self)
        act_conf.triggered.connect(lambda: self._open_dir(self._conf_dir()))
        act_selftest = QAction("自检（模拟服务器全流程）", self)
        act_selftest.triggered.connect(self._run_selftest)
        act_about = QAction("关于", self)
        act_about.triggered.connect(self._about)
        for a in (act_manual, act_logs, act_conf, act_selftest):
            help_menu.addAction(a)
        help_menu.addSeparator()
        help_menu.addAction(act_about)

    @staticmethod
    def _conf_dir() -> str:
        from ..utils.paths import default_config_dir

        return str(default_config_dir())

    @staticmethod
    def _log_dir() -> str:
        return os.path.join(MainWindow._conf_dir(), "logs")

    @staticmethod
    def _open_dir(path: str) -> None:
        try:
            os.makedirs(path, exist_ok=True)
            os.startfile(path)  # Windows
        except Exception as exc:
            QMessageBox.warning(None, "打开失败", f"无法打开 {path}:\n{exc}")

    @staticmethod
    def find_manual_path() -> str:
        """定位用户手册（打包环境 _MEIPASS / 源码仓库），找不到返回空串。"""
        candidates = []
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(os.path.join(meipass, "docs", "用户手册.md"))
        candidates.append(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "docs", "用户手册.md"))
        for path in candidates:
            if os.path.isfile(path):
                return path
        return ""

    @staticmethod
    def manual_html(md_text: str) -> str:
        """Markdown -> HTML（表格/围栏代码块）。markdown 库缺失时返回空串（调用方回退纯文本）。"""
        try:
            import markdown
        except ImportError:
            return ""
        return markdown.markdown(md_text,
                                 extensions=["tables", "fenced_code"])

    def _open_manual(self) -> None:
        """在程序内弹窗显示用户手册（Markdown 渲染为富文本，不调用外部程序）。"""
        path = self.find_manual_path()
        if not path:
            QMessageBox.information(
                self, "用户手册",
                "未找到用户手册文件。\n可查看仓库 docs/用户手册.md。")
            return
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            QMessageBox.warning(self, "用户手册", f"读取失败: {exc}")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"用户手册 · {APP_NAME} v{__version__}")
        dlg.resize(940, 680)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        html = self.manual_html(text)
        if html:
            # 注意: DefaultStyleSheet 必须在 setHtml 之前设置才生效
            browser.document().setDefaultStyleSheet(_MANUAL_CSS)
            browser.setHtml(html)
        else:
            browser.setPlainText(text)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dlg.reject)
        buttons.clicked.connect(lambda _: dlg.reject())
        layout = QVBoxLayout(dlg)
        layout.addWidget(browser, 1)
        layout.addWidget(buttons)
        dlg.exec()

    def _run_selftest(self) -> None:
        from ..mock.selftest import run_selftest

        ok, report = run_selftest(progress=lambda m: None)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information if ok else QMessageBox.Warning)
        box.setWindowTitle("自检结果")
        box.setText("\n".join(report))
        box.exec()

    def _about(self) -> None:
        QMessageBox.about(
            self, "关于",
            f"<b>{APP_NAME}</b> v{__version__}<br><br>"
            "UCM (Unified Cache Management) 一键部署工具<br>"
            "适用引擎: vLLM / vLLM-Ascend / SGLang<br>"
            "适用硬件: Ascend NPU / NVIDIA GPU<br><br>"
            "配置目录: ~/.ucm_deployer")

    # ------------------------------------------------------------ 状态栏
    def _update_status(self) -> None:
        sel = len(self.ctx.selected)
        imgs = sum(1 for s in self.ctx.selected if s.id in self.ctx.images)
        ctrs = sum(1 for s in self.ctx.selected if s.id in self.ctx.containers)
        scripts = "已生成" if self.ctx.scripts else "未生成"
        msg = (f"已选服务器 {sel}    |    UCM 镜像 {imgs}/{sel}"
               f"    |    容器 {ctrs}/{sel}    |    部署脚本 {scripts}")
        busy = sum(1 for p in self._all_panels() if p.is_running())
        if busy:
            msg = f"⚙ 后台任务运行中 ×{busy}    |    " + msg
        self.statusBar().showMessage(msg)

    def _all_panels(self):
        return [p.panel for p in (self.server_page, self.image_page,
                                  self.container_page, self.deploy_page,
                                  self.launch_page)]

    # ------------------------------------------------------------ 导航
    def goto_step(self, row: int) -> None:
        """程序化切换步骤（等价于用户点击左侧步骤按钮）。"""
        if not (0 <= row < len(self._nav_buttons)):
            return
        self._nav_buttons[row].setChecked(True)   # 程序化 setChecked 不触发 idClicked
        self._switch(row)

    def current_step(self) -> int:
        return self._active_row

    def _switch(self, row: int) -> None:
        """步骤切换守卫：前置条件不满足时弹提示并把导航恢复到当前步骤。"""
        if row == self._active_row:
            self.pages.setCurrentIndex(row)
            self._update_status()
            return

        if row == 0:
            pass
        elif row in (1, 2, 3, 4):
            if not self._require_servers():
                self._restore_nav()
                return
            if row >= 2 and not self._require_images():
                self._restore_nav()
                return
            if row >= 3 and not self._require_containers():
                self._restore_nav()
                return
            if row == 4:
                if self.ctx.scripts is None:
                    QMessageBox.information(
                        self, "提示", "请先在「步骤4：部署配置」生成部署脚本后再进入本页")
                    self._restore_nav()
                    return
                # 关键：把步骤4中用户的最新脚本编辑同步进 ctx.scripts
                self.deploy_page.sync_edits()
                self.launch_page.on_enter()
            elif row == 1:
                self.image_page.on_enter()
            elif row == 2:
                self.container_page.on_enter()
            else:
                self.deploy_page.on_enter()

        self._active_row = row
        self.pages.setCurrentIndex(row)
        self._update_status()

    def _restore_nav(self) -> None:
        """把导航选中恢复到当前已进入的步骤（程序化 setChecked 不触发 _switch）。"""
        if 0 <= self._active_row < len(self._nav_buttons):
            self._nav_buttons[self._active_row].setChecked(True)

    def _require_servers(self) -> bool:
        if not self.ctx.selected:
            QMessageBox.warning(self, "未完成：步骤1",
                                "请先在「步骤1：服务器管理」勾选要部署的服务器")
            return False
        return True

    def _require_images(self) -> bool:
        missing = [s.name for s in self.ctx.selected if s.id not in self.ctx.images]
        if missing:
            QMessageBox.warning(
                self, "未完成：步骤2",
                "以下服务器还未确定 UCM 镜像：\n  " + "、".join(missing)
                + "\n\n请先在「步骤2：镜像构建」完成构建，或检查已有镜像后跳过构建。")
            return False
        return True

    def _require_containers(self) -> bool:
        missing = [s.name for s in self.ctx.selected if s.id not in self.ctx.containers]
        if missing:
            QMessageBox.warning(
                self, "未完成：步骤3",
                "以下服务器还未确定容器：\n  " + "、".join(missing)
                + "\n\n请先在「步骤3：容器创建」创建容器或检查容器内 UCM。")
            return False
        return True

    def _prev(self) -> None:
        if self._active_row > 0:
            self.goto_step(self._active_row - 1)

    def _next(self) -> None:
        row = self._active_row
        if row == 0:
            self.server_page._collect_selection()
        if row < len(_STEPS) - 1:
            self.goto_step(row + 1)

    # ------------------------------------------------------------ 关闭
    def _restore_geometry(self) -> None:
        settings = QSettings("UCM-Deployer", "MainWindow")
        geo = settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)

    def closeEvent(self, event) -> None:
        busy = [p for p in self._all_panels() if p.is_running()]
        if busy or self.launch_page._launching:
            ret = QMessageBox.question(
                self, "任务运行中",
                "仍有后台任务在执行，退出可能中断操作。\n确定要退出吗？")
            if ret != QMessageBox.Yes:
                event.ignore()
                return
            for p in busy:
                p.shutdown()
        settings = QSettings("UCM-Deployer", "MainWindow")
        settings.setValue("geometry", self.saveGeometry())
        event.accept()
