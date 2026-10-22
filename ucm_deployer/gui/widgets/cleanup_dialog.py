# -*- coding: utf-8 -*-
"""清理对话框：管理当前登录服务器上的容器/镜像（列表勾选 + 删除）。

由主窗口「工具」菜单打开，不属于三步部署流程。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
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

from ...core.docker_manager import DockerManager
from ...core.ssh_client import SSHClient
from ..state import AppContext
from .common import QuickTask

_MODE_CONTAINER = "container"
_MODE_IMAGE = "image"


class CleanupDialog(QDialog):
    """清理指定容器或镜像（作用于当前勾选登录的服务器）。"""

    def __init__(self, ctx: AppContext, mode: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.ctx = ctx
        self.mode = mode
        self._quick_tasks: List[QuickTask] = []
        self._rows: Dict[int, dict] = {}     # 行号 -> 原始记录（names/ref）

        if mode == _MODE_CONTAINER:
            self.setWindowTitle("清理容器")
            self._info = ("列出服务器上全部容器（含已停止）；勾选后点「删除选中」。\n"
                          "注意：运行中的容器将被强制停止并删除。")
            headers = ["删除", "容器名", "镜像", "状态", "ID"]
        else:
            self.setWindowTitle("清理镜像")
            self._info = ("列出服务器上全部镜像；勾选后点「删除选中」。\n"
                          "注意：被运行中容器引用的镜像会被强制删除（docker rmi -f）。")
            headers = ["删除", "镜像", "ID", "大小", "创建时间"]

        self.tip_label = QLabel(f"服务器：{self._server_desc()}")
        self.tip_label.setProperty("role", "hint")
        self.info_label = QLabel(self._info)
        self.info_label.setProperty("role", "hint")
        self.info_label.setWordWrap(True)

        # 筛选行：关键字过滤（整行所有列匹配，保留已勾选状态）
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("输入关键字过滤列表（匹配名称/ID/状态等，不区分大小写）")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._apply_filter)
        self.filter_count_label = QLabel("")
        self.filter_count_label.setProperty("role", "hint")

        self.table = QTableWidget(0, len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        header = self.table.horizontalHeader()
        # 全列按内容自适应宽度：长镜像名完整可见，窗口不够宽时出横向滚动条
        for col in range(len(headers)):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumSize(860, 420)

        self.refresh_btn = QPushButton("🔄 刷新列表")
        self.del_btn = QPushButton("🗑 删除选中")
        self.del_btn.setProperty("accent", True)
        self.close_btn = QPushButton("关闭")
        self.refresh_btn.clicked.connect(self._load)
        self.del_btn.clicked.connect(self._delete_selected)
        self.close_btn.clicked.connect(self.reject)

        btns = QHBoxLayout()
        btns.addWidget(self.refresh_btn)
        btns.addWidget(self.del_btn)
        btns.addStretch(1)
        btns.addWidget(self.close_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tip_label)
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("筛选"), 0)
        filter_row.addWidget(self.filter_edit, 1)
        filter_row.addWidget(self.filter_count_label, 0)
        layout.addLayout(filter_row)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.info_label)
        layout.addLayout(btns)

        self._load()

    # ------------------------------------------------------------ 辅助
    def _server_desc(self) -> str:
        server = self.ctx.server
        return f"{server.name} ({server.host})" if server else "未登录"

    def _run_quick(self, fn, on_done) -> None:
        task = QuickTask(fn, self)
        task.signals.sig_done.connect(on_done)

        def _cleanup(*_):
            if task in self._quick_tasks:
                self._quick_tasks.remove(task)

        task.signals.sig_done.connect(_cleanup)
        self._quick_tasks.append(task)
        task.start()

    # ------------------------------------------------------------ 加载
    def _load(self) -> None:
        server = self.ctx.server
        if server is None:
            QMessageBox.information(self, "提示", "请先在步骤1勾选要登录的服务器")
            return
        self.table.setRowCount(0)
        self._rows.clear()
        self.refresh_btn.setEnabled(False)
        self.tip_label.setText(f"服务器：{self._server_desc()}　正在加载列表...")

        def fn():
            with SSHClient(server) as ssh:
                dm = DockerManager(ssh)
                if self.mode == _MODE_CONTAINER:
                    return dm.list_containers(all_states=True)
                return dm.list_images()

        def on_done(items, error):
            self.refresh_btn.setEnabled(True)
            self.tip_label.setText(f"服务器：{self._server_desc()}")
            if error is not None:
                QMessageBox.warning(self, "加载失败", str(error))
                return
            self._fill(items or [])

        self._run_quick(fn, on_done)

    def _fill(self, items: list) -> None:
        for item in items:
            row = self.table.rowCount()
            self.table.insertRow(row)
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            check.setCheckState(Qt.Unchecked)
            self.table.setItem(row, 0, check)
            if self.mode == _MODE_CONTAINER:
                self._set_text_item(row, 1, str(item.get("names", "")))
                self._set_text_item(row, 2, str(item.get("image", "")))
                self._set_text_item(row, 3, str(item.get("status", "")))
                self._set_text_item(row, 4, str(item.get("id", "")))
                self._rows[row] = item
            else:
                ref = item.ref if hasattr(item, "ref") else str(item)
                size = item.size if hasattr(item, "size") else ""
                image_id = item.image_id if hasattr(item, "image_id") else ""
                created = item.created if hasattr(item, "created") else ""
                self._set_text_item(row, 1, ref)
                self._set_text_item(row, 2, image_id)
                self._set_text_item(row, 3, size)
                self._set_text_item(row, 4, created)
                self._rows[row] = {"ref": ref}
        self.table.resizeColumnsToContents()
        self._apply_filter()

    def _set_text_item(self, row: int, col: int, text: str) -> None:
        """填单元格并悬浮显示全文（长镜像名被截断时 tooltip 可见全名）。"""
        item = QTableWidgetItem(text)
        if text:
            item.setToolTip(text)
        self.table.setItem(row, col, item)

    # ------------------------------------------------------------ 筛选
    def _apply_filter(self, *_) -> None:
        """按关键字过滤行（整行所有列不区分大小写包含匹配，保留勾选）。"""
        keyword = self.filter_edit.text().strip().lower()
        total = self.table.rowCount()
        visible = 0
        for row in range(total):
            if not keyword:
                self.table.setRowHidden(row, False)
                visible += 1
                continue
            matched = False
            for col in range(1, self.table.columnCount()):
                it = self.table.item(row, col)
                if it is not None and keyword in it.text().lower():
                    matched = True
                    break
            self.table.setRowHidden(row, not matched)
            if matched:
                visible += 1
        if total == 0:
            self.filter_count_label.setText("")
        elif keyword:
            self.filter_count_label.setText(f"当前 {visible}/{total} 条")
        else:
            self.filter_count_label.setText(f"共 {total} 条")

    # ------------------------------------------------------------ 删除
    def _selected_refs(self) -> List[str]:
        refs = []
        for row in range(self.table.rowCount()):
            it = self.table.item(row, 0)
            if it is not None and it.checkState() == Qt.Checked:
                rec = self._rows.get(row)
                if rec:
                    refs.append(str(rec.get("names") or rec.get("ref") or ""))
        return [r for r in refs if r]

    def _delete_selected(self) -> None:
        server = self.ctx.server
        if server is None:
            QMessageBox.information(self, "提示", "请先在步骤1勾选要登录的服务器")
            return
        refs = self._selected_refs()
        if not refs:
            QMessageBox.information(self, "提示", "请先勾选要删除的"
                                    + ("容器" if self.mode == _MODE_CONTAINER else "镜像"))
            return
        kind = "容器" if self.mode == _MODE_CONTAINER else "镜像"
        force_note = ("运行中的容器将被强制停止并删除"
                      if self.mode == _MODE_CONTAINER
                      else "被运行中容器引用的镜像会被强制删除")
        ret = QMessageBox.question(
            self, "确认删除",
            f"确定在服务器 {server.name} 上删除以下 {len(refs)} 个{kind}？\n\n"
            + "\n".join(refs[:10])
            + ("\n..." if len(refs) > 10 else "")
            + f"\n\n注意：{force_note}。")
        if ret != QMessageBox.Yes:
            return

        self.del_btn.setEnabled(False)
        mode = self.mode

        def fn():
            with SSHClient(server) as ssh:
                dm = DockerManager(ssh)
                if mode == _MODE_CONTAINER:
                    return dm.remove_containers(refs)
                return dm.remove_images(refs)

        def on_done(_result, error):
            self.del_btn.setEnabled(True)
            if error is not None:
                QMessageBox.warning(self, "删除失败", str(error))
            else:
                QMessageBox.information(
                    self, "完成", f"已删除 {len(refs)} 个{kind}。")
            self._load()

        self._run_quick(fn, on_done)
