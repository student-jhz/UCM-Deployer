# -*- coding: utf-8 -*-
"""步骤2：镜像构建 —— 单台构建服务器上构建，再分发到其他服务器。

流程：选择构建服务器与基础镜像（或上传 tar 包 docker load）
      -> UCM whl 安装构建新镜像（仅构建服务器）
      -> 自动分发到其他已选服务器（docker save -> 本机中转 -> docker load），
         每台服务器独立日志与进度，结束后汇总成功/失败及原因。
"""
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
                                    export_image_to_local, import_image,
                                    suggest_tag, upload_image_tar)
from ...core.models import DockerImage, ServerInfo
from ..state import AppContext
from .common import ParallelTaskPanel, combo_ref, fill_image_combo, image_from_ref, setup_image_combo


class ImagePage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._image_lists: Dict[str, List[DockerImage]] = {}
        self._build_sid: Optional[str] = None

        # ---------- 镜像选择区
        image_box = QGroupBox("1. 选择构建服务器与基础镜像")
        self.image_rows: Dict[str, QComboBox] = {}
        self.placeholder = QLabel("请先在「1. 服务器管理」勾选要部署的服务器")
        self.placeholder.setProperty("role", "hint")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setMinimumHeight(48)

        self.build_combo = QComboBox()
        self.build_combo.currentIndexChanged.connect(self._on_build_server_changed)
        self.build_combo.setToolTip("镜像只在这台服务器上构建，完成后自动分发到其他已选服务器")
        server_form = QFormLayout()
        server_form.addRow("构建服务器", self.build_combo)

        self.image_form = QFormLayout()
        image_box.setLayout(QVBoxLayout())
        image_box.layout().addWidget(self.placeholder)
        image_box.layout().addLayout(server_form)
        image_box.layout().addLayout(self.image_form)
        refresh_btn = QPushButton("🔄 刷新镜像列表")
        check_ucm_btn = QPushButton("✅ 检查所选镜像 UCM")
        upload_btn = QPushButton("⬆ 上传镜像 tar/tar.gz 到构建服务器")
        refresh_btn.clicked.connect(self._refresh_images)
        check_ucm_btn.clicked.connect(self._check_ucm)
        upload_btn.clicked.connect(self._upload_tar)
        refresh_btn.setToolTip("刷新构建服务器上的镜像列表")
        check_ucm_btn.setToolTip("检查构建服务器上所选镜像内是否已安装 UCM")
        upload_btn.setToolTip("把本地镜像 tar 包加载到构建服务器（可用于导入基础镜像）")

        # ---------- 构建参数区
        build_box = QGroupBox("2. UCM 安装与镜像构建（构建服务器）")
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

        build_btn = QPushButton("🔨 开始构建（构建服务器，完成后自动分发）")
        build_btn.setProperty("accent", True)
        build_btn.clicked.connect(self._build)
        use_existing_btn = QPushButton("⏭ 使用已有 UCM 镜像，跳过构建")
        use_existing_btn.clicked.connect(self._use_existing)
        distribute_btn = QPushButton("🚚 分发镜像到其他服务器")
        distribute_btn.setToolTip(
            "把构建服务器上当前所选镜像分发到其他已选服务器（已存在的服务器自动跳过）")
        distribute_btn.clicked.connect(self._distribute_selected)

        btns = QHBoxLayout()
        btns.addWidget(build_btn)
        btns.addWidget(use_existing_btn)
        btns.addWidget(distribute_btn)

        self.hint = QLabel(
            "提示：镜像只需在构建服务器上构建一次，完成后自动分发到其他已选服务器"
            "（也可手动点「分发镜像」）。若服务器上已有安装了 UCM 的镜像，"
            "可「检查所选镜像 UCM」后跳过构建。")
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
        """进入本页：按已选服务器重建构建服务器下拉（默认上次选择）并加载镜像列表。"""
        self.build_combo.blockSignals(True)
        self.build_combo.clear()
        for s in self.ctx.selected:
            self.build_combo.addItem(f"{s.name} ({s.host})", s.id)
        idx = 0
        if self._build_sid:
            for i in range(self.build_combo.count()):
                if str(self.build_combo.itemData(i)) == self._build_sid:
                    idx = i
                    break
        self.build_combo.setCurrentIndex(idx)
        self.build_combo.blockSignals(False)
        self.placeholder.setVisible(not self.ctx.selected)
        # 显式同步（addItem 会自动把 currentIndex 置 0，信号不可靠）
        self._on_build_server_changed()

    def _current_build_sid(self) -> Optional[str]:
        return str(self.build_combo.currentData() or "") or None

    def _build_server(self) -> Optional[ServerInfo]:
        sid = self._build_sid or self._current_build_sid()
        for s in self.ctx.selected:
            if s.id == sid:
                return s
        return None

    def _on_build_server_changed(self, *_):
        sid = self._current_build_sid()
        if sid == self._build_sid and self.image_rows:
            return
        self._build_sid = sid
        self._rebuild_image_row()

    def _rebuild_image_row(self) -> None:
        """按当前构建服务器重建基础镜像下拉（带筛选/限高），必要时自动加载列表。"""
        for i in reversed(range(self.image_form.count())):
            item = self.image_form.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.image_rows.clear()
        server = self._build_server()
        if server is None:
            return
        combo = QComboBox()
        # 弹层限高(10条)滚动；可输入关键字筛选，但只能选中列表项
        setup_image_combo(combo)
        combo.setMinimumWidth(380)
        combo.currentTextChanged.connect(self._suggest_tag_if_empty)
        self.image_rows[server.id] = combo
        self.image_form.addRow("基础镜像", combo)
        # 已记录的镜像先回显（等待自动刷新补全大小等信息）
        fill_image_combo(combo,
                         [image_from_ref(self.ctx.images[server.id])]
                         if self.ctx.images.get(server.id) else [])
        if server.id not in self._image_lists:
            self._refresh_images()
        else:
            self._apply_image_lists()

    def _suggest_tag_if_empty(self, *_):
        if not self.tag_edit.text().strip():
            first = next(iter(self.image_rows.values()), None)
            ref = combo_ref(first) if first is not None else ""
            if ref:
                self.tag_edit.setText(suggest_tag(ref))

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
        server = self._build_server()
        if server is None:
            QMessageBox.information(self, "提示", "请先在步骤1选择服务器")
            return

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

        def on_finished(all_ok: bool) -> None:
            if all_ok:
                self._apply_image_lists()
            else:
                combo = self.image_rows.get(server.id)
                if combo is not None:
                    fill_image_combo(combo, [])  # 显示加载失败占位提示

        self.panel.run_tasks([(server, fn)], "刷新镜像列表", on_finished=on_finished)

    def _apply_image_lists(self) -> None:
        combo = self.image_rows.get(self._build_sid) if self._build_sid else None
        if combo is not None:
            fill_image_combo(combo, self._image_lists.get(self._build_sid) or [],
                             current=self.ctx.images.get(self._build_sid, ""))
        if not self.tag_edit.text().strip():
            first = next(iter(self.image_rows.values()), None)
            ref = combo_ref(first) if first is not None else ""
            if ref:
                self.tag_edit.setText(suggest_tag(ref))

    def _upload_tar(self) -> None:
        server = self._build_server()
        if server is None:
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

        def fn(ssh, tctx):
            dm = DockerManager(ssh)
            refs = upload_image_tar(
                ssh, dm, path,
                progress=lambda p, m: tctx.progress(p, m),
                log=tctx.log)
            if not refs:
                raise RuntimeError("docker load 未解析出镜像名")
            tctx.log("已加载: " + ", ".join(refs))
            self._image_lists.setdefault(ssh.server.id, [])
            for r in refs:
                if not any(im.ref == r for im in self._image_lists[ssh.server.id]):
                    self._image_lists[ssh.server.id].append(image_from_ref(r))

        def on_finished(all_ok: bool) -> None:
            if all_ok:
                self._apply_image_lists()
                QMessageBox.information(self, "完成", "镜像已上传并加载到构建服务器")

        self.panel.run_tasks([(server, fn)], "上传镜像", on_finished=on_finished)

    def _selected_base(self) -> Optional[str]:
        """当前选中的基础镜像 ref（构建服务器）。"""
        combo = self.image_rows.get(self._build_sid) if self._build_sid else None
        ref = combo_ref(combo) if combo is not None else ""
        if not ref:
            QMessageBox.warning(
                self, "参数错误",
                "未选择基础镜像。\n"
                "镜像列表来自构建服务器 docker images（进入本页自动加载，"
                "也可点「刷新镜像列表」），只能从列表中选择。")
            return None
        return ref

    def _check_ucm(self) -> None:
        server = self._build_server()
        base = self._selected_base()
        if server is None or base is None:
            return

        def fn(ssh, tctx):
            tctx.progress(50, f"检查 {base}")
            ucm = DockerManager(ssh).image_ucm_info(base)
            tctx.log(f"{base}: {ucm}")
            tctx.progress(100, str(ucm))
            if ucm.installed:
                self.ctx.images[server.id] = base

        self.panel.start([(server, fn)], "检查镜像 UCM")

    def _use_existing(self) -> None:
        base = self._selected_base()
        build = self._build_server()
        if base is None or build is None:
            return
        self.ctx.images[build.id] = base
        if len(self.ctx.selected) <= 1:
            QMessageBox.information(
                self, "已记录",
                f"已将镜像 {base} 记录为本服务器使用的 UCM 镜像。\n"
                "请确认镜像内已安装 UCM（建议先执行「检查所选镜像 UCM」）。")
            return
        self._distribute(base)

    def _distribute_selected(self) -> None:
        base = self._selected_base()
        if base is None:
            return
        self._distribute(base)

    # ------------------------------------------------------------ 分发
    def _distribute(self, image_ref: str) -> None:
        """把构建服务器上的镜像分发到其他已选服务器（导出中转 + 各服务器并行导入）。"""
        build = self._build_server()
        if build is None:
            QMessageBox.information(self, "提示", "请先在步骤1选择服务器")
            return
        targets = [s for s in self.ctx.selected if s.id != build.id]
        if not targets:
            QMessageBox.information(self, "提示", "仅选择了一台服务器，无需分发。")
            return

        local_holder: Dict[str, str] = {}

        def export_fn(ssh, tctx):
            local_tar, _size = export_image_to_local(
                ssh, DockerManager(ssh), image_ref,
                progress=tctx.progress, log=tctx.log,
                cancelled=tctx.cancelled)
            local_holder["path"] = local_tar

        def on_exported(all_ok: bool) -> None:
            if not all_ok or not local_holder.get("path"):
                QMessageBox.warning(
                    self, "分发失败",
                    f"从构建服务器 {build.name} 导出镜像 {image_ref} 失败，"
                    "详见任务日志。")
                return
            self._import_to_targets(image_ref, targets, local_holder["path"])

        self.panel.run_tasks([(build, export_fn)],
                             f"分发镜像：从 {build.name} 导出（docker save + 本机中转）",
                             on_finished=on_exported)

    def _import_to_targets(self, image_ref: str, targets: List[ServerInfo],
                           local_tar: str) -> None:
        outcomes: Dict[str, object] = {}

        def make_fn(server):
            def fn(ssh, tctx):
                try:
                    status = import_image(ssh, DockerManager(ssh), local_tar,
                                          image_ref, progress=tctx.progress,
                                          log=tctx.log, cancelled=tctx.cancelled)
                except Exception as exc:
                    outcomes[server.id] = (False, str(exc))
                    raise
                outcomes[server.id] = (True, status)
                self.ctx.images[server.id] = image_ref
                lst = self._image_lists.setdefault(server.id, [])
                if not any(im.ref == image_ref for im in lst):
                    lst.append(image_from_ref(image_ref))
            return fn

        def on_finished(all_ok: bool) -> None:
            try:
                os.unlink(local_tar)
            except OSError:
                pass
            lines = []
            for s in targets:
                ok, msg = outcomes.get(s.id, (False, "未执行"))
                lines.append(("✓ " if ok else "✗ ") + f"{s.name} ({s.host}): {msg}")
            summary = "\n".join(lines)
            (QMessageBox.information if all_ok else QMessageBox.warning)(
                self,
                "分发完成" if all_ok else "分发结束（存在失败）",
                f"镜像 {image_ref} 分发结果：\n\n{summary}"
                + ("" if all_ok else "\n\n失败原因见上方汇总与各服务器日志页签。"))

        self.panel.run_tasks([(s, make_fn(s)) for s in targets],
                             f"分发镜像 → {len(targets)} 台服务器（上传 + docker load）",
                             on_finished=on_finished)

    # ------------------------------------------------------------ 构建
    def _build(self) -> None:
        server = self._build_server()
        base = self._selected_base()
        if server is None or base is None:
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
        tag = self.tag_edit.text().strip() or suggest_tag(base)
        platform = self.platform_label.text().strip() or "ascend"

        def fn(ssh, tctx):
            cfg = ImageBuildConfig(
                base_image=base, image_tag=tag,
                ucm_whl_local=ucm_whl,
                wrapt_whl_local=wrapt_whl if offline else "",
                offline=offline,
                toolkit_dir=self.toolkit_edit.text().strip(),
                platform=platform)

            result = ImageBuilder(ssh, DockerManager(ssh)).build(
                cfg, progress=tctx.progress, log=tctx.log,
                cancelled=tctx.cancelled)
            tctx.log(f"构建完成: {result.image} (UCM {result.ucm.version if result.ucm else '?'})")
            self.ctx.images[server.id] = result.image
            # 新镜像同步进缓存列表，便于下拉选择/手动分发
            lst = self._image_lists.setdefault(server.id, [])
            if not any(im.ref == result.image for im in lst):
                lst.append(image_from_ref(result.image))

        def on_finished(all_ok: bool) -> None:
            if not all_ok:
                return
            self._apply_image_lists()
            if len(self.ctx.selected) <= 1:
                QMessageBox.information(
                    self, "构建完成",
                    f"镜像构建完成: {tag}\n已记录为该服务器使用的 UCM 镜像。")
                return
            self._distribute(tag)

        self.panel.run_tasks([(server, fn)], f"构建镜像（构建服务器 {server.name}）",
                             on_finished=on_finished)
