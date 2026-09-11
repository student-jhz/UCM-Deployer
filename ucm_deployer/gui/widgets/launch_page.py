# -*- coding: utf-8 -*-
"""步骤5：拉起服务 —— 脚本部署、按依赖顺序拉起、健康检查、日志跟踪、停止。"""
from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.docker_manager import DockerManager
from ...core.models import ServerInfo
from ...core.ssh_client import SSHClient
from ...service import deploy_service
from ..state import AppContext
from .common import LogViewDialog, ParallelTaskPanel, QuickTask


class LaunchPage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx

        info_box = QGroupBox("部署脚本")
        self.info_label = QLabel("尚未生成部署脚本，请回到步骤4生成。")
        self.info_label.setWordWrap(True)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["脚本", "服务器", "容器", "状态", "操作"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)

        deploy_btn = QPushButton("📦 部署脚本到容器")
        launch_all_btn = QPushButton("🚀 全部拉起（按依赖顺序）")
        health_btn = QPushButton("❤ 健康检查")
        stop_all_btn = QPushButton("⏹ 全部停止")
        deploy_btn.clicked.connect(self._deploy_scripts)
        launch_all_btn.clicked.connect(self._launch_all)
        health_btn.clicked.connect(self._health_check)
        stop_all_btn.clicked.connect(self._stop_all)

        btns = QHBoxLayout()
        for b in (deploy_btn, launch_all_btn, health_btn, stop_all_btn):
            btns.addWidget(b)
        btns.addStretch(1)

        self.hint = QLabel(
            "拉起顺序：mooncake master → ray head/worker → prefill/decode/serve → 负载均衡。\n"
            "健康检查在全部拉起后执行（引擎启动需 1-10 分钟，可多次点击重试）。")
        self.hint.setWordWrap(True)

        ib_layout = QVBoxLayout(info_box)
        ib_layout.addWidget(self.info_label)
        ib_layout.addWidget(self.table)

        self.panel = ParallelTaskPanel()

        layout = QVBoxLayout(self)
        layout.addWidget(info_box)
        layout.addLayout(btns)
        layout.addWidget(self.hint)
        layout.addWidget(self.panel, 2)

    # ------------------------------------------------------------ 状态
    def on_enter(self) -> None:
        self._refresh_table()

    def _server_of(self, server_id: str) -> Optional[ServerInfo]:
        for s in self.ctx.selected:
            if s.id == server_id:
                return s
        return None

    def _scripts(self):
        return (self.ctx.scripts.scripts if self.ctx.scripts else [])

    def _refresh_table(self) -> None:
        ds = self.ctx.scripts
        self.table.setRowCount(0)
        if not ds or not ds.scripts:
            self.info_label.setText("尚未生成部署脚本，请回到步骤4生成。")
            return
        self.info_label.setText(
            f"拓扑: {ds.summary}    脚本 {len(ds.scripts)} 个 / 配置 {len(ds.config_files)} 个")
        for s in deploy_service.ordered_scripts(ds):
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(s.name))
            self.table.setItem(row, 1, QTableWidgetItem(s.server_name))
            self.table.setItem(row, 2, QTableWidgetItem(s.container))
            status = QTableWidgetItem("未拉起")
            status.setForeground(Qt.gray)
            self.table.setItem(row, 3, status)
            launch = QPushButton("拉起")
            log = QPushButton("日志")
            stop = QPushButton("停止")
            launch.clicked.connect(lambda _=False, ss=s: self._launch_one(ss))
            log.clicked.connect(lambda _=False, ss=s: self._show_log(ss))
            stop.clicked.connect(lambda _=False, ss=s: self._stop_one(ss))
            cell = QWidget()
            h = QHBoxLayout(cell)
            h.setContentsMargins(2, 2, 2, 2)
            h.addWidget(launch)
            h.addWidget(log)
            h.addWidget(stop)
            self.table.setCellWidget(row, 4, cell)

    def _set_row_status(self, script, text: str, ok: Optional[bool] = None) -> None:
        for row in range(self.table.rowCount()):
            if (self.table.item(row, 0) is not None
                    and self.table.item(row, 0).text() == script.name
                    and self.table.item(row, 1) is not None
                    and self.table.item(row, 1).text() == script.server_name):
                item = self.table.item(row, 3)
                if item is not None:
                    item.setText(text)
                    if ok is True:
                        item.setForeground(Qt.darkGreen)
                    elif ok is False:
                        item.setForeground(Qt.red)

    # ------------------------------------------------------------ 操作
    def _deploy_scripts(self) -> None:
        ds = self.ctx.scripts
        if not ds:
            QMessageBox.information(self, "提示", "请先在步骤4生成部署脚本")
            return

        def make_fn(server):
            scripts = deploy_service.scripts_by_server(ds).get(server.id, [])
            configs = deploy_service.config_files_by_server(ds).get(server.id, [])

            def fn(ssh, tctx):
                all_files = scripts + configs
                if not all_files:
                    tctx.log("无需要部署的文件")
                    return
                tctx.progress(10, f"部署 {len(all_files)} 个文件")
                deploy_service.deploy_files(ssh, DockerManager(ssh),
                                            all_files[0].container, all_files)
                for s in all_files:
                    tctx.log(f"已写入 {s.path}")
                tctx.progress(100, "部署完成")
            return fn

        def on_done(all_ok):
            try:
                if all_ok:
                    QMessageBox.information(self, "完成", "脚本已部署到各容器 /root/ucm-deploy/")
            finally:
                self.panel.finished_all.disconnect(on_done)

        self.panel.finished_all.connect(on_done)
        self.panel.start([(s, make_fn(s)) for s in self.ctx.selected], "部署脚本")

    def _launch_one(self, script) -> None:
        server = self._server_of(script.server_id)
        if server is None:
            return

        def work():
            with SSHClient(server) as ssh:
                deploy_service.launch_script(ssh, DockerManager(ssh),
                                             script.container, script)
        task = QuickTask(work, self)
        task.sig_done.connect(lambda res, err: self._after_launch(script, err))
        task.start()

    def _after_launch(self, script, err) -> None:
        if err:
            self._set_row_status(script, f"拉起失败: {err}", False)
        else:
            self._set_row_status(script, "已拉起", True)

    def _launch_all(self) -> None:
        ds = self.ctx.scripts
        if not ds or not ds.scripts:
            QMessageBox.information(self, "提示", "请先在步骤4生成部署脚本")
            return
        if self.panel.is_running():
            QMessageBox.warning(self, "忙碌", "有任务在执行")
            return
        self._pending = list(deploy_service.ordered_scripts(ds))
        self._launch_next_pending()

    def _launch_next_pending(self) -> None:
        if not getattr(self, "_pending", None):
            QMessageBox.information(
                self, "拉起完成",
                "全部脚本已拉起。引擎启动需要时间，稍后请点击「健康检查」。\n"
                "可点击各脚本「日志」查看启动进度。")
            return
        script = self._pending.pop(0)
        self._launch_one_auto(script)

    def _launch_one_auto(self, script) -> None:
        server = self._server_of(script.server_id)
        if server is None:
            self._launch_next_pending()
            return
        self._set_row_status(script, "拉起中...")

        def work():
            with SSHClient(server) as ssh:
                deploy_service.launch_script(ssh, DockerManager(ssh),
                                             script.container, script)
        task = QuickTask(work, self)
        task.sig_done.connect(lambda res, err: self._after_launch_auto(script, err))
        task.start()

    def _after_launch_auto(self, script, err) -> None:
        self._after_launch(script, err)
        self._launch_next_pending()

    def _health_check(self) -> None:
        ds = self.ctx.scripts
        if not ds or not ds.health_checks:
            QMessageBox.information(self, "提示", "无健康检查项（请先生成部署脚本）")
            return

        def make_fn(server):
            checks = [h for h in ds.health_checks if h.server_id == server.id]

            def fn(ssh, tctx):
                ok_any = True
                for h in checks:
                    ok, msg = deploy_service.health_check(ssh, h.url)
                    tctx.log(f"[{h.label}] {h.url} -> {msg}")
                    if not ok:
                        ok_any = False
                if not checks:
                    tctx.log("本服务器无检查项")
                tctx.progress(100, "健康" if ok_any else "未就绪")
                if not ok_any:
                    raise RuntimeError("存在未就绪服务（引擎可能仍在启动，稍后重试）")
            return fn

        self.panel.start([(s, make_fn(s)) for s in self.ctx.selected], "健康检查")

    def _show_log(self, script) -> None:
        server = self._server_of(script.server_id)
        if server is None:
            return

        def loader(n: int) -> str:
            with SSHClient(server) as ssh:
                return deploy_service.tail_log(ssh, DockerManager(ssh),
                                               script.container, script.log_path, n)

        dlg = LogViewDialog(self, loader, f"{script.name} @ {script.server_name}")
        dlg.exec()

    def _stop_one(self, script) -> None:
        server = self._server_of(script.server_id)
        if server is None:
            return

        def work():
            with SSHClient(server) as ssh:
                deploy_service.stop_script(ssh, DockerManager(ssh),
                                           script.container, script)
        task = QuickTask(work, self)
        task.sig_done.connect(
            lambda res, err: self._set_row_status(
                script, "已停止" if not err else f"停止失败:{err}", None if err else True))
        task.start()

    def _stop_all(self) -> None:
        ds = self.ctx.scripts
        if not ds or not ds.scripts:
            return

        def make_fn(server):
            scripts = deploy_service.scripts_by_server(ds).get(server.id, [])

            def fn(ssh, tctx):
                for s in reversed(scripts):
                    deploy_service.stop_script(ssh, DockerManager(ssh),
                                               s.container, s)
                    tctx.log(f"已停止 {s.name}")
                tctx.progress(100, "完成")
            return fn

        def on_done(all_ok):
            try:
                self._refresh_table()
            finally:
                self.panel.finished_all.disconnect(on_done)

        self.panel.finished_all.connect(on_done)
        self.panel.start([(s, make_fn(s)) for s in self.ctx.selected], "停止服务")
