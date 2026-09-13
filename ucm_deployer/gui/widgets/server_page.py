# -*- coding: utf-8 -*-
"""步骤1：服务器管理 —— 登录信息保存/复用、选择部署服务器、设备一致性校验。"""
from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.device_detector import DeviceDetector
from ...core.models import DeviceInfo, ServerInfo
from ..state import AppContext
from .common import ParallelTaskPanel


class ServerEditDialog(QDialog):
    """添加/编辑服务器登录信息。"""

    def __init__(self, parent=None, server: Optional[ServerInfo] = None):
        super().__init__(parent)
        self.setWindowTitle("编辑服务器" if server else "添加服务器")
        self.resize(460, 320)

        self.name_edit = QLineEdit(server.name if server else "")
        self.host_edit = QLineEdit(server.host if server else "")
        self.port_edit = QLineEdit(str(server.port if server else 22))
        self.user_edit = QLineEdit(server.username if server else "root")
        self.auth_combo = QComboBox()
        self.auth_combo.addItems(["密码", "私钥"])
        self.auth_combo.currentIndexChanged.connect(self._auth_changed)
        self.password_edit = QLineEdit(server.password if server else "")
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.key_edit = QLineEdit(server.key_path if server else "")
        self.passphrase_edit = QLineEdit(server.passphrase if server else "")
        self.passphrase_edit.setEchoMode(QLineEdit.Password)
        self.remark_edit = QLineEdit(server.remark if server else "")

        form = QFormLayout()
        form.addRow("名称*", self.name_edit)
        form.addRow("主机 IP*", self.host_edit)
        form.addRow("SSH 端口", self.port_edit)
        form.addRow("用户名", self.user_edit)
        form.addRow("认证方式", self.auth_combo)
        form.addRow("密码", self.password_edit)
        form.addRow("私钥文件", self.key_edit)
        form.addRow("私钥口令", self.passphrase_edit)
        form.addRow("备注", self.remark_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        if server and server.auth_type == "key":
            self.auth_combo.setCurrentIndex(1)
        self._auth_changed()

    def _auth_changed(self) -> None:
        key_mode = self.auth_combo.currentIndex() == 1
        self.password_edit.setEnabled(not key_mode)
        self.key_edit.setEnabled(key_mode)
        self.passphrase_edit.setEnabled(key_mode)

    def _accept(self) -> None:
        if not self.host_edit.text().strip():
            QMessageBox.warning(self, "参数错误", "主机 IP 不能为空")
            return
        try:
            port = int(self.port_edit.text() or 22)
        except ValueError:
            QMessageBox.warning(self, "参数错误", "端口必须是数字")
            return
        self.accept()

    def build_server_info(self, base: Optional[ServerInfo] = None) -> ServerInfo:
        key_mode = self.auth_combo.currentIndex() == 1
        if base is None:
            info = ServerInfo.create(
                name=self.name_edit.text().strip() or self.host_edit.text().strip(),
                host=self.host_edit.text().strip(),
                port=int(self.port_edit.text() or 22),
                username=self.user_edit.text().strip() or "root",
                password="" if key_mode else self.password_edit.text(),
                auth_type="key" if key_mode else "password",
                key_path=self.key_edit.text().strip() if key_mode else "",
                passphrase=self.passphrase_edit.text() if key_mode else "",
                remark=self.remark_edit.text().strip(),
            )
        else:
            base.name = self.name_edit.text().strip() or base.host
            base.host = self.host_edit.text().strip()
            base.port = int(self.port_edit.text() or 22)
            base.username = self.user_edit.text().strip() or "root"
            base.auth_type = "key" if key_mode else "password"
            base.password = "" if key_mode else self.password_edit.text()
            base.key_path = self.key_edit.text().strip() if key_mode else ""
            base.passphrase = self.passphrase_edit.text() if key_mode else ""
            base.remark = self.remark_edit.text().strip()
            info = base
        return info


class ServerPage(QWidget):
    """步骤1 主页面。"""

    selection_changed = Signal()

    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._detected: Dict[str, DeviceInfo] = {}

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["部署", "名称", "IP", "端口", "用户", "设备", "卡数", "备注"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for col in (1, 2, 4, 5, 6):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.itemChanged.connect(self._on_item_changed)

        add_btn = QPushButton("➕ 添加服务器")
        edit_btn = QPushButton("✏ 编辑")
        del_btn = QPushButton("🗑 删除")
        test_btn = QPushButton("🔌 测试连接")
        detect_btn = QPushButton("🔍 探测设备并校验一致性")
        add_btn.clicked.connect(self._add)
        edit_btn.clicked.connect(self._edit)
        del_btn.clicked.connect(self._delete)
        test_btn.clicked.connect(self._test_connections)
        detect_btn.clicked.connect(self._detect_devices)

        btns = QHBoxLayout()
        for b in (add_btn, edit_btn, del_btn, test_btn, detect_btn):
            btns.addWidget(b)
        btns.addStretch(1)

        self.hint = QLabel(
            "勾选「部署」选择本次要部署的服务器；执行部署前会校验设备型号一致性。"
            "登录信息保存在 ~/.ucm_deployer/servers.json（密码加密）。")
        self.hint.setWordWrap(True)

        self.panel = ParallelTaskPanel()

        layout = QVBoxLayout(self)
        layout.addLayout(btns)
        layout.addWidget(self.hint)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.panel, 2)

    # ------------------------------------------------------------ 表格
    def refresh(self) -> None:
        self.table.itemChanged.disconnect(self._on_item_changed)
        servers = self.ctx.registry.list_servers()
        self.table.setRowCount(0)
        selected_ids = {s.id for s in self.ctx.selected}
        for s in servers:
            row = self.table.rowCount()
            self.table.insertRow(row)
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            check.setCheckState(Qt.Checked if s.id in selected_ids else Qt.Unchecked)
            self.table.setItem(row, 0, check)
            self.table.setItem(row, 1, QTableWidgetItem(s.name))
            self.table.setItem(row, 2, QTableWidgetItem(s.host))
            self.table.setItem(row, 3, QTableWidgetItem(str(s.port)))
            self.table.setItem(row, 4, QTableWidgetItem(s.username))
            dev = self.ctx.devices.get(s.id) or s.device
            self.table.setItem(row, 5, QTableWidgetItem(dev.display if dev else "未检测"))
            self.table.setItem(row, 6, QTableWidgetItem(str(dev.count if dev else 0)))
            self.table.setItem(row, 7, QTableWidgetItem(s.remark))
        self.table.itemChanged.connect(self._on_item_changed)

    def _current_server(self) -> Optional[ServerInfo]:
        row = self.table.currentRow()
        if row < 0:
            return None
        servers = self.ctx.registry.list_servers()
        if 0 <= row < len(servers):
            return servers[row]
        return None

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        self._collect_selection()
        self.selection_changed.emit()

    def _collect_selection(self) -> None:
        servers = self.ctx.registry.list_servers()
        selected: List[ServerInfo] = []
        for row in range(self.table.rowCount()):
            it = self.table.item(row, 0)
            if it is not None and it.checkState() == Qt.Checked and row < len(servers):
                selected.append(servers[row])
        self.ctx.selected = selected
        self.ctx.reset_after_servers_changed()

    def ensure_selection(self) -> bool:
        self._collect_selection()
        if not self.ctx.selected:
            QMessageBox.warning(self, "未选择服务器", "请先勾选至少一台要部署的服务器")
            return False
        return True

    # ------------------------------------------------------------ 操作
    def _add(self) -> None:
        dlg = ServerEditDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self.ctx.registry.upsert(dlg.build_server_info())
            self.refresh()

    def _edit(self) -> None:
        server = self._current_server()
        if server is None:
            QMessageBox.information(self, "提示", "请先选中一行")
            return
        dlg = ServerEditDialog(self, server)
        if dlg.exec() == QDialog.Accepted:
            self.ctx.registry.upsert(dlg.build_server_info(server))
            self.refresh()

    def _delete(self) -> None:
        server = self._current_server()
        if server is None:
            QMessageBox.information(self, "提示", "请先选中一行")
            return
        if QMessageBox.question(self, "确认删除",
                                f"删除服务器 {server.name} ({server.endpoint}) 的登录信息？"
                                ) == QMessageBox.Yes:
            self.ctx.registry.remove(server.id)
            self.ctx.selected = [s for s in self.ctx.selected if s.id != server.id]
            self.ctx.devices.pop(server.id, None)
            self.ctx.reset_after_servers_changed()
            self.refresh()
            self.selection_changed.emit()

    def _test_connections(self) -> None:
        if not self.ensure_selection():
            return

        def fn(ssh, tctx):
            tctx.progress(50, "SSH 连接成功")
            tctx.log(f"已连接 {ssh.server.endpoint} (用户 {ssh.server.username})")
            tctx.progress(100, "连接测试完成")

        self.panel.run_tasks([(s, fn) for s in self.ctx.selected], "测试连接")

    def _detect_devices(self) -> None:
        if not self.ensure_selection():
            return
        self._detected = {}

        def make_fn(server):
            def fn(ssh, tctx):
                tctx.progress(30, "探测设备...")
                dev = DeviceDetector(ssh).detect()
                tctx.log(f"探测结果: {dev.display} ({dev.device_type.value})")
                if dev.device_type.value == "unknown" or dev.count <= 0:
                    raise RuntimeError("未探测到加速设备(NPU/GPU)")
                self._detected[server.id] = dev
                tctx.progress(100, dev.display)
            return fn

        def on_finished(all_ok: bool) -> None:
            if not all_ok or not self._detected:
                QMessageBox.warning(self, "设备探测", "部分服务器设备探测失败，详见日志")
                return
            self.ctx.devices.update(self._detected)
            for sid, dev in self._detected.items():
                self.ctx.registry.touch(sid, device=dev)
            infos = {s.name: (self._detected.get(s.id) or s.device or DeviceInfo())
                     for s in self.ctx.selected}
            result = DeviceDetector.verify_consistency(infos)
            icon = QMessageBox.Information if result.ok else QMessageBox.Warning
            box = QMessageBox(icon, "设备一致性校验",
                              result.message + ("\n\n" if result.warnings else "")
                              + "\n".join(result.warnings))
            box.exec()
            self.refresh()
            self.selection_changed.emit()

        self.panel.run_tasks([(s, make_fn(s)) for s in self.ctx.selected],
                             "探测设备", on_finished=on_finished)
