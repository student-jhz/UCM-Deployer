# -*- coding: utf-8 -*-
"""步骤2：镜像构建 —— 基础镜像选择/tar上传docker load、UCM whl安装、构建带UCM镜像。"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ...core.docker_manager import DockerManager
from ...core.image_builder import (ImageBuildConfig, ImageBuilder,
                                   suggest_tag, upload_image_tar)
from ...core.models import DockerImage, ServerInfo
from ..state import AppContext
from .common import (ParallelTaskPanel, combo_ref, fill_image_combo,
                     image_from_ref, setup_image_combo)


class ImagePage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._image_lists: Dict[str, List[str]] = {}

        # ---------- 镜像选择区
        image_box = QGroupBox("1. 选择基础镜像（每台服务器）")
        self.image_rows: Dict[str, QComboBox] = {}
        self.image_form = QFormLayout()
        self.placeholder = QLabel("请先在「1. 服务器管理」勾选要部署的服务器")
        self.placeholder.setProperty("role", "hint")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setMinimumHeight(48)
        image_box.setLayout(QVBoxLayout())
        image_box.layout().addWidget(self.placeholder)
        image_box.layout().addLayout(self.image_form)
        refresh_btn = QPushButton("🔄 刷新镜像列表")
        check_ucm_btn = QPushButton("✅ 检查所选镜像 UCM")
        upload_btn = QPushButton("⬆ 上传镜像 tar/tar.gz 并 docker load")
        refresh_btn.clicked.connect(self._refresh_images)
        check_ucm_btn.clicked.connect(self._check_ucm)
        upload_btn.clicked.connect(self._upload_tar)
        for b in (refresh_btn, check_ucm_btn, upload_btn):
            b.setToolTip("对步骤1勾选的全部服务器并行执行")

        # ---------- 构建参数区
        build_box = QGroupBox("2. UCM 安装与镜像构建")
        self.ucm_whl_edit = QLineEdit()
        self.wrapt_whl_edit = QLineEdit()
        self.toolkit_edit = QLineEdit()
        for w in (self.ucm_whl_edit, self.wrapt_whl_edit, self.toolkit_edit):
            w.setPlaceholderText("本地文件路径")
        ucm_btn = QPushButton("浏览...")
        wrapt_btn = QPushButton("浏览...")
        toolkit_btn = QPushButton("浏览...")
        ucm_btn.clicked.connect(lambda: self._pick_file(self.ucm_whl_edit, "UCM whl (*.whl)"))
        wrapt_btn.clicked.connect(lambda: self._pick_file(self.wrapt_whl_edit, "wrapt whl (*.whl)"))
        toolkit_btn.clicked.connect(
            lambda: self._pick_dir(self.toolkit_edit))

        self.online_radio = QRadioButton("服务器可联网（pip 自动安装 wrapt 依赖）")
        self.offline_radio = QRadioButton("服务器离线（需上传本地 wrapt whl）")
        self.online_radio.setChecked(True)
        self.offline_radio.toggled.connect(self._mode_changed)

        self.tag_edit = QLineEdit()
        self.tag_edit.setPlaceholderText("新镜像名，默认根据基础镜像自动生成")
        self.platform_label = QLabel("-")

        form = QFormLayout(build_box)
        row1 = QHBoxLayout()
        row1.addWidget(self.ucm_whl_edit, 1)
        row1.addWidget(ucm_btn)
        row2 = QHBoxLayout()
        row2.addWidget(self.wrapt_whl_edit, 1)
        row2.addWidget(wrapt_btn)
        row3 = QHBoxLayout()
        row3.addWidget(self.toolkit_edit, 1)
        row3.addWidget(toolkit_btn)

        # 在线下载提示（本地没有包时指引获取途径，链接可点击直达浏览器）
        self.ucm_dl_hint = QLabel(
            '⬇ 本地没有 UCM 包？<a href="https://github.com/ModelEngine-Group/'
            'unified-cache-management/releases">从 GitHub Releases 在线下载'
            ' uc_manager-*.whl</a>')
        self.wrapt_dl_hint = QLabel(
            '⬇ 离线依赖 wrapt：<a href="https://pypi.org/project/wrapt/#files">'
            '从 PyPI 文件列表在线下载</a>'
            '（选择匹配服务器 Python 版本与 CPU 架构的 whl，'
            '如 manylinux 的 x86_64 / aarch64）')
        for hint in (self.ucm_dl_hint, self.wrapt_dl_hint):
            hint.setProperty("role", "hint")
            hint.setOpenExternalLinks(True)
        self.ucm_dl_hint.setToolTip(
            "https://github.com/ModelEngine-Group/unified-cache-management/releases")
        self.wrapt_dl_hint.setToolTip("https://pypi.org/project/wrapt/#files")

        form.addRow("UCM whl 包*", row1)
        form.addRow("", self.ucm_dl_hint)
        form.addRow("wrapt whl（离线）", row2)
        form.addRow("", self.wrapt_dl_hint)
        form.addRow("ucm-toolkit 源码目录（可选）", row3)
        form.addRow("联网模式", self.online_radio)
        form.addRow("", self.offline_radio)
        form.addRow("新镜像名", self.tag_edit)
        form.addRow("构建平台(ENV PLATFORM)", self.platform_label)

        build_btn = QPushButton("🔨 开始构建（全部服务器）")
        build_btn.setProperty("accent", True)
        build_btn.clicked.connect(self._build)
        use_existing_btn = QPushButton("⏭ 使用已有 UCM 镜像，跳过构建")
        use_existing_btn.clicked.connect(self._use_existing)

        btns = QHBoxLayout()
        btns.addWidget(build_btn)
        btns.addWidget(use_existing_btn)

        self.hint = QLabel(
            "提示：若服务器上已有安装了 UCM 的镜像，可直接「检查所选镜像 UCM」后跳过构建。"
            "构建 = 上传 whl -> 生成 Dockerfile -> docker build -> 校验 UCM 安装。")
        self.hint.setWordWrap(True)

        self.panel = ParallelTaskPanel()

        top = QHBoxLayout()
        top.addWidget(refresh_btn)
        top.addWidget(check_ucm_btn)
        top.addWidget(upload_btn)
        top.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(image_box)
        layout.addWidget(build_box)
        layout.addWidget(self.hint)
        layout.addLayout(btns)
        layout.addWidget(self.panel, 2)

    # ------------------------------------------------------------ 初始化
    def on_enter(self) -> None:
        """进入本页：按已选服务器重建镜像下拉（只可选择）并自动加载镜像列表。"""
        for i in reversed(range(self.image_form.count())):
            item = self.image_form.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.image_rows.clear()
        self.placeholder.setVisible(not self.ctx.selected)
        for s in self.ctx.selected:
            combo = QComboBox()
            # 弹层限高(10条)滚动；可输入关键字筛选，但只能选中列表项
            setup_image_combo(combo)
            combo.setMinimumWidth(380)
            combo.currentTextChanged.connect(self._suggest_tag_if_empty)
            self.image_rows[s.id] = combo
            self.image_form.addRow(f"{s.name} ({s.host})", combo)
            # 已记录的镜像先回显（等待自动刷新补全大小等信息）
            fill_image_combo(combo,
                             [image_from_ref(self.ctx.images[s.id])]
                             if self.ctx.images.get(s.id) else [])
        devs = {s.id: self.ctx.device_of(s) for s in self.ctx.selected}
        types = {d.device_type.value for d in devs.values()}
        platform = "ascend" if types == {"ascend"} else ("cuda" if types == {"nvidia"} else "ascend")
        self.platform_label.setText(platform)
        # 任一已选服务器缺少镜像列表时自动加载；否则直接回填已知列表
        missing = [s for s in self.ctx.selected if s.id not in self._image_lists]
        if self.ctx.selected and (missing or not self.image_rows):
            self._refresh_images()
        elif self.image_rows:
            self._apply_image_lists()

    def _suggest_tag_if_empty(self, *_):
        if not self.tag_edit.text().strip():
            first = next(iter(self.image_rows.values()), None)
            ref = combo_ref(first) if first is not None else ""
            if ref:
                self.tag_edit.setText(suggest_tag(ref))

    def _servers(self) -> List[ServerInfo]:
        return self.ctx.selected

    def _pick_file(self, edit: QLineEdit, flt: str) -> None:
        from PySide6.QtCore import QSettings

        settings = QSettings("UCM-Deployer", "paths")
        start = str(settings.value("last_file_dir", ""))
        path, _ = QFileDialog.getOpenFileName(self, "选择文件", start, flt)
        if path:
            edit.setText(path)
            settings.setValue("last_file_dir", os.path.dirname(path))

    def _pick_dir(self, edit: QLineEdit) -> None:
        from PySide6.QtCore import QSettings

        settings = QSettings("UCM-Deployer", "paths")
        start = str(settings.value("last_file_dir", ""))
        path = QFileDialog.getExistingDirectory(self, "选择 ucm-toolkit 源码目录", start)
        if path:
            edit.setText(path)
            settings.setValue("last_file_dir", path)

    def _mode_changed(self) -> None:
        offline = self.offline_radio.isChecked()
        self.wrapt_whl_edit.setEnabled(offline)

    # ------------------------------------------------------------ 镜像操作
    def _refresh_images(self) -> None:
        servers = self._servers()
        if not servers:
            QMessageBox.information(self, "提示", "请先在步骤1选择服务器")
            return

        def make_fn(server):
            def fn(ssh, tctx):
                tctx.progress(40, "获取镜像列表")
                dm = DockerManager(ssh)
                if not dm.check_docker():
                    raise RuntimeError("docker 不可用")
                images = dm.list_images()
                self._image_lists[server.id] = images
                tctx.log(f"共 {len(images)} 个镜像：")
                for im in images:
                    mark = " [疑似UCM]" if "ucm" in im.ref.lower() else ""
                    tctx.log(f"  {im.ref}    {im.size}{mark}")
                tctx.progress(100, f"{len(images)} 个镜像")
            return fn

        def on_finished(all_ok: bool) -> None:
            if all_ok:
                self._apply_image_lists()
            else:
                for combo in self.image_rows.values():
                    fill_image_combo(combo, [])  # 显示加载失败占位提示

        self.panel.run_tasks([(s, make_fn(s)) for s in servers],
                             "刷新镜像列表", on_finished=on_finished)

    def _apply_image_lists(self) -> None:
        for sid, combo in self.image_rows.items():
            fill_image_combo(combo, self._image_lists.get(sid) or [],
                             current=self.ctx.images.get(sid, ""))
        if not self.tag_edit.text().strip():
            first = next(iter(self.image_rows.values()), None)
            ref = combo_ref(first) if first is not None else ""
            if ref:
                self.tag_edit.setText(suggest_tag(ref))

    @staticmethod
    def _looks_ucm(ref: str) -> bool:
        return "ucm" in ref.lower()

    def _upload_tar(self) -> None:
        servers = self._servers()
        if not servers:
            QMessageBox.information(self, "提示", "请先在步骤1选择服务器")
            return
        from PySide6.QtCore import QSettings

        settings = QSettings("UCM-Deployer", "paths")
        start = str(settings.value("last_file_dir", ""))
        path, _ = QFileDialog.getOpenFileName(
            self, "选择镜像 tar 包", start, "镜像包 (*.tar *.tar.gz *.tgz)")
        if not path:
            return
        settings.setValue("last_file_dir", os.path.dirname(path))

        def make_fn(path_):
            def fn(ssh, tctx):
                dm = DockerManager(ssh)
                refs = upload_image_tar(
                    ssh, dm, path_,
                    progress=lambda p, m: tctx.progress(p, m),
                    log=tctx.log)
                if not refs:
                    raise RuntimeError("docker load 未解析出镜像名")
                tctx.log("已加载: " + ", ".join(refs))
                self._image_lists.setdefault(ssh.server.id, [])
                for r in refs:
                    if not any(im.ref == r for im in self._image_lists[ssh.server.id]):
                        self._image_lists[ssh.server.id].append(image_from_ref(r))
            return fn

        def on_finished(all_ok: bool) -> None:
            if all_ok:
                self._apply_image_lists()
                QMessageBox.information(self, "完成", "镜像上传并加载完成")

        self.panel.run_tasks([(s, make_fn(path)) for s in servers],
                             "上传镜像", on_finished=on_finished)

    def _selected_bases(self) -> Optional[Dict[str, str]]:
        bases = {}
        for sid, combo in self.image_rows.items():
            ref = combo_ref(combo)
            if not ref:
                QMessageBox.warning(
                    self, "参数错误",
                    "存在未选择基础镜像的服务器。\n"
                    "镜像列表来自服务器 docker images（进入本页自动加载，"
                    "也可点「刷新镜像列表」），只能从列表中选择。")
                return None
            bases[sid] = ref
        return bases

    def _check_ucm(self) -> None:
        servers = self._servers()
        bases = self._selected_bases()
        if not servers or bases is None:
            return

        def make_fn(server):
            def fn(ssh, tctx):
                ref = bases[server.id]
                tctx.progress(50, f"检查 {ref}")
                ucm = DockerManager(ssh).image_ucm_info(ref)
                tctx.log(f"{ref}: {ucm}")
                tctx.progress(100, str(ucm))
                if ucm.installed:
                    self.ctx.images[server.id] = ref
            return fn

        self.panel.start([(s, make_fn(s)) for s in servers], "检查镜像 UCM")

    def _use_existing(self) -> None:
        bases = self._selected_bases()
        if bases is None:
            return
        for sid, ref in bases.items():
            self.ctx.images[sid] = ref
        QMessageBox.information(
            self, "已记录", "已将当前所选镜像记录为本服务器使用的 UCM 镜像：\n"
            + "\n".join(f"{ref}" for ref in bases.values())
            + "\n\n请确认镜像内已安装 UCM（建议先执行「检查所选镜像 UCM」）。")

    # ------------------------------------------------------------ 构建
    def _build(self) -> None:
        servers = self._servers()
        bases = self._selected_bases()
        if not servers or bases is None:
            return
        ucm_whl = self.ucm_whl_edit.text().strip()
        if not ucm_whl or not os.path.isfile(ucm_whl):
            QMessageBox.warning(self, "参数错误", "请选择有效的 UCM whl 文件")
            return
        offline = self.offline_radio.isChecked()
        wrapt_whl = self.wrapt_whl_edit.text().strip()
        if offline and wrapt_whl and not os.path.isfile(wrapt_whl):
            QMessageBox.warning(self, "参数错误", "离线模式下 wrapt whl 路径无效")
            return
        tag = self.tag_edit.text().strip() or suggest_tag(
            next(iter(bases.values()), "ucm-engine:latest"))
        platform = self.platform_label.text().strip() or "ascend"

        def make_fn(server):
            cfg = ImageBuildConfig(
                base_image=bases[server.id], image_tag=tag,
                ucm_whl_local=ucm_whl,
                wrapt_whl_local=wrapt_whl if offline else "",
                offline=offline,
                toolkit_dir=self.toolkit_edit.text().strip(),
                platform=platform)

            def fn(ssh, tctx):
                result = ImageBuilder(ssh, DockerManager(ssh)).build(
                    cfg, progress=tctx.progress, log=tctx.log,
                    cancelled=tctx.cancelled)
                tctx.log(f"构建完成: {result.image} (UCM {result.ucm.version if result.ucm else '?'})")
                self.ctx.images[server.id] = result.image
            return fn

        def on_finished(all_ok: bool) -> None:
            if all_ok:
                QMessageBox.information(
                    self, "构建完成",
                    f"全部服务器构建完成，镜像: {tag}\n已记录为各服务器使用的 UCM 镜像。")

        self.panel.run_tasks([(s, make_fn(s)) for s in servers],
                             "构建镜像", on_finished=on_finished)
