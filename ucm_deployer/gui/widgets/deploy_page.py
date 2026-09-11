# -*- coding: utf-8 -*-
"""步骤4：部署配置 —— 容器选择/UCM校验、PD拓扑、资源检查、脚本生成（可编辑）。"""
from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.command_generator import CommandGenerator
from ...core.docker_manager import DockerManager
from ...core.models import DeviceType
from ...core.resource_checker import check_resources
from ...core.topology import (DeployMode, DeployPlan, NodePlan, NodeRole,
                              validate_plan)
from ..state import AppContext
from .common import ParallelTaskPanel

_ROLE_TEXT = {NodeRole.MIXED: "混部", NodeRole.P: "P(Prefill)", NodeRole.D: "D(Decode)"}


class DeployPage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._node_cache: Dict[str, NodePlan] = {}

        # ---------- 容器选择
        ctr_box = QGroupBox("1. 选择已安装 UCM 的容器")
        self.ctr_form = QFormLayout()
        ctr_box.setLayout(self.ctr_form)
        refresh_ctr_btn = QPushButton("🔄 刷新容器列表")
        check_ctr_btn = QPushButton("✅ 检查容器内 UCM")
        res_btn = QPushButton("🔍 卡资源占用检查")
        refresh_ctr_btn.clicked.connect(self._refresh_containers)
        check_ctr_btn.clicked.connect(self._check_container_ucm)
        res_btn.clicked.connect(self._check_resources)
        self.ctr_rows: Dict[str, QComboBox] = {}

        # ---------- 拓扑
        topo_box = QGroupBox("2. 部署形态与节点拓扑")
        self.colocated_radio = QRadioButton("PD 混部（不区分 P/D）")
        self.pd_radio = QRadioButton("PD 分离（P/D 分组）")
        self.colocated_radio.setChecked(True)
        self.colocated_radio.toggled.connect(self._mode_changed)
        self.vllm_radio = QRadioButton("vLLM")
        self.sglang_radio = QRadioButton("SGLang")
        self.vllm_radio.setChecked(True)
        self.vllm_radio.toggled.connect(self._validate)

        self.node_table = QTableWidget(0, 5)
        self.node_table.setHorizontalHeaderLabels(
            ["服务器", "容器", "角色", "DP(进程数)", "TP(卡/进程)"])
        header = self.node_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.node_table.verticalHeader().setVisible(False)

        auto_btn = QPushButton("🪄 自动填满卡数 (DP=cards/TP)")
        auto_btn.clicked.connect(self._auto_fill)

        mode_row = QHBoxLayout()
        mode_row.addWidget(self.colocated_radio)
        mode_row.addWidget(self.pd_radio)
        mode_row.addSpacing(20)
        mode_row.addWidget(QLabel("引擎:"))
        mode_row.addWidget(self.vllm_radio)
        mode_row.addWidget(self.sglang_radio)
        mode_row.addStretch(1)
        mode_row.addWidget(auto_btn)

        topo_layout = QVBoxLayout(topo_box)
        topo_layout.addLayout(mode_row)
        topo_layout.addWidget(self.node_table)

        # ---------- 参数
        param_box = QGroupBox("3. 服务参数")
        self.model_edit = QLineEdit("/models/your-model")
        self.served_edit = QLineEdit()
        self.served_edit.setPlaceholderText("默认取模型目录名")
        self.ucm_cfg_edit = QLineEdit("/root/ucm-deploy/ucm_config.yaml")
        self.kv_dir_edit = QLineEdit("/mnt/nfs_share")
        self.nic_edit = QLineEdit()
        self.nic_edit.setPlaceholderText("留空自动探测")
        self.pp_spin = QSpinBox()
        self.pp_spin.setRange(1, 16)
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(9000)
        self.mooncake_spin = QSpinBox()
        self.mooncake_spin.setRange(1024, 65535)
        self.mooncake_spin.setValue(20001)
        self.patch_check = QCheckBox("启用 UCM monkey patch (ENABLE_UCM_PATCH=1, vLLM>=0.11)")
        self.patch_check.setChecked(True)
        self.extra_edit = QPlainTextEdit()
        self.extra_edit.setPlaceholderText(
            "附加 vllm/sglang 参数，每行一个，如：\n--max-model-len 32000\n--gpu-memory-utilization 0.9")
        self.extra_edit.setMaximumHeight(90)
        pform = QFormLayout(param_box)
        pform.addRow("模型路径(容器内)", self.model_edit)
        pform.addRow("--served-model-name", self.served_edit)
        pform.addRow("UCM 配置文件(容器内)", self.ucm_cfg_edit)
        pform.addRow("kvcache 共享目录", self.kv_dir_edit)
        pform.addRow("通信网卡", self.nic_edit)
        prow = QHBoxLayout()
        prow.addWidget(QLabel("服务起始端口"))
        prow.addWidget(self.port_spin)
        prow.addWidget(QLabel("mooncake起始端口"))
        prow.addWidget(self.mooncake_spin)
        prow.addWidget(QLabel("PP(多机混部)"))
        prow.addWidget(self.pp_spin)
        prow.addStretch(1)
        pform.addRow("端口规划", prow)
        pform.addRow("UCM patch", self.patch_check)
        pform.addRow("附加参数", self.extra_edit)
        for w in (self.model_edit, self.served_edit, self.ucm_cfg_edit,
                  self.kv_dir_edit, self.nic_edit, self.extra_edit, self.pp_spin):
            w.textChanged.connect(lambda *_: self._validate())
            if hasattr(w, "valueChanged"):
                w.valueChanged.connect(lambda *_: self._validate())

        # ---------- 校验与脚本
        self.validate_label = QLabel("—")
        self.validate_label.setWordWrap(True)
        gen_btn = QPushButton("📜 生成部署脚本（可编辑）")
        gen_btn.clicked.connect(self._generate)
        self.script_tabs = QTabWidget()
        self.script_edits: Dict[str, QPlainTextEdit] = {}

        bottom = QVBoxLayout()
        bottom.addWidget(QLabel("校验结果："))
        bottom.addWidget(self.validate_label)
        bottom.addWidget(gen_btn)
        bottom.addWidget(self.script_tabs, 1)

        left = QVBoxLayout()
        left.addWidget(ctr_box)
        top_row = QHBoxLayout()
        top_row.addWidget(refresh_ctr_btn)
        top_row.addWidget(check_ctr_btn)
        top_row.addWidget(res_btn)
        top_row.addStretch(1)
        left.addLayout(top_row)
        left.addWidget(topo_box, 1)
        left.addWidget(param_box, 2)

        splitter = QSplitter(Qt.Horizontal)
        left_widget = QWidget()
        left_widget.setLayout(left)
        right_widget = QWidget()
        right_widget.setLayout(bottom)
        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 4)

        self.panel = ParallelTaskPanel()
        layout = QVBoxLayout(self)
        layout.addWidget(splitter, 1)
        layout.addWidget(self.panel, 2)

    # ------------------------------------------------------------ 初始化
    def on_enter(self) -> None:
        for i in reversed(range(self.ctr_form.count())):
            item = self.ctr_form.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.ctr_rows.clear()
        for s in self.ctx.selected:
            combo = QComboBox()
            combo.setEditable(True)
            current = self.ctx.containers.get(s.id, "")
            if current:
                combo.addItem(current)
            self.ctr_rows[s.id] = combo
            self.ctr_form.addRow(f"{s.name} ({s.host})", combo)
        self._rebuild_node_table()
        self._validate()

    def _mode_changed(self) -> None:
        self._rebuild_node_table()
        self._validate()

    def _rebuild_node_table(self) -> None:
        pd_mode = self.pd_radio.isChecked()
        self.node_table.setRowCount(0)
        for s in self.ctx.selected:
            row = self.node_table.rowCount()
            self.node_table.insertRow(row)
            self.node_table.setItem(row, 0, QTableWidgetItem(f"{s.name}\n{s.host}"))
            combo = QComboBox()
            combo.setEditable(True)
            current = self.ctx.containers.get(s.id, "")
            if current:
                combo.addItem(current)
            self.node_table.setCellWidget(row, 1, combo)
            role = QComboBox()
            if pd_mode:
                role.addItems([_ROLE_TEXT[NodeRole.P], _ROLE_TEXT[NodeRole.D]])
                role.setCurrentIndex(0 if row == 0 else min(1, row))
            else:
                role.addItems([_ROLE_TEXT[NodeRole.MIXED]])
            role.currentIndexChanged.connect(lambda *_: self._validate())
            self.node_table.setCellWidget(row, 2, role)
            dev = self.ctx.device_of(s)
            cards = max(dev.count, 1)
            dp = QSpinBox()
            dp.setRange(1, 64)
            dp.setValue(1)
            tp = QSpinBox()
            tp.setRange(1, 64)
            tp.setValue(cards)
            for spin in (dp, tp):
                spin.valueChanged.connect(lambda *_: self._validate())
            self.node_table.setCellWidget(row, 3, dp)
            self.node_table.setCellWidget(row, 4, tp)
            if row == 0:
                role_cell = self.node_table.cellWidget(row, 2)
        # PD 模式默认第一台为 P，其余为 D；至少保留一台 D
        if pd_mode and self.node_table.rowCount() >= 2:
            w = self.node_table.cellWidget(0, 2)
            if w is not None:
                w.setCurrentIndex(0)
            w2 = self.node_table.cellWidget(1, 2)
            if w2 is not None:
                w2.setCurrentIndex(1)

    def _auto_fill(self) -> None:
        for row in range(self.node_table.rowCount()):
            s = self.ctx.selected[row]
            cards = max(self.ctx.cards_of(s), 1)
            tp = self.node_table.cellWidget(row, 4)
            if tp is None:
                continue
            tpv = tp.value()
            dp = self.node_table.cellWidget(row, 3)
            if dp is None:
                continue
            dp.setValue(max(1, cards // max(tpv, 1)))
        self._validate()

    # ------------------------------------------------------------ 容器/资源
    def _refresh_containers(self) -> None:
        def make_fn(server):
            def fn(ssh, tctx):
                dm = DockerManager(ssh)
                cs = dm.list_containers(all_containers=True)
                self._ctr_lists = getattr(self, "_ctr_lists", {})
                self._ctr_lists[server.id] = [c.names for c in cs]
                tctx.log("\n".join(f"{c.names} [{c.state}] {c.image}" for c in cs))
            return fn

        def on_done(all_ok):
            try:
                if all_ok:
                    lists = getattr(self, "_ctr_lists", {})
                    for sid, combo in self.ctr_rows.items():
                        current = combo.currentText()
                        combo.clear()
                        combo.addItems(lists.get(sid, []))
                        if current:
                            combo.setCurrentText(current)
            finally:
                self.panel.finished_all.disconnect(on_done)

        self.panel.finished_all.connect(on_done)
        self.panel.start([(s, make_fn(s)) for s in self.ctx.selected], "刷新容器")

    def _check_container_ucm(self) -> None:
        def make_fn(server):
            def fn(ssh, tctx):
                combo = self.ctr_rows.get(server.id)
                name = combo.currentText().strip() if combo else ""
                if not name:
                    raise RuntimeError("未选择容器")
                ucm = DockerManager(ssh).container_ucm_info(name)
                tctx.log(f"容器 {name}: {ucm}")
                if not ucm.installed:
                    raise RuntimeError(f"容器 {name} 内未检测到 UCM，请回到步骤2/3")
                self.ctx.containers[server.id] = name
            return fn

        self.panel.start([(s, make_fn(s)) for s in self.ctx.selected], "检查容器 UCM")

    def _check_resources(self) -> None:
        reports: Dict[str, str] = {}

        def make_fn(server):
            def fn(ssh, tctx):
                dev = self.ctx.device_of(server)
                report = check_resources(ssh, dev)
                tctx.log(f"[{server.name}] {report.message}")
                for c in report.running_containers:
                    tctx.log("  容器: " + c)
                reports[server.name] = report.message
            return fn

        def on_done(all_ok):
            try:
                text = "\n\n".join(f"【{n}】{m}" for n, m in reports.items())
                QMessageBox.information(self, "卡资源占用检查", text or "无结果")
            finally:
                self.panel.finished_all.disconnect(on_done)

        self.panel.finished_all.connect(on_done)
        self.panel.start([(s, make_fn(s)) for s in self.ctx.selected], "资源检查")

    # ------------------------------------------------------------ 校验/生成
    def _collect_nodes(self) -> List[NodePlan]:
        nodes = []
        pd_mode = self.pd_radio.isChecked()
        for row in range(self.node_table.rowCount()):
            if row >= len(self.ctx.selected):
                break
            s = self.ctx.selected[row]
            combo = self.node_table.cellWidget(row, 1)
            role_w = self.node_table.cellWidget(row, 2)
            dp_w = self.node_table.cellWidget(row, 3)
            tp_w = self.node_table.cellWidget(row, 4)
            role_text = role_w.currentText() if role_w else _ROLE_TEXT[NodeRole.MIXED]
            role = next((r for r, t in _ROLE_TEXT.items() if t == role_text), NodeRole.MIXED)
            if not pd_mode:
                role = NodeRole.MIXED
            nodes.append(NodePlan(
                server_id=s.id, server_name=s.name, host=s.host, role=role,
                dp=dp_w.value() if dp_w else 1,
                tp=tp_w.value() if tp_w else 1,
                container=(combo.currentText().strip() if combo else ""),
                cards=self.ctx.cards_of(s)))
        return nodes

    def build_plan(self) -> DeployPlan:
        device_type = DeviceType.UNKNOWN
        types = {self.ctx.device_of(s).device_type for s in self.ctx.selected}
        if types == {DeviceType.ASCEND}:
            device_type = DeviceType.ASCEND
        elif types == {DeviceType.NVIDIA}:
            device_type = DeviceType.NVIDIA
        return DeployPlan(
            mode=DeployMode.PD if self.pd_radio.isChecked() else DeployMode.COLOCATED,
            engine="vllm" if self.vllm_radio.isChecked() else "sglang",
            device_type=device_type,
            nodes=self._collect_nodes(),
            model_path=self.model_edit.text().strip(),
            served_model_name=self.served_edit.text().strip(),
            ucm_config_file=self.ucm_cfg_edit.text().strip(),
            kv_cache_dir=self.kv_dir_edit.text().strip(),
            enable_ucm_patch=self.patch_check.isChecked(),
            pp=self.pp_spin.value(),
            server_port=self.port_spin.value(),
            mooncake_port=self.mooncake_spin.value(),
            nic_name=self.nic_edit.text().strip(),
            extra_args=self.extra_edit.toPlainText())

    def _validate(self) -> None:
        if not self.ctx.selected:
            self.validate_label.setText("—")
            return
        plan = self.build_plan()
        issues = validate_plan(plan)
        errors = [i.message for i in issues if i.is_error]
        warnings = [i.message for i in issues if not i.is_error]
        if errors:
            self.validate_label.setText("❌ " + "\n❌ ".join(errors)
                                       + ("\n⚠ " + "\n⚠ ".join(warnings) if warnings else ""))
            self.validate_label.setStyleSheet("color:#c0392b;")
        elif warnings:
            self.validate_label.setText("⚠ " + "\n⚠ ".join(warnings))
            self.validate_label.setStyleSheet("color:#b7791f;")
        else:
            p_nodes = len(plan.p_nodes)
            d_nodes = len(plan.d_nodes)
            total = sum(n.used_cards for n in plan.nodes)
            self.validate_label.setText(
                f"✓ 校验通过：{len(plan.nodes)} 节点 / 共 {total} 卡"
                + (f"（P:{p_nodes} D:{d_nodes}）" if plan.mode == DeployMode.PD else ""))
            self.validate_label.setStyleSheet("color:#1e8449;")

    def _generate(self) -> None:
        plan = self.build_plan()
        errors = [i.message for i in validate_plan(plan) if i.is_error]
        if errors:
            QMessageBox.warning(self, "无法生成", "存在校验错误：\n" + "\n".join(errors))
            return
        ds = CommandGenerator(plan).generate()
        self.ctx.scripts = ds
        # 保留用户已编辑过的同名脚本内容
        self.script_tabs.clear()
        self.script_edits.clear()
        for s in ds.scripts:
            edit = QPlainTextEdit()
            edit.setFont(QFont("Consolas"))
            edit.setPlainText(s.content)
            self.script_edits[f"{s.server_name}:{s.name}"] = edit
            self.script_tabs.addTab(edit, f"{s.server_name}/{s.name}")
        for f in ds.config_files:
            key = f"{f.server_name}:{f.name}"
            if key in self.script_edits:
                continue
            edit = QPlainTextEdit()
            edit.setFont(QFont("Consolas"))
            edit.setPlainText(f.content)
            self.script_edits[key] = edit
            self.script_tabs.addTab(edit, f"{f.server_name}/{f.name} [配置]")
        # 把用户编辑回写到 scripts（进入步骤5前）
        for s in list(ds.scripts) + list(ds.config_files):
            edit = self.script_edits.get(f"{s.server_name}:{s.name}")
            if edit is not None:
                s.content = edit.toPlainText()
        self._validate()
        QMessageBox.information(
            self, "生成完成",
            f"已生成 {len(ds.scripts)} 个脚本 / {len(ds.config_files)} 个配置文件。\n"
            "脚本内容可直接编辑，编辑结果在进入步骤5时生效。\n"
            f"拓扑: {ds.summary}")
