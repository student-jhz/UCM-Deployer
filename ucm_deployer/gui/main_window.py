# -*- coding: utf-8 -*-
"""主窗口：左侧五步导航 + 右侧步骤页面 + 状态栏/菜单。"""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
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
        self.resize(1320, 880)
        self.ctx = AppContext()

        # ---------- 导航
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(200)
        for title, tip in _STEPS:
            item = QListWidgetItem(title)
            item.setToolTip(tip)
            self.nav.addItem(item)
        self.nav.setCurrentRow(0)
        self.nav.currentRowChanged.connect(self._switch)

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
        left.addWidget(self.nav)
        left.addStretch(1)
        left.addLayout(nav_btns)
        left_widget = QWidget()
        left_widget.setLayout(left)

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

    def _open_manual(self) -> None:
        candidates = []
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(os.path.join(meipass, "docs", "用户手册.md"))
        candidates.append(os.path.join(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))), "docs", "用户手册.md"))
        for path in candidates:
            if os.path.isfile(path):
                try:
                    os.startfile(path)
                    return
                except Exception:
                    pass
        QMessageBox.information(
            self, "用户手册",
            "未找到用户手册文件。\n可查看仓库 docs/用户手册.md，\n"
            "或使用「帮助 → 关于」中的仓库地址。")

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
    def _switch(self, row: int) -> None:
        if row == 0:
            pass
        elif row in (1, 2, 3):
            if not self._require_servers():
                self.nav.setCurrentRow(0)
                return
            if row == 1:
                self.image_page.on_enter()
            elif row == 2:
                self.container_page.on_enter()
            else:
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
            # 关键：把步骤4中用户的最新脚本编辑同步进 ctx.scripts
            self.deploy_page.sync_edits()
            self.launch_page.on_enter()
        self.pages.setCurrentIndex(row)
        self._update_status()

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
