# -*- coding: utf-8 -*-
"""预检参数配置对话框：查看/编辑容器内的 precheck JSON 配置。

- 「从容器读取」：定位并读取随 ucm-toolkit 安装的 precheck.defaults.json
  （<site-packages>/ucm_toolkit/tools/precheck/），或已保存的自定义配置
- 编辑内容实时做 JSON 语法校验；保存为容器内 /root/ucm-deploy/precheck.json，
  预检命令以 --config 引用（叠加于出厂默认之上，只需写要改的键）
"""
from __future__ import annotations

import json
from typing import Optional

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.precheck import CONFIG_CONTAINER_PATH, PrecheckRunner
from ...core.ssh_client import SSHClient
from ..state import AppContext
from .common import QuickTask


class PrecheckConfigDialog(QDialog):
    """编辑并保存容器内预检配置；结果通过 saved(bool) 通知页面刷新状态。"""

    def __init__(self, ctx: AppContext, container: str,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.ctx = ctx
        self.container = container
        self._quick_tasks = []

        self.setWindowTitle("预检参数配置")
        self.resize(640, 520)

        intro = QLabel(
            "配置为 JSON，保存到容器内 " + CONFIG_CONTAINER_PATH +
            "，预检时以 --config 引用，\n"
            "叠加于 ucm-toolkit 出厂默认之上（只需写要覆盖的键）。\n"
            "出厂默认文件随包安装于 ucm_toolkit/tools/precheck/precheck.defaults.json，"
            "可点下方按钮读取后修改。")
        intro.setProperty("role", "hint")
        intro.setWordWrap(True)

        self.load_btn = QPushButton("📦 从容器读取配置")
        self.load_btn.setToolTip(
            "优先读取容器内已保存的自定义配置；不存在则读取随 ucm-toolkit\n"
            "安装的出厂默认 precheck.defaults.json")
        self.load_btn.clicked.connect(self._load)

        self.editor = QPlainTextEdit()
        self.editor.setProperty("role", "code")
        font = QFont("Consolas")
        font.setStyleHint(QFont.Monospace)
        self.editor.setFont(font)
        self.editor.setPlaceholderText(
            "点击「从容器读取配置」加载，或直接粘贴 JSON 配置")
        self.editor.textChanged.connect(self._validate)

        self.validate_label = QLabel("")
        self.validate_label.setProperty("role", "hint")
        self.validate_label.setWordWrap(True)

        self.save_btn = QPushButton("💾 保存并启用")
        self.save_btn.setProperty("accent", True)
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._save)
        self.reset_btn = QPushButton("↩ 恢复默认配置")
        self.reset_btn.setToolTip("删除容器内的自定义配置，预检回到出厂默认")
        self.reset_btn.clicked.connect(self._reset)
        self.close_btn = QPushButton("取消")
        self.close_btn.clicked.connect(self.reject)

        btns = QHBoxLayout()
        btns.addWidget(self.save_btn)
        btns.addWidget(self.reset_btn)
        btns.addStretch(1)
        btns.addWidget(self.close_btn)

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(self.load_btn)
        top.addStretch(1)
        layout.addLayout(top)
        layout.addWidget(intro)
        layout.addWidget(self.editor, 1)
        layout.addWidget(self.validate_label)
        layout.addLayout(btns)

        self._load()      # 打开即自动加载

    # ------------------------------------------------------------ 后台任务
    def _run_quick(self, fn, on_done) -> None:
        task = QuickTask(fn, self)
        task.signals.sig_done.connect(on_done)

        def _cleanup(*_):
            if task in self._quick_tasks:
                self._quick_tasks.remove(task)

        task.signals.sig_done.connect(_cleanup)
        self._quick_tasks.append(task)
        task.start()

    def _runner(self, ssh) -> PrecheckRunner:
        return PrecheckRunner(ssh)

    # ------------------------------------------------------------ 加载
    def _load(self) -> None:
        server = self.ctx.server
        if server is None:
            QMessageBox.information(self, "提示", "请先在步骤1勾选要登录的服务器")
            return
        container = self.container
        self.load_btn.setEnabled(False)
        self.validate_label.setText("正在从容器读取配置...")

        def fn():
            with SSHClient(server) as ssh:
                runner = self._runner(ssh)
                if runner.config_exists(container):
                    return ("custom", runner.read_custom_config(container))
                return ("default", runner.read_default_config(container))

        def on_done(result, error):
            self.load_btn.setEnabled(True)
            if error is not None:
                self.validate_label.setText(f"✗ 读取失败：{error}")
                QMessageBox.warning(self, "读取失败", str(error))
                return
            kind, text = result
            self.editor.setPlainText(text or "")
            source = ("容器内已保存的自定义配置" if kind == "custom"
                      else "随 ucm-toolkit 安装的出厂默认配置")
            self.validate_label.setText(f"已加载：{source}")

        self._run_quick(fn, on_done)

    # ------------------------------------------------------------ 编辑校验
    def _validate(self) -> None:
        text = self.editor.toPlainText().strip()
        if not text:
            self.validate_label.setText("配置为空（保存后等效于出厂默认）")
            self.save_btn.setEnabled(False)
            return
        try:
            json.loads(text)
        except ValueError as exc:
            self.validate_label.setText(f"✗ JSON 语法错误：{exc}")
            self.save_btn.setEnabled(False)
        else:
            self.validate_label.setText("✓ JSON 语法正确")
            self.save_btn.setEnabled(True)

    # ------------------------------------------------------------ 保存/恢复
    def _save(self) -> None:
        server = self.ctx.server
        if server is None:
            return
        json_text = self.editor.toPlainText().strip()
        container = self.container
        self.save_btn.setEnabled(False)

        def fn():
            with SSHClient(server) as ssh:
                self._runner(ssh).save_config(container, json_text)

        def on_done(_result, error):
            self.save_btn.setEnabled(True)
            if error is not None:
                QMessageBox.warning(self, "保存失败", str(error))
                return
            QMessageBox.information(
                self, "已启用", f"自定义预检配置已保存到容器内\n{CONFIG_CONTAINER_PATH}")
            self.accept()

        self._run_quick(fn, on_done)

    def _reset(self) -> None:
        server = self.ctx.server
        if server is None:
            return
        container = self.container

        def fn():
            with SSHClient(server) as ssh:
                return self._runner(ssh).remove_config(container)

        def on_done(existed, error):
            if error is not None:
                QMessageBox.warning(self, "恢复失败", str(error))
                return
            if existed:
                QMessageBox.information(
                    self, "已恢复默认", "容器内自定义预检配置已删除，预检回到出厂默认。")
            else:
                QMessageBox.information(self, "提示", "容器内没有自定义配置，本就在使用出厂默认。")
            self.accept()

        self._run_quick(fn, on_done)
