# -*- coding: utf-8 -*-
"""GUI 通用组件：多服务器并行任务面板、后台单任务、远端目录选择、日志查看。"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QApplication,
)

from ...core.models import ServerInfo
from ...core.ssh_client import SSHClient
from ...utils.log import get_logger

logger = get_logger(__name__)


# ============================================================ 任务线程
class TaskContext:
    """任务函数使用的回调上下集（线程安全：通过 Qt 信号投递到 GUI 线程）。"""

    def __init__(self, thread: "ServerTaskThread"):
        self._thread = thread

    def progress(self, pct: int, msg: str = "") -> None:
        self._thread.sig_progress.emit(self._thread.server.id, int(pct), msg)

    def log(self, line: str) -> None:
        self._thread.sig_log.emit(self._thread.server.id, str(line))

    def cancelled(self) -> bool:
        return self._thread.cancel_requested


class ServerTaskThread(QThread):
    sig_progress = Signal(str, int, str)   # server_id, pct, msg
    sig_log = Signal(str, str)             # server_id, line
    sig_done = Signal(str, bool, str)      # server_id, ok, message

    def __init__(self, server: ServerInfo, fn: Callable, parent=None):
        super().__init__(parent)
        self.server = server
        self.fn = fn
        self.cancel_requested = False
        self.ok = False
        self.message = ""

    def request_cancel(self) -> None:
        self.cancel_requested = True

    def run(self) -> None:  # pragma: no cover - 线程体
        ctx = TaskContext(self)
        try:
            with SSHClient(self.server) as ssh:
                self.fn(ssh, ctx)
            self.ok = True
            self.message = "完成"
        except Exception as exc:
            logger.exception("服务器任务失败: %s", self.server.name)
            self.ok = False
            self.message = str(exc)
        self.sig_done.emit(self.server.id, self.ok, self.message)


class QuickTask(QThread):
    """单次后台操作（不显示面板）。done(result, error)"""
    sig_done = Signal(object, object)

    def __init__(self, fn: Callable, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self) -> None:  # pragma: no cover
        try:
            self.sig_done.emit(self.fn(), None)
        except Exception as exc:
            logger.exception("后台任务失败")
            self.sig_done.emit(None, exc)


# ============================================================ 并行任务面板
class ParallelTaskPanel(QWidget):
    """多服务器并行执行：每服务器一行进度 + 一页日志。"""

    finished_all = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._threads: List[ServerTaskThread] = []
        self._results: Dict[str, Tuple[bool, str]] = {}

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["服务器", "进度", "状态"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self._cancel)
        top = QHBoxLayout()
        top.addStretch(1)
        top.addWidget(self.cancel_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table)
        layout.addWidget(self.tabs, 1)

    # ------------------------------------------------------------ API
    def is_running(self) -> bool:
        return bool(self._threads)

    def start(self, tasks: List[Tuple[ServerInfo, Callable]],
              title: str = "执行任务") -> None:
        if self.is_running():
            QMessageBox.warning(self, "忙碌", "有任务正在执行，请等待完成或取消")
            return
        self._results = {}
        self._threads = []
        self.table.setRowCount(0)
        while self.tabs.count():
            self.tabs.removeTab(0)
        self._logs: Dict[str, QPlainTextEdit] = {}

        for server, fn in tasks:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(f"{server.name} ({server.host})"))
            status = QTableWidgetItem("排队中")
            status.setForeground(Qt.gray)
            self.table.setItem(row, 2, status)
            edit = QPlainTextEdit()
            edit.setReadOnly(True)
            edit.setMaximumBlockCount(5000)
            font = QFont("Consolas")
            font.setStyleHint(QFont.Monospace)
            edit.setFont(font)
            self._logs[server.id] = edit
            self.tabs.addTab(edit, server.name)

            thread = ServerTaskThread(server, fn, self)
            thread.sig_progress.connect(self._on_progress)
            thread.sig_log.connect(self._on_log)
            thread.sig_done.connect(self._on_done)
            self._threads.append(thread)

        self.cancel_btn.setEnabled(True)
        for t in self._threads:
            t.start()

    def _cancel(self) -> None:
        for t in self._threads:
            t.request_cancel()
        self.cancel_btn.setEnabled(False)
        self._set_status_all("正在取消...")

    def _row_of(self, server_id: str) -> int:
        for i, t in enumerate(self._threads):
            if t.server.id == server_id:
                return i
        return -1

    def _on_progress(self, server_id: str, pct: int, msg: str) -> None:
        row = self._row_of(server_id)
        if row >= 0:
            self.table.setItem(row, 1, QTableWidgetItem(f"{pct}%  {msg}"))

    def _on_log(self, server_id: str, line: str) -> None:
        edit = self._logs.get(server_id)
        if edit is not None:
            edit.appendPlainText(line)

    def _set_status_all(self, text: str) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 2)
            if item is not None:
                item.setText(text)

    def _on_done(self, server_id: str, ok: bool, message: str) -> None:
        self._results[server_id] = (ok, message)
        row = self._row_of(server_id)
        if row >= 0:
            item = self.table.item(row, 2)
            if item is None:
                item = QTableWidgetItem()
                self.table.setItem(row, 2, item)
            item.setText(("✓ " if ok else "✗ ") + message)
            item.setForeground(Qt.darkGreen if ok else Qt.red)
        if len(self._results) == len(self._threads):
            self.cancel_btn.setEnabled(False)
            all_ok = all(ok for ok, _ in self._results.values())
            self.finished_all.emit(all_ok)
            self._threads = []


# ============================================================ 远端目录选择
class RemoteDirDialog(QDialog):
    """浏览远端服务器目录（下拉框数据源）。lister(path)->[dirs]"""

    def __init__(self, parent, lister: Callable[[str], List[str]],
                 start: str = "/", title: str = "选择服务器目录"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(520, 420)
        self._lister = lister
        self._selected: Optional[str] = None

        self.path_edit = QLineEdit(start)
        up_btn = QPushButton("↑ 上级")
        refresh_btn = QPushButton("刷新")
        up_btn.clicked.connect(self._up)
        refresh_btn.clicked.connect(self._load)

        self.listw = QListWidget()
        self.listw.itemDoubleClicked.connect(self._enter)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(self.path_edit, 1)
        row.addWidget(up_btn)
        row.addWidget(refresh_btn)
        layout.addLayout(row)
        layout.addWidget(self.listw, 1)
        layout.addWidget(QLabel("双击进入子目录；「确定」使用当前路径"))
        layout.addWidget(self.buttons)
        self._load()

    def _load(self) -> None:
        path = self.path_edit.text().strip() or "/"
        self.listw.clear()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            dirs = self._lister(path)
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "读取失败", f"无法列出 {path}:\n{exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()
        for d in dirs:
            self.listw.addItem(QListWidgetItem("📁 " + d))

    def _enter(self, item: QListWidgetItem) -> None:
        text = item.text()
        if text.startswith("📁 "):
            self.path_edit.setText(text[2:])
            self._load()

    def _up(self) -> None:
        path = self.path_edit.text().strip().rstrip("/")
        if "/" in path[1:]:
            self.path_edit.setText(path.rsplit("/", 1)[0] or "/")
        else:
            self.path_edit.setText("/")
        self._load()

    def _accept(self) -> None:
        self._selected = self.path_edit.text().strip() or "/"
        self.accept()

    def selected_path(self) -> Optional[str]:
        return self._selected


# ============================================================ 日志查看
class LogViewDialog(QDialog):
    """查看容器内日志（tail）。loader(n)->str"""

    def __init__(self, parent, loader: Callable[[int], str], title: str,
                 lines: int = 300):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 520)
        self._loader = loader
        self._lines = lines

        self.edit = QPlainTextEdit()
        self.edit.setReadOnly(True)
        font = QFont("Consolas")
        font.setStyleHint(QFont.Monospace)
        self.edit.setFont(font)

        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.refresh)
        auto = QCheckBox("自动刷新(5s)")
        auto.toggled.connect(self._toggle_auto)

        from PySide6.QtCore import QTimer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)

        top = QHBoxLayout()
        top.addWidget(refresh_btn)
        top.addWidget(auto)
        top.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.edit, 1)
        self.refresh()

    def refresh(self) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            text = self._loader(self._lines)
        except Exception as exc:
            text = f"[读取失败] {exc}"
        finally:
            QApplication.restoreOverrideCursor()
        self.edit.setPlainText(text)
        cursor = self.edit.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.edit.setTextCursor(cursor)

    def _toggle_auto(self, on: bool) -> None:
        if on:
            self._timer.start(5000)
        else:
            self._timer.stop()
