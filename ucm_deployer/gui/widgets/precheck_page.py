# -*- coding: utf-8 -*-
"""步骤4：环境预检 —— 在已创建的容器内运行 ucm-toolkit precheck。

- 纯环境检查或带挂载目录带宽测试（在挂载目录下创建临时子目录执行，
  结束后自动清理，不污染用户目录）
- 结果以表格逐项展示（PASS/WARN/FAIL/INFO 着色），原始输出见日志页签
"""
from __future__ import annotations

from typing import List

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ...core.precheck import PrecheckRunner
from ...core.ssh_client import SSHClient
from ..state import AppContext
from .common import ParallelTaskPanel, RemoteDirDialog

_STATUS_COLORS = {
    "PASS": QColor("#16a34a"),
    "WARN": QColor("#d97706"),
    "FAIL": QColor("#dc2626"),
    "INFO": QColor("#64748b"),
    "SKIP": QColor("#94a3b8"),
}


class PrecheckPage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._result_items = []
        self._result_summary = ""

        # ---------- 预检参数
        cfg_box = QGroupBox("1. 预检参数")
        self.container_label = QLabel("目标容器：未创建（请先在步骤3创建容器）")
        self.container_label.setProperty("role", "hint")

        self.bandwidth_check = QCheckBox("执行存储带宽检查（默认开启）")
        self.bandwidth_check.setChecked(True)
        self.bandwidth_check.setToolTip(
            "开启：在指定挂载目录下创建临时子目录 ucm-precheck-* 执行读写负载，\n"
            "预检结束后自动清理（运行日志会播报该临时目录路径，便于核对）；\n"
            "关闭：仅环境检查（驱动/内核/共享内存/AIO），无需填挂载目录。")
        self.bandwidth_check.toggled.connect(self._on_bandwidth_toggled)

        self.mount_edit = QLineEdit()
        self.mount_edit.setPlaceholderText("容器内挂载目录（带宽测试在其下创建临时子目录）")
        self.mount_edit.setToolTip(
            "KV Cache 挂载目录（容器内路径，须为步骤3 已映射进容器的目录）。\n"
            "「浏览」列出容器内可见的目录；带宽检查将在该目录下创建临时子目录\n"
            "ucm-precheck-* 执行读写负载，预检结束后自动清理；请确保目录有可用空间。")
        mount_btn = QPushButton("浏览...")
        mount_btn.clicked.connect(self._browse_mount)
        self.mount_hint = QLabel(
            "带宽检查：在挂载目录下创建临时子目录（运行日志播报路径），结束后自动清理；"
            "关闭带宽检查则仅检查环境（驱动/内核/共享内存/AIO）。")
        self.mount_hint.setProperty("role", "hint")
        self.mount_hint.setWordWrap(True)

        run_btn = QPushButton("🔍 开始预检")
        run_btn.setProperty("accent", True)
        run_btn.clicked.connect(self._run)

        cfg_layout = QVBoxLayout(cfg_box)
        cfg_layout.setSpacing(4)
        cfg_layout.addWidget(self.container_label)
        cfg_layout.addWidget(self.bandwidth_check)
        mount_row = QHBoxLayout()
        mount_row.addWidget(QLabel("挂载目录"), 0)
        mount_row.addWidget(self.mount_edit, 1)
        mount_row.addWidget(mount_btn)
        mount_row.addWidget(run_btn)
        cfg_layout.addLayout(mount_row)
        cfg_layout.addWidget(self.mount_hint)

        # ---------- 结果
        result_box = QGroupBox("2. 预检结果")
        self.summary_label = QLabel("尚未预检")
        self.summary_label.setProperty("role", "hint")
        self.result_table = QTableWidget(0, 3)
        self.result_table.setHorizontalHeaderLabels(["状态", "检查项", "摘要"])
        header = self.result_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.result_table.verticalHeader().setVisible(False)
        self.result_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.result_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.result_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.result_table.setAlternatingRowColors(True)
        self.result_table.itemSelectionChanged.connect(self._show_selected_detail)

        # 详情区：选中行后显示该检查项完整详情与排查建议（表格只放摘要）
        self.detail_view = QTextBrowser()
        self.detail_view.setOpenExternalLinks(False)
        self.detail_view.setMinimumHeight(96)
        self.detail_view.setMaximumHeight(160)
        self.detail_view.setPlaceholderText(
            "点击上方结果行，此处显示该检查项的完整详情与排查建议")

        result_layout = QVBoxLayout(result_box)
        result_layout.setSpacing(4)
        layout_row = QHBoxLayout()
        layout_row.addWidget(self.result_table, 3)
        layout_col = QVBoxLayout()
        layout_col.addWidget(self.detail_view, 1)
        layout_row.addLayout(layout_col, 2)
        result_layout.addLayout(layout_row)
        result_layout.addWidget(self.summary_label)

        self.panel = ParallelTaskPanel()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(cfg_box)
        layout.addWidget(result_box, 1)
        layout.addWidget(self.panel, 2)

    # ------------------------------------------------------------ 辅助
    def on_enter(self) -> None:
        self._refresh_container_label()
        self._on_bandwidth_toggled()

    def _on_bandwidth_toggled(self, *_) -> None:
        """带宽检查开关：关闭时挂载目录不可用（免填）。"""
        on = self.bandwidth_check.isChecked()
        self.mount_edit.setEnabled(on)
        if not on:
            self.mount_edit.clear()

    def _refresh_container_label(self) -> None:
        server = self.ctx.server
        name = self.ctx.containers.get(server.id) if server else None
        if name:
            self.container_label.setText(
                f"目标容器：{name}（在步骤3创建；预检将在该容器内执行）")
        elif server:
            self.container_label.setText("目标容器：未创建（请先在步骤3创建容器）")
        else:
            self.container_label.setText("未登录服务器（请先在步骤1勾选）")

    def _browse_mount(self) -> None:
        server = self.ctx.server
        if server is None:
            QMessageBox.information(self, "提示", "请先在步骤1勾选要登录的服务器")
            return
        container = self.ctx.containers.get(server.id)
        if not container:
            QMessageBox.information(
                self, "提示", "请先在步骤3创建容器，再选择容器内挂载目录。")
            return

        # 带宽测试在容器内执行：浏览的必须是容器内可见的目录
        def lister(path: str) -> List[str]:
            with SSHClient(server) as ssh:
                return PrecheckRunner(ssh).list_dirs(container, path)

        start = self.mount_edit.text().strip() or "/"
        dlg = RemoteDirDialog(self, lister, start=start,
                              title="选择挂载目录（容器内）")
        if dlg.exec() == QDialog.Accepted and dlg.selected_path():
            self.mount_edit.setText(dlg.selected_path())

    # ------------------------------------------------------------ 执行
    def _run(self) -> None:
        server = self.ctx.server
        if server is None:
            QMessageBox.information(self, "提示", "请先在步骤1勾选要登录的服务器")
            return
        container = self.ctx.containers.get(server.id)
        if not container:
            QMessageBox.information(
                self, "提示", "请先在步骤3创建容器，再执行环境预检。")
            return
        bandwidth = self.bandwidth_check.isChecked()
        mount_path = self.mount_edit.text().strip() if bandwidth else ""
        if bandwidth and not mount_path:
            QMessageBox.warning(
                self, "参数错误",
                "已开启带宽检查，请填写挂载目录（或关闭带宽检查仅做环境检查）。")
            return
        if mount_path and not mount_path.startswith("/"):
            QMessageBox.warning(self, "参数错误", "挂载目录必须是以 / 开头的绝对路径")
            return

        mode = ("带宽检查（临时子目录自动清理，路径见日志）" if bandwidth
                else "仅环境检查（--skip-bandwidth）")
        self.summary_label.setText(f"预检运行中...（{mode}；过程日志见底部页签）")
        self.result_table.setRowCount(0)
        holder = {}

        def fn(ssh, tctx):
            tctx.progress(10, "在容器内运行 ucm-toolkit run precheck ...")
            runner = PrecheckRunner(ssh)
            result = runner.run(
                container, mount_path=mount_path,
                skip_bandwidth=not bandwidth,
                on_line=lambda line: tctx.log(line))
            for item in result.items:
                detail = (item.summary + ("\n" + item.detail if item.detail else "")
                          ).strip()
                tctx.log(f"[{item.status}] {item.name}  {detail}")
            if result.summary:
                tctx.log(result.summary)
            tctx.progress(100, result.summary or "预检完成")
            holder["result"] = result

        def on_finished(all_ok: bool) -> None:
            result = holder.get("result")
            if result is not None:
                self._show_result(result)
            if not all_ok:
                self.summary_label.setText("预检执行失败，详见日志页签")

        self.panel.run_tasks([(server, fn)], f"环境预检（容器 {container}）",
                             on_finished=on_finished)

    # ------------------------------------------------------------ 结果展示
    def _show_result(self, result) -> None:
        self._result_items = list(result.items)
        self._result_summary = result.summary
        self.result_table.setRowCount(0)
        for item in result.items:
            row = self.result_table.rowCount()
            self.result_table.insertRow(row)
            status_item = QTableWidgetItem(item.status)
            color = _STATUS_COLORS.get(item.status)
            if color is not None:
                status_item.setForeground(color)
            self.result_table.setItem(row, 0, status_item)
            # 检查项：中文名（英文名）
            name_text = (f"{item.name_zh}（{item.name}）"
                         if item.name_zh and item.name_zh != item.name
                         else item.name)
            self.result_table.setItem(row, 1, QTableWidgetItem(name_text))
            # 摘要列只放首行摘要（完整详情/排查建议见右侧详情区）
            self.result_table.setItem(row, 2, QTableWidgetItem(item.summary or "-"))
        self.result_table.resizeColumnsToContents()
        self.result_table.resizeRowsToContents()
        # 默认选中首个 WARN/FAIL 项（无则第一行），立即联动详情区
        select_row = 0
        for i, item in enumerate(result.items):
            if item.status in ("FAIL", "WARN"):
                select_row = i
                break
        self.result_table.setCurrentCell(select_row, 0)
        self._update_summary_label(result)

    def _show_selected_detail(self) -> None:
        """右侧详情区：显示选中检查项的完整详情与中文排查建议。"""
        import html as _html

        items = getattr(self, "_result_items", [])
        row = self.result_table.currentRow()
        if row < 0 or row >= len(items):
            return
        item = items[row]
        parts = [f"<b>{_html.escape(item.name_zh or item.name)}"
                 f"（{_html.escape(item.name)}）</b>　[{item.status}]"]
        if item.summary:
            parts.append(_html.escape(item.summary))
        if item.detail:
            parts.append(_html.escape(item.detail).replace("\n", "<br>"))
        if item.advice:
            parts.append(f"<b>排查建议：</b>{_html.escape(item.advice)}")
        self.detail_view.setHtml("<br>".join(parts))

    def _update_summary_label(self, result) -> None:
        tail = f"　{result.summary}" if result.summary else ""
        if result.verdict == "passed":
            self.summary_label.setText(f"✓ 预检通过（无警告）{tail}")
            QMessageBox.information(self, "预检完成", f"环境预检全部通过。\n{result.summary}")
        elif result.verdict == "completed":
            self.summary_label.setText(
                f"⚠ 预检完成（存在 {result.warn_count} 项警告，非全部通过）{tail}")
            QMessageBox.warning(
                self, "预检完成（有警告）",
                f"预检已运行完毕，存在 {result.warn_count} 项 WARN"
                "（未达 FAIL，可继续部署但建议排查）。\n{result.summary}")
        else:
            self.summary_label.setText(f"✗ 预检未通过{tail}")
            QMessageBox.warning(
                self, "预检未通过",
                f"环境预检存在 FAIL 项（或异常退出），请查看结果表格与日志。\n"
                f"{result.summary}")
