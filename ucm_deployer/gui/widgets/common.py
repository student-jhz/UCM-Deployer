# -*- coding: utf-8 -*-
"""GUI 通用组件：多服务器并行任务面板、后台单任务、远端目录选择、日志查看。"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
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
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.models import DockerImage, ServerInfo
from ...core.ssh_client import SSHClient
from ...utils.log import get_logger

logger = get_logger(__name__)


# ============================================================ 镜像下拉
def fill_image_combo(combo: QComboBox, images: List[DockerImage],
                     current: str = "") -> None:
    """填充镜像下拉框：只可选择（不可手输），显示镜像大小，userData 存 ref。

    - UCM 镜像优先排序；悬浮提示显示创建时间
    - 优先恢复 current（上次选择/已记录值），否则选第一项
    - 无镜像时显示占位提示（data 为空串，视作未选择）
    """
    combo.blockSignals(True)
    prev = current or combo_ref(combo)
    combo.clear()
    if not images:
        combo.addItem("（尚未加载镜像，请点击「刷新镜像列表」）")
        combo.setItemData(0, "", Qt.UserRole)
        combo.blockSignals(False)
        return
    ordered = sorted(images, key=lambda im: "ucm" not in im.ref.lower())
    for im in ordered:
        label = f"{im.ref}    {im.size}".rstrip()
        combo.addItem(label)
        idx = combo.count() - 1
        combo.setItemData(idx, im.ref, Qt.UserRole)
        if im.created:
            combo.setItemData(idx, f"创建时间: {im.created}", Qt.ToolTipRole)
    for i in range(combo.count()):
        if str(combo.itemData(i, Qt.UserRole) or "") == prev and prev:
            combo.setCurrentIndex(i)
            break
    else:
        combo.setCurrentIndex(0)
    combo.blockSignals(False)


def combo_ref(combo: QComboBox) -> str:
    """取镜像下拉当前选择的镜像引用（userData），未选择返回空串。"""
    if combo.count() <= 0:
        return ""
    return str(combo.currentData(Qt.UserRole) or "")


def image_from_ref(ref: str) -> DockerImage:
    """由 ref 构造占位 DockerImage（用于预填/新加载的镜像，无大小信息）。"""
    return DockerImage(repository=ref, tag="", image_id=ref)


def setup_image_combo(combo: QComboBox, max_visible: int = 10) -> None:
    """配置镜像下拉框：弹层最多显示 max_visible 条（超出滚动），输入关键字即可筛选。

    仍只能选中列表中的镜像（不可自由输入）：
    NoInsert 禁止新增项；结束编辑（回车/失焦）时文本未匹配任何列表项则回退当前选择。
    """
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.NoInsert)
    combo.setMaxVisibleItems(max_visible)
    edit = combo.lineEdit()
    edit.setPlaceholderText("输入关键字筛选镜像")

    completer = QCompleter(combo.model(), combo)
    completer.setCompletionMode(QCompleter.PopupCompletion)
    completer.setCaseSensitivity(Qt.CaseInsensitive)
    completer.setFilterMode(Qt.MatchContains)
    completer.setMaxVisibleItems(max_visible)
    completer.popup().setObjectName("imageFilterPopup")
    combo.setCompleter(completer)

    def commit() -> None:
        text = edit.text().strip()
        idx = combo.findText(text, Qt.MatchContains) if text else -1
        if idx >= 0:
            combo.setCurrentIndex(idx)
        elif combo.count() > 0:
            combo.setCurrentIndex(max(combo.currentIndex(), 0))
            edit.setText(combo.currentText())

    def pick(text: str) -> None:
        idx = combo.findText(text)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    completer.activated.connect(pick)
    edit.editingFinished.connect(commit)


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
    """多服务器并行执行：每服务器一行进度条 + 一页日志。

    用法（推荐）：
        panel.run_tasks(tasks, "标题", on_finished=lambda all_ok: ...)
    on_finished 由面板内部一次性连接，任务被拒（忙碌）时不会残留。
    """

    finished_all = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._threads: List[ServerTaskThread] = []
        self._results: Dict[str, Tuple[bool, str]] = {}
        self._total = 0
        self._logs: Dict[str, QPlainTextEdit] = {}
        self._bars: Dict[str, QProgressBar] = {}

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["服务器", "进度", "状态"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self._cancel)
        self.title_label = QLabel("")
        self.title_label.setStyleSheet("color:#64748b;")
        top = QHBoxLayout()
        top.addWidget(self.title_label)
        top.addStretch(1)
        top.addWidget(self.cancel_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table)
        layout.addWidget(self.tabs, 1)

    # ------------------------------------------------------------ API
    def is_running(self) -> bool:
        return self._total > len(self._results)

    def start(self, tasks: List[Tuple[ServerInfo, Callable]],
              title: str = "执行任务") -> bool:
        """启动并行任务；忙碌时提示并返回 False。"""
        if self.is_running():
            QMessageBox.warning(self, "忙碌", "有任务正在执行，请等待完成或取消")
            return False
        # 清理上一轮已结束的线程（此时均已结束，可安全释放）
        for t in self._threads:
            try:
                t.deleteLater()
            except RuntimeError:
                pass
        self._threads = []
        self._results = {}
        self._total = len(tasks)
        self._logs = {}
        self._bars = {}
        self.title_label.setText(title if title else "")
        self.table.setRowCount(0)
        while self.tabs.count():
            self.tabs.removeTab(0)

        for server, fn in tasks:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(f"{server.name} ({server.host})"))
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setFormat("等待中")
            self.table.setCellWidget(row, 1, bar)
            self._bars[server.id] = bar
            status = QTableWidgetItem("排队中")
            status.setForeground(Qt.gray)
            self.table.setItem(row, 2, status)
            edit = QPlainTextEdit()
            edit.setReadOnly(True)
            edit.setMaximumBlockCount(5000)
            edit.setProperty("role", "code")
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

        self.cancel_btn.setEnabled(bool(tasks))
        for t in self._threads:
            t.start()
        return True

    def run_tasks(self, tasks: List[Tuple[ServerInfo, Callable]],
                  title: str = "执行任务",
                  on_finished: Optional[Callable[[bool], None]] = None) -> bool:
        """start() + 一次性 on_finished 回调（面板内部管理连接，无残留）。"""
        if self.is_running():
            QMessageBox.warning(self, "忙碌", "有任务正在执行，请等待完成或取消")
            return False

        def handler(all_ok: bool) -> None:
            try:
                if on_finished is not None:
                    on_finished(all_ok)
            finally:
                try:
                    self.finished_all.disconnect(handler)
                except (RuntimeError, TypeError):
                    pass

        if on_finished is not None:
            self.finished_all.connect(handler)
        return self.start(tasks, title)

    def shutdown(self, timeout_ms: int = 2000) -> None:
        """请求取消并等待线程结束（关闭窗口时使用，避免 QThread 析构崩溃）。"""
        for t in self._threads:
            t.request_cancel()
        for t in self._threads:
            if not t.wait(timeout_ms):
                t.terminate()
                t.wait(1000)

    # ------------------------------------------------------------ 内部
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
        bar = self._bars.get(server_id)
        if bar is not None:
            bar.setValue(max(0, min(100, int(pct))))
            bar.setFormat(f"{pct}%  {msg}" if msg else f"{pct}%")

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
        bar = self._bars.get(server_id)
        if bar is not None:
            if ok:
                # 任务正常结束但未上报 100% 时自动补满，避免进度条停滞
                if bar.value() < 100:
                    bar.setValue(100)
                    bar.setFormat("100%  完成")
            else:
                bar.setFormat("失败")
        if row >= 0:
            item = self.table.item(row, 2)
            if item is None:
                item = QTableWidgetItem()
                self.table.setItem(row, 2, item)
            item.setText(("✓ " if ok else "✗ ") + message)
            item.setForeground(Qt.darkGreen if ok else Qt.red)
        if self._total and len(self._results) >= self._total:
            self.cancel_btn.setEnabled(False)
            all_ok = all(ok for ok, _ in self._results.values())
            self.finished_all.emit(all_ok)


# ============================================================ 远端目录选择
class RemoteDirDialog(QDialog):
    """浏览远端服务器目录。

    listers: {服务器显示名: lister(path)->[子目录列表]}，多台服务器时提供切换下拉。
    """

    def __init__(self, parent, listers: Dict[str, Callable[[str], List[str]]],
                 start: str = "/", title: str = "选择服务器目录"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(540, 440)
        self._listers = listers
        self._selected: Optional[str] = None

        self.path_edit = QLineEdit(start)
        up_btn = QPushButton("↑ 上级")
        refresh_btn = QPushButton("刷新")
        up_btn.clicked.connect(self._up)
        refresh_btn.clicked.connect(self._load)

        self.server_combo: Optional[QComboBox] = None
        if len(listers) > 1:
            self.server_combo = QComboBox()
            self.server_combo.addItems(list(listers.keys()))
            self.server_combo.currentTextChanged.connect(lambda _: self._load())

        self.listw = QListWidget()
        self.listw.itemDoubleClicked.connect(self._enter)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        if self.server_combo is not None:
            top.addWidget(QLabel("服务器"))
            top.addWidget(self.server_combo)
        top.addWidget(self.path_edit, 1)
        top.addWidget(up_btn)
        top.addWidget(refresh_btn)
        layout.addLayout(top)
        layout.addWidget(self.listw, 1)
        layout.addWidget(QLabel("双击进入子目录；「确定」使用当前路径"))
        layout.addWidget(self.buttons)
        self._load()

    def _current_lister(self) -> Callable[[str], List[str]]:
        if self.server_combo is not None:
            return self._listers[self.server_combo.currentText()]
        return next(iter(self._listers.values()))

    def _load(self) -> None:
        path = self.path_edit.text().strip() or "/"
        self.listw.clear()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            dirs = self._current_lister()(path)
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
        if path.startswith("/") and "/" in path[1:]:
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
        self.resize(780, 540)
        self._loader = loader
        self._lines = lines

        self.edit = QPlainTextEdit()
        self.edit.setReadOnly(True)
        self.edit.setProperty("role", "code")
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
