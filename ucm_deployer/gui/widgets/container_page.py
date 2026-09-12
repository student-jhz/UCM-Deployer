# -*- coding: utf-8 -*-
"""步骤3：容器创建 —— UCM 镜像校验、kvcache 挂载、docker run 命令生成/编辑/执行。"""
from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.container_manager import (ContainerCreateConfig,
                                       ContainerManager, check_shared_fs,
                                       evaluate_shared_fs)
from ...core.docker_manager import DockerManager
from ...core.models import VolumeMount
from ...core.ssh_client import SSHClient
from ..state import AppContext
from .common import ParallelTaskPanel, RemoteDirDialog


class _PathPickRow(QWidget):
    """路径输入 + 浏览(远端目录, 多服务器可选) """

    def __init__(self, page: "ContainerPage", placeholder: str = "/mnt/..."):
        super().__init__()
        self.page = page
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        browse = QPushButton("浏览...")
        browse.clicked.connect(self._browse)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit, 1)
        layout.addWidget(browse)

    def _browse(self) -> None:
        servers = self.page.ctx.selected
        if not servers:
            QMessageBox.information(self, "提示", "请先在步骤1选择服务器")
            return

        def make_lister(server):
            def lister(path: str) -> List[str]:
                with SSHClient(server) as ssh:
                    return ContainerManager(ssh).list_host_dirs(path)
            return lister

        listers = {f"{s.name} ({s.host})": make_lister(s) for s in servers}
        start = self.edit.text().strip() or "/"
        dlg = RemoteDirDialog(self, listers, start=start)
        if dlg.exec() == QDialog.Accepted and dlg.selected_path():
            self.edit.setText(dlg.selected_path())

    def text(self) -> str:
        return self.edit.text().strip()


class ContainerPage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx

        # ---------- 镜像与容器名
        top_box = QGroupBox("1. UCM 镜像与容器")
        self.image_rows: Dict[str, QComboBox] = {}
        self.image_form = QFormLayout()
        self.placeholder = QLabel("请先在「1. 服务器管理」勾选要部署的服务器")
        self.placeholder.setProperty("role", "hint")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setMinimumHeight(40)
        top_box.setLayout(QVBoxLayout())
        top_box.layout().addWidget(self.placeholder)
        top_box.layout().addLayout(self.image_form)
        self.name_edit = QLineEdit("ucm-vllm")
        self.shm_edit = QLineEdit("512g")
        self.net_edit = QLineEdit("host")
        form = QFormLayout()
        form.addRow("容器名", self.name_edit)
        form.addRow("shm-size", self.shm_edit)
        form.addRow("网络", self.net_edit)
        inner = QHBoxLayout()
        inner.addLayout(form)
        inner.addStretch(1)

        # ---------- kvcache 挂载
        kv_box = QGroupBox("2. UCM kvcache 持久化挂载目录（多目录须同一共享文件系统）")
        self.kv_list = QListWidget()
        self.kv_list.setMaximumHeight(110)
        kv_add_row = _PathPickRow(self)
        kv_add_btn = QPushButton("➕ 添加目录")
        kv_del_btn = QPushButton("🗑 移除选中")
        kv_check_btn = QPushButton("🔍 共享文件系统校验")
        kv_add_btn.clicked.connect(self._kv_add)
        kv_del_btn.clicked.connect(self._kv_del)
        kv_check_btn.clicked.connect(self._kv_check)
        row = QHBoxLayout()
        row.addWidget(kv_add_row, 1)
        row.addWidget(kv_add_btn)
        row.addWidget(kv_del_btn)
        row.addWidget(kv_check_btn)
        self.kv_hint = QLabel(
            "⚠ 多个挂载目录必须位于同一共享文件系统；跨服务器共用时必须是 NFS/3FS 等网络存储。\n"
            "⚠ 只有「同一种模型 + 同一种部署模式(P节点数、DP/TP 一致)」的服务才能共用同一套挂载目录。")
        self.kv_hint.setProperty("role", "hint")
        self.kv_hint.setWordWrap(True)
        kv_layout = QVBoxLayout(kv_box)
        kv_layout.addLayout(row)
        kv_layout.addWidget(self.kv_list)
        kv_layout.addWidget(self.kv_hint)
        self.kv_add_row = kv_add_row

        # ---------- 模型与其他挂载
        mount_box = QGroupBox("3. 模型路径（只读）与其他挂载")
        self.model_host = _PathPickRow(self, "/data/models/...")
        self.model_container = QLineEdit("/models")
        self.model_ro = QCheckBox("只读(ro)")
        self.model_ro.setChecked(True)
        mform = QFormLayout()
        r = QHBoxLayout()
        r.addWidget(self.model_host, 1)
        r.addWidget(QLabel("→ 容器内"))
        r.addWidget(self.model_container, 1)
        r.addWidget(self.model_ro)
        mform.addRow("模型路径", r)

        self.extra_table = QTableWidget(0, 3)
        self.extra_table.setHorizontalHeaderLabels(["宿主机路径", "容器内路径", "只读"])
        self.extra_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.extra_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.extra_table.verticalHeader().setVisible(False)
        ex_add_btn = QPushButton("➕ 添加挂载")
        ex_del_btn = QPushButton("🗑 删除选中")
        ex_add_btn.clicked.connect(self._extra_add)
        ex_del_btn.clicked.connect(self._extra_del)
        ex_row = QHBoxLayout()
        ex_row.addWidget(ex_add_btn)
        ex_row.addWidget(ex_del_btn)
        ex_row.addStretch(1)
        ex_layout = QVBoxLayout(mount_box)
        ex_layout.addLayout(mform)
        ex_layout.addWidget(QLabel("其他挂载："))
        ex_layout.addWidget(self.extra_table, 1)
        ex_layout.addLayout(ex_row)

        # ---------- 命令预览（每服务器一页，可分别编辑）
        cmd_box = QGroupBox("4. docker run 命令（自动生成，可编辑）")
        self.cmd_tabs = QTabWidget()
        self.cmd_edits: Dict[str, QPlainTextEdit] = {}
        regen_btn = QPushButton("🔁 生成/刷新命令")
        regen_btn.clicked.connect(self._regen)
        create_btn = QPushButton("🚀 创建容器（全部服务器）")
        create_btn.setProperty("accent", True)
        create_btn.clicked.connect(self._create)
        check_ctr_btn = QPushButton("✅ 检查容器内 UCM")
        check_ctr_btn.clicked.connect(self._check_container_ucm)
        self.cmd_hint = QLabel(
            "每台服务器一个命令页（卡数/镜像可能不同），可分别编辑；创建时按各页内容执行。")
        self.cmd_hint.setProperty("role", "hint")
        btns = QHBoxLayout()
        btns.addWidget(regen_btn)
        btns.addWidget(create_btn)
        btns.addWidget(check_ctr_btn)
        btns.addStretch(1)
        cmd_layout = QVBoxLayout(cmd_box)
        cmd_layout.addWidget(self.cmd_tabs)
        cmd_layout.addWidget(self.cmd_hint)
        cmd_layout.addLayout(btns)

        self.panel = ParallelTaskPanel()

        # 顶部工具行
        refresh_img_btn = QPushButton("🔄 刷新镜像")
        check_img_btn = QPushButton("✅ 检查镜像内 UCM")
        top_row = QHBoxLayout()
        top_row.addWidget(refresh_img_btn)
        top_row.addWidget(check_img_btn)
        top_row.addStretch(1)
        refresh_img_btn.clicked.connect(self._refresh_images)
        check_img_btn.clicked.connect(self._check_image_ucm)
        top_box.layout().addLayout(top_row)
        top_box.layout().addLayout(inner)

        layout = QVBoxLayout(self)
        layout.addWidget(top_box)
        layout.addWidget(kv_box)
        layout.addWidget(mount_box)
        layout.addWidget(cmd_box)
        layout.addWidget(self.panel, 2)

    # ------------------------------------------------------------ 辅助
    def current_server(self) -> Optional[object]:
        return self.ctx.selected[0] if self.ctx.selected else None

    def on_enter(self) -> None:
        for i in reversed(range(self.image_form.count())):
            item = self.image_form.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.image_rows.clear()
        self.placeholder.setVisible(not self.ctx.selected)
        for s in self.ctx.selected:
            combo = QComboBox()
            combo.setEditable(True)
            combo.setMinimumWidth(360)
            current = self.ctx.images.get(s.id, "")
            if current:
                combo.addItem(current)
            self.image_rows[s.id] = combo
            self.image_form.addRow(f"{s.name} ({s.host})", combo)
        self._rebuild_cmd_tabs()
        if self.ctx.selected and not getattr(self, "_image_refs", None):
            self._refresh_images()

    # ------------------------------------------------------------ 命令生成
    def _build_config(self, server) -> ContainerCreateConfig:
        model_mount = None
        if self.model_host.text():
            model_mount = VolumeMount(self.model_host.text(),
                                      self.model_container.text().strip() or "/models",
                                      self.model_ro.isChecked())
        extra_mounts = []
        for row in range(self.extra_table.rowCount()):
            host = (self.extra_table.item(row, 0).text() if self.extra_table.item(row, 0) else "").strip()
            cont = (self.extra_table.item(row, 1).text() if self.extra_table.item(row, 1) else "").strip()
            ro_item = self.extra_table.item(row, 2)
            ro = ro_item is not None and ro_item.checkState() == Qt.Checked
            if host and cont:
                extra_mounts.append(VolumeMount(host, cont, ro))
        kv_dirs = [self.kv_list.item(i).text() for i in range(self.kv_list.count())]
        dev = self.ctx.device_of(server)
        combo = self.image_rows.get(server.id)
        image = combo.currentText().strip() if combo else self.ctx.images.get(server.id, "")
        return ContainerCreateConfig(
            image=image, name=self.name_edit.text().strip(),
            device_type=dev.device_type, device_count=dev.count,
            shm_size=self.shm_edit.text().strip() or "512g",
            network=self.net_edit.text().strip(),
            kv_cache_dirs=kv_dirs, model_mount=model_mount,
            extra_mounts=extra_mounts)

    def _rebuild_cmd_tabs(self) -> None:
        """按已选服务器重建命令页签（保留已编辑内容）。"""
        old_texts = {sid: e.toPlainText()
                     for sid, e in self.cmd_edits.items()}
        self.cmd_tabs.clear()
        self.cmd_edits.clear()
        for s in self.ctx.selected:
            edit = QPlainTextEdit()
            edit.setProperty("role", "code")
            font = QFont("Consolas")
            font.setStyleHint(QFont.Monospace)
            edit.setFont(font)
            edit.setPlaceholderText("点击「生成/刷新命令」生成 docker run 命令")
            if s.id in old_texts and old_texts[s.id].strip():
                edit.setPlainText(old_texts[s.id])
            self.cmd_edits[s.id] = edit
            self.cmd_tabs.addTab(edit, s.name)

    def _regen(self) -> None:
        if not self.ctx.selected:
            QMessageBox.information(self, "提示", "请先在步骤1选择服务器")
            return
        self._rebuild_cmd_tabs()
        for s in self.ctx.selected:
            try:
                cfg = self._build_config(s)
                cmd = ContainerManager.generate_run_command(cfg)
            except Exception as exc:
                QMessageBox.warning(self, "生成失败", f"{s.name}: {exc}")
                return
            self.cmd_edits[s.id].setPlainText(cmd)

    def command_text(self, server_id: str) -> str:
        edit = self.cmd_edits.get(server_id)
        return edit.toPlainText() if edit is not None else ""

    # ------------------------------------------------------------ kvcache
    def _kv_add(self) -> None:
        path = self.kv_add_row.text()
        if not path.startswith("/"):
            QMessageBox.warning(self, "参数错误", "请输入以 / 开头的绝对路径")
            return
        if path not in [self.kv_list.item(i).text() for i in range(self.kv_list.count())]:
            self.kv_list.addItem(path)

    def _kv_del(self) -> None:
        item = self.kv_list.currentItem()
        if item is not None:
            self.kv_list.takeItem(self.kv_list.row(item))

    def _kv_check(self) -> None:
        dirs = [self.kv_list.item(i).text() for i in range(self.kv_list.count())]
        if not dirs:
            QMessageBox.information(self, "提示", "请先添加 kvcache 挂载目录")
            return
        servers = self.ctx.selected
        if not servers:
            return
        reports: Dict[str, object] = {}

        def make_fn(server):
            def fn(ssh, tctx):
                tctx.progress(50, "df 检查中")
                report = check_shared_fs({server.id: ssh}, dirs)
                for e in report.entries:
                    tctx.log(f"[{server.name}] {e.path} -> {e.source} ({e.fstype})"
                             if not e.error else f"[{server.name}] {e.path} {e.error}")
                reports[server.id] = report
                tctx.progress(100, "完成")
            return fn

        def on_finished(all_ok: bool) -> None:
            if not reports:
                return
            entries = []
            for rep in reports.values():
                entries.extend(rep.entries)
            merged = evaluate_shared_fs(entries)
            icon = QMessageBox.Information if merged.ok else QMessageBox.Warning
            box = QMessageBox(icon, "共享文件系统校验",
                              merged.message
                              + ("\n\n" + "\n".join(merged.warnings)
                                 if merged.warnings else ""))
            box.exec()

        self.panel.run_tasks([(s, make_fn(s)) for s in servers],
                             "共享FS校验", on_finished=on_finished)

    def _extra_add(self) -> None:
        row = self.extra_table.rowCount()
        self.extra_table.insertRow(row)
        self.extra_table.setItem(row, 0, QTableWidgetItem("/host/path"))
        self.extra_table.setItem(row, 1, QTableWidgetItem("/container/path"))
        ro = QTableWidgetItem()
        ro.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        ro.setCheckState(Qt.Unchecked)
        self.extra_table.setItem(row, 2, ro)

    def _extra_del(self) -> None:
        row = self.extra_table.currentRow()
        if row >= 0:
            self.extra_table.removeRow(row)

    # ------------------------------------------------------------ 镜像操作
    def _refresh_images(self) -> None:
        def make_fn(server):
            def fn(ssh, tctx):
                dm = DockerManager(ssh)
                refs = [im.ref for im in dm.list_images()]
                tctx.log("\n".join(refs))
                self._image_refs = getattr(self, "_image_refs", {})
                self._image_refs[server.id] = refs
            return fn

        def on_finished(all_ok: bool) -> None:
            if not all_ok:
                return
            refs_map = getattr(self, "_image_refs", {})
            for sid, combo in self.image_rows.items():
                current = combo.currentText()
                combo.clear()
                refs = sorted(refs_map.get(sid, []),
                               key=lambda r: "ucm" not in r.lower())
                combo.addItems(refs)
                if current:
                    combo.setCurrentText(current)
                elif self.ctx.images.get(sid) in refs:
                    combo.setCurrentText(self.ctx.images[sid])

        self.panel.run_tasks([(s, make_fn(s)) for s in self.ctx.selected],
                             "刷新镜像", on_finished=on_finished)

    def _check_image_ucm(self) -> None:
        def make_fn(server):
            def fn(ssh, tctx):
                combo = self.image_rows.get(server.id)
                ref = combo.currentText().strip() if combo else ""
                if not ref:
                    raise RuntimeError("未选择镜像")
                ucm = DockerManager(ssh).image_ucm_info(ref)
                tctx.log(f"{ref}: {ucm}")
                if not ucm.installed:
                    raise RuntimeError(f"镜像 {ref} 未安装 UCM，请回到步骤2构建")
                self.ctx.images[server.id] = ref
            return fn

        self.panel.run_tasks([(s, make_fn(s)) for s in self.ctx.selected],
                             "检查镜像 UCM")

    # ------------------------------------------------------------ 创建
    def _create(self) -> None:
        servers = self.ctx.selected
        if not servers:
            QMessageBox.information(self, "提示", "请先在步骤1选择服务器")
            return
        commands: Dict[str, str] = {}
        for s in servers:
            text = self.command_text(s.id)
            if not text.strip():
                try:
                    text = ContainerManager.generate_run_command(self._build_config(s))
                    self.cmd_edits[s.id].setPlainText(text)
                except Exception as exc:
                    QMessageBox.warning(self, "参数错误", f"{s.name}: {exc}")
                    return
            commands[s.id] = text

        def make_fn(server):
            cmd = commands[server.id]

            def fn(ssh, tctx):
                tctx.progress(20, "创建容器...")
                cm = ContainerManager(ssh, DockerManager(ssh))
                tctx.log("$ " + cmd.replace("\n", " \\\n"))
                cm.create(cmd, on_line=tctx.log)
                name = self.name_edit.text().strip()
                self.ctx.containers[server.id] = name
                tctx.progress(100, f"容器 {name} 已创建")
            return fn

        def on_finished(all_ok: bool) -> None:
            if all_ok:
                QMessageBox.information(
                    self, "完成",
                    f"容器创建完成: {self.name_edit.text().strip()}\n"
                    "建议执行「检查容器内 UCM」后进入下一步。")

        self.panel.run_tasks([(s, make_fn(s)) for s in servers],
                             "创建容器", on_finished=on_finished)

    def _check_container_ucm(self) -> None:
        name = self.name_edit.text().strip()

        def make_fn(server):
            def fn(ssh, tctx):
                ucm = DockerManager(ssh).container_ucm_info(name)
                tctx.log(f"容器 {name}: {ucm}")
                if not ucm.installed:
                    raise RuntimeError(f"容器 {name} 内未检测到 UCM")
                self.ctx.containers[server.id] = name
            return fn

        self.panel.run_tasks([(s, make_fn(s)) for s in self.ctx.selected],
                             "检查容器 UCM")
