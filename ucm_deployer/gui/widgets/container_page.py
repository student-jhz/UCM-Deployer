# -*- coding: utf-8 -*-
"""步骤3：容器创建 —— 选择 UCM 镜像（选择时自动校验）、docker run 生成/编辑/执行。

自动识别服务器卡数并映射全部加速卡（NPU: 全部 davinci 设备 + 驱动挂载；
GPU: --runtime nvidia --gpus all）。用户选择模型目录、kvcache 目录与自定义挂载。
"""
from __future__ import annotations

from typing import Dict, List

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.container_manager import (ContainerCreateConfig,
                                       ContainerManager, check_shared_fs,
                                       evaluate_shared_fs)
from ...core.docker_manager import DockerManager
from ...core.models import DeviceType, UCMInfo, VolumeMount
from ...core.ssh_client import SSHClient
from ..state import AppContext
from .common import (ParallelTaskPanel, QuickTask, RemoteDirDialog, combo_ref,
                     fill_image_combo, setup_image_combo)


class _PathPickRow(QWidget):
    """路径输入 + 浏览（远端目录）"""

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
        server = self.page.ctx.server
        if server is None:
            QMessageBox.information(self, "提示", "请先在步骤1勾选要登录的服务器")
            return

        def lister(path: str) -> List[str]:
            with SSHClient(server) as ssh:
                return ContainerManager(ssh).list_host_dirs(path)

        start = self.edit.text().strip() or "/"
        dlg = RemoteDirDialog(self, lister, start=start)
        if dlg.exec() == QDialog.Accepted and dlg.selected_path():
            self.edit.setText(dlg.selected_path())

    def text(self) -> str:
        return self.edit.text().strip()


class ContainerPage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._images = []
        self._ucm_state: Dict[str, UCMInfo] = {}   # 镜像 ref -> UCM 校验结果
        self._quick_tasks = []                      # 运行中的 QuickTask 引用

        # ---------- 镜像与容器
        top_box = QGroupBox("1. UCM 镜像与容器")
        self.image_combo = self._make_image_combo()
        self.ucm_check_label = QLabel("镜像 UCM：未校验")
        self.ucm_check_label.setProperty("role", "hint")
        self.ucm_check_label.setWordWrap(True)

        refresh_img_btn = QPushButton("🔄 刷新镜像")
        refresh_img_btn.clicked.connect(self._refresh_images)
        self.name_edit = QLineEdit("ucm-vllm")
        self.shm_edit = QLineEdit("512g")
        self.net_edit = QLineEdit("host")
        self.net_edit.setToolTip(
            "docker run --net 参数（Ascend 容器使用；NVIDIA 容器固定用 -p 端口映射）")

        img_form = QFormLayout()
        img_form.addRow("镜像（已装 UCM）", self.image_combo)
        img_form.addRow("", self.ucm_check_label)
        top_btns = QHBoxLayout()
        top_btns.addWidget(refresh_img_btn)
        top_btns.addStretch(1)
        form = QFormLayout()
        form.addRow("容器名", self.name_edit)
        form.addRow("shm-size", self.shm_edit)
        form.addRow("网络(Ascend)", self.net_edit)
        inner = QHBoxLayout()
        inner.addLayout(form)
        inner.addStretch(1)

        top_layout = QVBoxLayout(top_box)
        top_layout.addLayout(img_form)
        top_layout.addLayout(top_btns)
        top_layout.addLayout(inner)

        # ---------- kvcache 挂载
        kv_box = QGroupBox("2. KV Cache 挂载目录")
        self.kv_list = QListWidget()
        self.kv_list.setMaximumHeight(100)
        kv_add_row = _PathPickRow(self)
        kv_add_btn = QPushButton("➕ 添加目录")
        kv_del_btn = QPushButton("🗑 移除选中")
        kv_check_btn = QPushButton("🔍 文件系统校验")
        kv_add_btn.clicked.connect(self._kv_add)
        kv_del_btn.clicked.connect(self._kv_del)
        kv_check_btn.clicked.connect(self._kv_check)
        row = QHBoxLayout()
        row.addWidget(kv_add_row, 1)
        row.addWidget(kv_add_btn)
        row.addWidget(kv_del_btn)
        row.addWidget(kv_check_btn)
        self.kv_hint = QLabel(
            "目录将挂载到容器内相同路径；单机部署本地目录即可，"
            "多目录须位于同一文件系统。")
        self.kv_hint.setProperty("role", "hint")
        self.kv_hint.setWordWrap(True)
        kv_layout = QVBoxLayout(kv_box)
        kv_layout.addLayout(row)
        kv_layout.addWidget(self.kv_list)
        kv_layout.addWidget(self.kv_hint)
        self.kv_add_row = kv_add_row

        # ---------- 模型与其他挂载
        mount_box = QGroupBox("3. 模型路径（只读）与自定义挂载")
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
        ex_layout.addWidget(QLabel("自定义挂载："))
        ex_layout.addWidget(self.extra_table, 1)
        ex_layout.addLayout(ex_row)

        # ---------- 命令预览（可编辑）
        cmd_box = QGroupBox("4. docker run 命令（自动生成，可编辑后执行）")
        self.cmd_edit = QPlainTextEdit()
        self.cmd_edit.setProperty("role", "code")
        font = QFont("Consolas")
        font.setStyleHint(QFont.Monospace)
        self.cmd_edit.setFont(font)
        self.cmd_edit.setPlaceholderText("点击「生成/刷新命令」生成 docker run 命令")
        self.cmd_edit.setMinimumHeight(220)
        regen_btn = QPushButton("🔁 生成/刷新命令")
        create_btn = QPushButton("🚀 创建容器")
        create_btn.setProperty("accent", True)
        check_ctr_btn = QPushButton("✅ 检查容器内 UCM")
        regen_btn.clicked.connect(self._regen)
        create_btn.clicked.connect(self._create)
        check_ctr_btn.clicked.connect(self._check_container_ucm)
        self.cmd_hint = QLabel(
            "自动识别服务器卡数并映射全部加速卡（NPU: 全部 davinci 设备 + 驱动挂载；"
            "GPU: --runtime nvidia --gpus all -p 8000:8000）；命令可编辑，创建时按编辑框内容执行。")
        self.cmd_hint.setProperty("role", "hint")
        self.cmd_hint.setWordWrap(True)
        btns = QHBoxLayout()
        btns.addWidget(regen_btn)
        btns.addWidget(create_btn)
        btns.addWidget(check_ctr_btn)
        btns.addStretch(1)
        cmd_layout = QVBoxLayout(cmd_box)
        cmd_layout.addWidget(self.cmd_edit)
        cmd_layout.addWidget(self.cmd_hint)
        cmd_layout.addLayout(btns)

        self.panel = ParallelTaskPanel()

        # ---------- 配置区放入滚动容器（窗口小时可滚动）
        config_widget = QWidget()
        config_layout = QVBoxLayout(config_widget)
        config_layout.setContentsMargins(0, 0, 0, 0)
        config_layout.addWidget(top_box)
        config_layout.addWidget(kv_box)
        config_layout.addWidget(mount_box)
        config_layout.addWidget(cmd_box)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidget(config_widget)
        self.scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        self.scroll.viewport().setAutoFillBackground(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.scroll, 3)
        layout.addWidget(self.panel, 2)

    # ------------------------------------------------------------ 辅助
    def _make_image_combo(self):
        from PySide6.QtWidgets import QComboBox

        combo = QComboBox()
        setup_image_combo(combo)
        combo.setMinimumWidth(440)
        combo.currentIndexChanged.connect(self._on_image_changed)
        return combo

    def on_enter(self) -> None:
        self._refresh_images()

    def current_image(self) -> str:
        return combo_ref(self.image_combo)

    def _run_quick(self, fn, on_done) -> None:
        """后台快任务：保持引用防止运行中线程被回收，完成后自动清理。"""
        task = QuickTask(fn, self)
        task.signals.sig_done.connect(on_done)

        def _cleanup(*_):
            if task in self._quick_tasks:
                self._quick_tasks.remove(task)

        task.signals.sig_done.connect(_cleanup)
        self._quick_tasks.append(task)
        task.start()

    def _on_image_changed(self, *_) -> None:
        """选择镜像时自动校验镜像内 UCM 安装情况。"""
        ref = self.current_image()
        if not ref:
            self.ucm_check_label.setText("镜像 UCM：未校验")
            return
        if ref in self._ucm_state:
            self._show_ucm_state(ref)
            return
        server = self.ctx.server
        if server is None:
            return
        self.ucm_check_label.setText(f"镜像 UCM：正在检查 {ref} ...")

        def fn():
            with SSHClient(server) as ssh:
                return DockerManager(ssh).image_ucm_info(ref)

        def on_done(ucm, error):
            if error is not None:
                self._ucm_state[ref] = UCMInfo()
                self.ucm_check_label.setText(f"✗ 镜像 UCM：检查失败（{error}）")
                return
            self._ucm_state[ref] = ucm
            self._show_ucm_state(ref)

        self._run_quick(fn, on_done)

    def _show_ucm_state(self, ref: str) -> None:
        ucm = self._ucm_state.get(ref)
        server = self.ctx.server
        if ucm is None:
            return
        if ucm.installed:
            self.ucm_check_label.setText(f"✓ 镜像 UCM：已安装 {ucm.version}")
            if server is not None:
                self.ctx.images[server.id] = ref
        else:
            self.ucm_check_label.setText(
                f"✗ 镜像 UCM：{ref} 未安装 UCM（请回步骤2构建，或选择其他镜像）")

    # ------------------------------------------------------------ 命令生成
    def _build_config(self) -> ContainerCreateConfig:
        server = self.ctx.server
        model_mount = None
        if self.model_host.text():
            model_mount = VolumeMount(self.model_host.text(),
                                      self.model_container.text().strip() or "/models",
                                      self.model_ro.isChecked())
        extra_mounts = []
        for row in range(self.extra_table.rowCount()):
            host = (self.extra_table.item(row, 0).text()
                    if self.extra_table.item(row, 0) else "").strip()
            cont = (self.extra_table.item(row, 1).text()
                    if self.extra_table.item(row, 1) else "").strip()
            ro_item = self.extra_table.item(row, 2)
            ro = ro_item is not None and ro_item.checkState() == Qt.Checked
            if host and cont:
                extra_mounts.append(VolumeMount(host, cont, ro))
        kv_dirs = [self.kv_list.item(i).text() for i in range(self.kv_list.count())]
        dev = self.ctx.device_of(server) if server else None
        return ContainerCreateConfig(
            image=self.current_image(), name=self.name_edit.text().strip(),
            device_type=dev.device_type if dev else DeviceType.UNKNOWN,
            device_count=dev.count if dev else 0,
            shm_size=self.shm_edit.text().strip() or "512g",
            network=self.net_edit.text().strip(),
            kv_cache_dirs=kv_dirs, model_mount=model_mount,
            extra_mounts=extra_mounts)

    def _regen(self) -> None:
        server = self.ctx.server
        if server is None:
            QMessageBox.information(self, "提示", "请先在步骤1勾选要登录的服务器")
            return
        try:
            cmd = ContainerManager.generate_run_command(self._build_config())
        except Exception as exc:
            QMessageBox.warning(self, "生成失败", str(exc))
            return
        self.cmd_edit.setPlainText(cmd)

    def command_text(self) -> str:
        return self.cmd_edit.toPlainText()

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
        server = self.ctx.server
        if server is None:
            return

        def fn():
            with SSHClient(server) as ssh:
                return check_shared_fs(ssh, dirs)

        def on_done(report, error):
            if error is not None or report is None:
                QMessageBox.warning(self, "校验失败", str(error))
                return
            merged = evaluate_shared_fs(report.entries)
            icon = QMessageBox.Information if merged.ok else QMessageBox.Warning
            box = QMessageBox(icon, "文件系统校验",
                              merged.message
                              + ("\n\n" + "\n".join(merged.warnings)
                                 if merged.warnings else ""))
            box.exec()

        self._run_quick(fn, on_done)

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
        server = self.ctx.server
        if server is None:
            fill_image_combo(self.image_combo, [])
            return

        def fn():
            with SSHClient(server) as ssh:
                return DockerManager(ssh).list_images()

        def on_done(images, error):
            if error is not None:
                return
            self._images = images or []
            current = self.ctx.images.get(server.id, "")
            fill_image_combo(self.image_combo, self._images, current=current)
            # fill_image_combo 内部 blockSignals，手动触发选中镜像的 UCM 校验
            if self.current_image():
                self._on_image_changed()

        self._run_quick(fn, on_done)

    # ------------------------------------------------------------ 创建
    def _create(self) -> None:
        server = self.ctx.server
        if server is None:
            QMessageBox.information(self, "提示", "请先在步骤1勾选要登录的服务器")
            return
        ref = self.current_image()
        if not ref:
            QMessageBox.warning(self, "参数错误", "请先选择镜像（刷新镜像列表后选择）")
            return
        ucm = self._ucm_state.get(ref)
        if ucm is None:
            QMessageBox.warning(
                self, "未校验 UCM",
                f"镜像 {ref} 的 UCM 安装情况尚未校验完成，请稍候或重新选择镜像。")
            return
        if not ucm.installed:
            QMessageBox.warning(
                self, "镜像未安装 UCM",
                f"镜像 {ref} 未安装 UCM。\n请回步骤2构建带 UCM 的镜像，或选择其他镜像。")
            return

        text = self.command_text()
        if not text.strip():
            try:
                text = ContainerManager.generate_run_command(self._build_config())
                self.cmd_edit.setPlainText(text)
            except Exception as exc:
                QMessageBox.warning(self, "参数错误", str(exc))
                return

        cmd = text

        def fn(ssh, tctx):
            tctx.progress(20, "创建容器...")
            cm = ContainerManager(ssh, DockerManager(ssh))
            tctx.log("$ " + cmd.replace("\n", " \\\n"))
            cm.create(cmd, on_line=tctx.log)
            name = self.name_edit.text().strip()
            self.ctx.containers[server.id] = name
            tctx.progress(100, f"容器 {name} 已创建")

        def on_finished(all_ok: bool) -> None:
            if all_ok:
                QMessageBox.information(
                    self, "完成",
                    f"容器创建完成: {self.name_edit.text().strip()}\n"
                    "可执行「检查容器内 UCM」确认容器可用。")

        self.panel.run_tasks([(server, fn)], "创建容器", on_finished=on_finished)

    def _check_container_ucm(self) -> None:
        server = self.ctx.server
        if server is None:
            return
        name = self.name_edit.text().strip()

        def fn(ssh, tctx):
            ucm = DockerManager(ssh).container_ucm_info(name)
            tctx.log(f"容器 {name}: {ucm}")
            if not ucm.installed:
                raise RuntimeError(f"容器 {name} 内未检测到 UCM")
            self.ctx.containers[server.id] = name

        self.panel.run_tasks([(server, fn)], "检查容器 UCM")
