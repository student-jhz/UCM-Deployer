# -*- coding: utf-8 -*-
"""主窗口：左侧品牌区 + 三步导航按钮 + 右侧步骤页面 + 状态栏/菜单。"""
from __future__ import annotations

import os

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..version import APP_NAME, __version__
from .state import AppContext
from .theme import make_app_icon
from .widgets.container_page import ContainerPage
from .widgets.image_page import ImagePage
from .widgets.precheck_page import PrecheckPage
from .widgets.server_page import ServerPage

_STEPS = [
    ("步骤1：服务器管理", "登录服务器并探测设备型号、卡数与 CPU 架构"),
    ("步骤2：镜像准备", "复用已含 UCM 的镜像，或按需构建新 UCM 镜像"),
    ("步骤3：容器准备", "按需创建 UCM 容器；已有容器可直接进入步骤4"),
    ("步骤4：环境预检", "显式选择容器并运行 UCM 环境预检"),
]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{__version__}")
        self.resize(1280, 860)
        self.ctx = AppContext()
        self._active_row = 0      # 当前已进入的步骤（导航回退基准）

        # ---------- 左侧：品牌区 + 步骤按钮
        brand = QFrame()
        brand.setObjectName("brandHeader")
        icon_lbl = QLabel()
        icon_lbl.setPixmap(make_app_icon().scaled(
            40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        title = QLabel(APP_NAME)
        title.setObjectName("brandTitle")
        sub = QLabel(f"v{__version__} · UCM 安装与环境预检")
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
        self.precheck_page = PrecheckPage(self.ctx)
        for p in (self.server_page, self.image_page,
                  self.container_page, self.precheck_page):
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

        # 状态栏 1s 刷新（服务器/镜像/容器/后台任务汇总）
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._update_status)
        self._status_timer.start(1000)
        self._update_status()

    # ------------------------------------------------------------ 菜单
    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("文件(&F)")
        act_quit = QAction("退出", self)
        act_quit.setShortcut("Ctrl+Q")
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        tool_menu = self.menuBar().addMenu("工具(&T)")
        act_clean_ctr = QAction("清理容器...", self)
        act_clean_ctr.triggered.connect(lambda: self._open_cleanup("container"))
        act_clean_img = QAction("清理镜像...", self)
        act_clean_img.triggered.connect(lambda: self._open_cleanup("image"))
        tool_menu.addAction(act_clean_ctr)
        tool_menu.addAction(act_clean_img)

        help_menu = self.menuBar().addMenu("帮助(&H)")
        act_logs = QAction("打开日志目录", self)
        act_logs.triggered.connect(lambda: self._open_dir(self._log_dir()))
        act_conf = QAction("打开配置目录", self)
        act_conf.triggered.connect(lambda: self._open_dir(self._conf_dir()))
        act_about = QAction("关于", self)
        act_about.triggered.connect(self._about)
        for a in (act_logs, act_conf):
            help_menu.addAction(a)
        help_menu.addSeparator()
        help_menu.addAction(act_about)

    def _open_cleanup(self, mode: str) -> None:
        """打开清理对话框（作用于当前勾选登录的服务器）。"""
        from .widgets.cleanup_dialog import CleanupDialog

        if self.ctx.server is None:
            QMessageBox.information(
                self, "提示", "请先在「步骤1：服务器管理」勾选要登录的服务器")
            return
        CleanupDialog(self.ctx, mode, self).exec()

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

    def _about(self) -> None:
        QMessageBox.about(
            self, "关于",
            f"<b>{APP_NAME}</b> v{__version__}<br><br>"
            "UCM (Unified Cache Management) 安装与环境预检工具<br>"
            "适用引擎: vLLM / vLLM-Ascend / SGLang<br>"
            "适用硬件: Ascend NPU / NVIDIA GPU<br><br>"
            "配置目录: ~/.ucm_deployer")

    # ------------------------------------------------------------ 状态栏
    def _update_status(self) -> None:
        server = self.ctx.server
        imgs = "已确定" if (server and self.ctx.images.get(server.id)) else "未确定"
        ctrs = "已创建" if (server and self.ctx.containers.get(server.id)) else "未创建"
        msg = (f"服务器 {server.name if server else '未登录'}"
               f"    |    UCM 镜像 {imgs}    |    容器 {ctrs}")
        busy = sum(1 for p in self._all_panels() if p.is_running())
        if busy:
            msg = f"⚙ 后台任务运行中 ×{busy}    |    " + msg
        self.statusBar().showMessage(msg)

    def _all_panels(self):
        return [p.panel for p in (self.server_page, self.image_page,
                                  self.container_page, self.precheck_page)]

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
        """步骤切换：允许自由跳转到任意步骤；前置条件由各步骤内的操作拦截。"""
        if row == self._active_row:
            self.pages.setCurrentIndex(row)
            self._update_status()
            return

        if row == 1:
            self.image_page.on_enter()
        elif row == 2:
            self.container_page.on_enter()
        elif row == 3:
            self.precheck_page.on_enter()

        self._active_row = row
        self.pages.setCurrentIndex(row)
        self._update_status()

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
        if busy:
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
