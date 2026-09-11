# -*- coding: utf-8 -*-
"""主窗口：左侧五步导航 + 右侧步骤页面。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..version import APP_NAME, __version__
from .state import AppContext
from .widgets.container_page import ContainerPage
from .widgets.deploy_page import DeployPage
from .widgets.image_page import ImagePage
from .widgets.launch_page import LaunchPage
from .widgets.server_page import ServerPage

_STEPS = [
    ("1. 服务器管理", "选择/管理要部署的服务器，校验设备型号"),
    ("2. 镜像构建", "基础镜像 + UCM whl -> 带 UCM 的引擎镜像"),
    ("3. 容器创建", "docker run（kvcache 挂载/模型映射/设备映射）"),
    ("4. 部署配置", "PD 拓扑 / vllm/sglang 参数 / 生成启动脚本"),
    ("5. 拉起服务", "部署脚本、按序拉起、健康检查、日志"),
]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{__version__}")
        self.resize(1280, 860)
        self.ctx = AppContext()

        self.nav = QListWidget()
        self.nav.setFixedWidth(190)
        for title, tip in _STEPS:
            item = QListWidgetItem(title)
            item.setToolTip(tip)
            self.nav.addItem(item)
        self.nav.setCurrentRow(0)
        self.nav.currentRowChanged.connect(self._switch)

        self.pages = QStackedWidget()
        self.server_page = ServerPage(self.ctx)
        self.image_page = ImagePage(self.ctx)
        self.container_page = ContainerPage(self.ctx)
        self.deploy_page = DeployPage(self.ctx)
        self.launch_page = LaunchPage(self.ctx)
        for p in (self.server_page, self.image_page, self.container_page,
                  self.deploy_page, self.launch_page):
            self.pages.addWidget(p)

        prev_btn = QPushButton("← 上一步")
        next_btn = QPushButton("下一步 →")
        prev_btn.clicked.connect(self._prev)
        next_btn.clicked.connect(self._next)
        nav_btns = QHBoxLayout()
        nav_btns.addWidget(prev_btn)
        nav_btns.addStretch(1)
        nav_btns.addWidget(next_btn)

        left = QVBoxLayout()
        left.addWidget(self.nav)
        left.addStretch(1)
        left.addLayout(nav_btns)
        left_widget = QWidget()
        left_widget.setLayout(left)

        center = QHBoxLayout()
        center.addWidget(left_widget)
        center.addWidget(self.pages, 1)
        container = QWidget()
        container.setLayout(center)
        self.setCentralWidget(container)

    # ------------------------------------------------------------ 导航
    def _switch(self, row: int) -> None:
        if row == 0:
            pass
        elif row == 1:
            if not self._require_servers():
                self.nav.setCurrentRow(0)
                return
            self.image_page.on_enter()
        elif row == 2:
            if not self._require_servers():
                self.nav.setCurrentRow(0)
                return
            self.container_page.on_enter()
        elif row == 3:
            if not self._require_servers():
                self.nav.setCurrentRow(0)
                return
            self.deploy_page.on_enter()
        elif row == 4:
            if not self._require_servers():
                self.nav.setCurrentRow(0)
                return
            if self.ctx.scripts is None:
                QMessageBox.information(
                    self, "提示", "请先在步骤4生成部署脚本后再进入本页")
                self.nav.setCurrentRow(3)
                return
            self.launch_page.on_enter()
        self.pages.setCurrentIndex(row)

    def _require_servers(self) -> bool:
        if not self.ctx.selected:
            QMessageBox.warning(self, "未选择服务器",
                                "请先在「1. 服务器管理」勾选要部署的服务器")
            return False
        return True

    def _prev(self) -> None:
        row = self.nav.currentRow()
        if row > 0:
            self.nav.setCurrentRow(row - 1)

    def _next(self) -> None:
        row = self.nav.currentRow()
        if row == 0:
            self.server_page._collect_selection()
        if row < len(_STEPS) - 1:
            self.nav.setCurrentRow(row + 1)

    def closeEvent(self, event) -> None:
        event.accept()
