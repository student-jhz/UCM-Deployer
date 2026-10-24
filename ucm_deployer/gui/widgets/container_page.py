# -*- coding: utf-8 -*-
"""步骤3：容器创建 —— 选择 UCM 镜像（选择时自动校验）、docker run 生成/编辑/执行。

排版目标：1280x860 一屏可见（无滚动条）——挂载配置合并为一个分组，
提示文字精简，详细说明放 tooltip。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt
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
        layout.setSpacing(4)
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
        self._ascend_devices: Optional[List[str]] = None
        self._ascend_mounts: Optional[List[tuple]] = None
        self._ascend_devices_loading = False

        # ---------- 1. UCM 镜像与容器参数（紧凑：两行）
        top_box = QGroupBox("1. UCM 镜像与容器参数")
        self.image_combo = QComboBox()
        setup_image_combo(self.image_combo)
        self.image_combo.setMinimumWidth(420)
        self.image_combo.currentIndexChanged.connect(self._on_image_changed)
        refresh_img_btn = QPushButton("🔄 刷新镜像")
        refresh_img_btn.clicked.connect(self._refresh_images)
        self.ucm_check_label = QLabel("镜像 UCM：未校验")
        self.ucm_check_label.setProperty("role", "hint")
        self.ucm_check_label.setWordWrap(True)
        self.ucm_check_label.setToolTip("选择镜像后自动在服务器上校验镜像内 UCM 安装情况")
        self.reuse_hint = QLabel(
            "已有安装 UCM 的容器时，可跳过本页创建操作，直接进入步骤4选择该容器预检。")
        self.reuse_hint.setProperty("role", "hint")
        self.reuse_hint.setWordWrap(True)
        self.ascend_device_label = QLabel("Ascend 设备节点：将按服务器实际存在情况自动映射")
        self.ascend_device_label.setProperty("role", "hint")
        self.ascend_device_label.setWordWrap(True)

        self.name_edit = QLineEdit("ucm-vllm")
        self.shm_edit = QLineEdit("512g")
        self.net_edit = QLineEdit("host")
        self.net_edit.setToolTip("docker run --net 参数（Ascend 容器使用；NVIDIA 容器固定 -p 端口映射）")
        self.shm_edit.setFixedWidth(90)
        self.net_edit.setFixedWidth(110)

        img_row = QHBoxLayout()
        img_row.addWidget(self.image_combo, 1)
        img_row.addWidget(refresh_img_btn)
        params_row = QHBoxLayout()
        params_row.addWidget(QLabel("容器名"))
        params_row.addWidget(self.name_edit)
        params_row.addWidget(QLabel("shm-size"))
        params_row.addWidget(self.shm_edit)
        params_row.addWidget(QLabel("网络(Ascend)"))
        params_row.addWidget(self.net_edit)
        params_row.addStretch(1)
        top_layout = QVBoxLayout(top_box)
        top_layout.setSpacing(4)
        top_layout.addLayout(img_row)
        top_layout.addWidget(self.ucm_check_label)
        top_layout.addWidget(self.reuse_hint)
        top_layout.addWidget(self.ascend_device_label)
        top_layout.addLayout(params_row)

        # ---------- 2. 挂载（KV Cache / 模型 / 自定义，合并一个分组）
        mount_box = QGroupBox("2. 挂载（KV Cache / 模型 / 自定义）")
        self.kv_list = QListWidget()
        self.kv_list.setMaximumHeight(72)
        self.kv_list.setToolTip(
            "KV Cache 目录列表（挂载到容器内相同路径）。\n"
            "单机部署本地目录即可；多目录须位于同一文件系统。")
        kv_add_row = _PathPickRow(self, "/mnt/kvcache...")
        kv_add_btn = QPushButton("➕ 添加")
        kv_del_btn = QPushButton("🗑 移除选中")
        kv_check_btn = QPushButton("🔍 文件系统校验")
        kv_add_btn.clicked.connect(self._kv_add)
        kv_del_btn.clicked.connect(self._kv_del)
        kv_check_btn.clicked.connect(self._kv_check)

        self.model_host = _PathPickRow(self, "/data/models/...")
        self.model_container = QLineEdit("/models")
        self.model_ro = QCheckBox("只读")
        self.model_ro.setChecked(True)
        ex_add_btn = QPushButton("➕ 添加挂载")
        ex_del_btn = QPushButton("🗑 删除选中")
        ex_add_btn.clicked.connect(self._extra_add)
        ex_del_btn.clicked.connect(self._extra_del)
        self.extra_table = QTableWidget(0, 3)
        self.extra_table.setHorizontalHeaderLabels(["宿主机路径", "容器内路径", "只读"])
        self.extra_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.extra_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.extra_table.verticalHeader().setVisible(False)
        self.extra_table.setMaximumHeight(96)

        mount_layout = QVBoxLayout(mount_box)
        mount_layout.setSpacing(4)
        kv_row = QHBoxLayout()
        kv_row.addWidget(kv_add_row, 1)
        kv_row.addWidget(kv_add_btn)
        kv_row.addWidget(kv_del_btn)
        kv_row.addWidget(kv_check_btn)
        mount_layout.addLayout(kv_row)
        mount_layout.addWidget(self.kv_list)
        self.kv_add_row = kv_add_row
        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("模型路径"), 0)
        model_row.addWidget(self.model_host, 1)
        model_row.addWidget(QLabel("→ 容器内"))
        model_row.addWidget(self.model_container, 1)
        model_row.addWidget(self.model_ro)
        mount_layout.addLayout(model_row)
        ex_row = QHBoxLayout()
        ex_row.addWidget(QLabel("自定义挂载"))
        ex_row.addWidget(ex_add_btn)
        ex_row.addWidget(ex_del_btn)
        ex_row.addStretch(1)
        mount_layout.addLayout(ex_row)
        mount_layout.addWidget(self.extra_table)

        # ---------- 3. docker run 命令（按钮在上，编辑框占余下空间）
        cmd_box = QGroupBox("3. docker run 命令（自动生成，可编辑后执行）")
        regen_btn = QPushButton("🔁 生成/刷新命令")
        create_btn = QPushButton("🚀 创建容器")
        create_btn.setProperty("accent", True)
        check_ctr_btn = QPushButton("✅ 检查容器内 UCM")
        regen_btn.clicked.connect(self._regen)
        create_btn.clicked.connect(self._create)
        check_ctr_btn.clicked.connect(self._check_container_ucm)
        self.cmd_edit = QPlainTextEdit()
        self.cmd_edit.setProperty("role", "code")
        font = QFont("Consolas")
        font.setStyleHint(QFont.Monospace)
        self.cmd_edit.setFont(font)
        self.cmd_edit.setPlaceholderText("点击「生成/刷新命令」生成 docker run 命令")
        self.cmd_edit.setMinimumHeight(110)
        self.cmd_edit.setToolTip(
            "自动识别服务器卡数并映射全部加速卡：\n"
            "NPU: 全部 /dev/davinci* 设备 + 驱动挂载 + --net=host\n"
            "GPU: --runtime nvidia --gpus all -p 8000:8000\n"
            "命令可编辑，创建容器时按编辑框内容执行。")

        cmd_layout = QVBoxLayout(cmd_box)
        cmd_layout.setSpacing(4)
        btns = QHBoxLayout()
        btns.addWidget(regen_btn)
        btns.addWidget(create_btn)
        btns.addWidget(check_ctr_btn)
        btns.addStretch(1)
        cmd_layout.addLayout(btns)
        cmd_layout.addWidget(self.cmd_edit, 1)

        self.panel = ParallelTaskPanel()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(top_box)
        layout.addWidget(mount_box)
        layout.addWidget(cmd_box, 1)
        layout.addWidget(self.panel, 2)

    # ------------------------------------------------------------ 辅助
    def on_enter(self) -> None:
        server = self.ctx.server
        if server is None:
            self._images = []
            self._ascend_devices = []
            self._ascend_mounts = []
            self._ascend_devices_loading = False
            self.ascend_device_label.setText("Ascend 设备节点：未登录服务器")
            fill_image_combo(self.image_combo, [])
            self.ucm_check_label.setText("镜像 UCM：未登录服务器（请回步骤1勾选）")
            return
        self._refresh_ascend_devices()
        self._refresh_images()

    def current_image(self) -> str:
        return combo_ref(self.image_combo)

    def _refresh_ascend_devices(self, regenerate_when_done: bool = False) -> None:
        server = self.ctx.server
        device = self.ctx.device_of(server) if server else None
        if server is None or device.device_type != DeviceType.ASCEND:
            self._ascend_devices = []
            self._ascend_mounts = []
            self._ascend_devices_loading = False
            self.ascend_device_label.setText(
                "Ascend 设备节点：不适用" if server is not None
                else "Ascend 设备节点：未登录服务器")
            if regenerate_when_done:
                self._regen()
            return
        if self._ascend_devices_loading:
            return

        self._ascend_devices_loading = True
        self._ascend_devices = None
        self._ascend_mounts = None
        self.ascend_device_label.setText("Ascend 设备节点：正在探测实际 /dev 节点...")

        def fn():
            with SSHClient(server) as ssh:
                manager = ContainerManager(ssh)
                return manager.list_ascend_devices(), manager.list_ascend_mounts()

        def on_done(resources, error):
            self._ascend_devices_loading = False
            if error is not None:
                self._ascend_devices = []
                self._ascend_mounts = []
                self.ascend_device_label.setText(
                    "Ascend 设备节点：探测失败，将使用兼容默认映射（可重试）")
            else:
                self._ascend_devices, self._ascend_mounts = resources
                self._ascend_devices = self._ascend_devices or []
                self._ascend_mounts = self._ascend_mounts or []
                davinci_count = sum(
                    node.startswith("/dev/davinci")
                    for node in self._ascend_devices
                )
                self.ascend_device_label.setText(
                    f"Ascend 设备节点：已探测 {davinci_count} 个 davinci 节点、"
                    f"{len(self._ascend_mounts)} 个系统挂载；仅映射实际存在的资源")
            if regenerate_when_done:
                self._regen()

        self._run_quick(fn, on_done)

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
        privileged = any(
            node in ("/dev/ummu", "/dev/uburma")
            for node in (self._ascend_devices or [])
        )
        return ContainerCreateConfig(
            image=self.current_image(), name=self.name_edit.text().strip(),
            device_type=dev.device_type if dev else DeviceType.UNKNOWN,
            device_count=dev.count if dev else 0,
            shm_size=self.shm_edit.text().strip() or "512g",
            network=self.net_edit.text().strip(),
            kv_cache_dirs=kv_dirs, model_mount=model_mount,
            extra_mounts=extra_mounts,
            device_nodes=self._ascend_devices or [],
            system_mounts=self._ascend_mounts or [],
            privileged=privileged)

    def _regen(self) -> None:
        server = self.ctx.server
        if server is None:
            QMessageBox.information(self, "提示", "请先在步骤1勾选要登录的服务器")
            return
        device = self.ctx.device_of(server)
        if (device.device_type == DeviceType.ASCEND
                and (self._ascend_devices is None or self._ascend_mounts is None)):
            self._refresh_ascend_devices(regenerate_when_done=True)
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
            QMessageBox.information(self, "提示", "请先在步骤1勾选要登录的服务器")
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
            QMessageBox.information(self, "提示", "请先在步骤1勾选要登录的服务器")
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
            QMessageBox.information(self, "提示", "请先在步骤1勾选要登录的服务器")
            return
        name = self.name_edit.text().strip()

        def fn(ssh, tctx):
            ucm = DockerManager(ssh).container_ucm_info(name)
            tctx.log(f"容器 {name}: {ucm}")
            if not ucm.installed:
                raise RuntimeError(f"容器 {name} 内未检测到 UCM")
            self.ctx.containers[server.id] = name

        self.panel.run_tasks([(server, fn)], "检查容器 UCM")
