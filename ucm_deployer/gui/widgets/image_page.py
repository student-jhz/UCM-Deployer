# -*- coding: utf-8 -*-
"""步骤2：镜像构建 —— 在已登录服务器上构建带 UCM 的镜像。

流程：选择基础镜像（docker images / 上传 tar）
      -> 上传 wrapt whl、UCM whl、（可选）ucm-toolkit whl
      -> 校验服务器/镜像/安装包匹配 -> 填新镜像名 -> 构建并校验 UCM 安装。
"""
from __future__ import annotations

import os
import shlex
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.docker_manager import (DockerManager, platform_from_arch,
                                    validate_image_ref)
from ...core.image_builder import (ImageBuildConfig, ImageBuilder,
                                   fetch_server_arch, suggest_tag,
                                   upload_image_tar, validate_build_match,
                                   whl_arch, whl_version)
from ...core.models import DeviceType, DockerImage
from ...core.shell import shq
from ..state import AppContext
from .common import (ParallelTaskPanel, QuickTask, combo_ref, fill_image_combo,
                     image_from_ref, setup_image_combo)


class ImagePage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._images: List[DockerImage] = []
        self._loaded_sid: Optional[str] = None
        self._quick_tasks = []      # 运行中的 QuickTask 引用（防线程被回收）
        self._tag_dirty = False     # 用户手动编辑过新镜像名（停止自动建议）
        self._updating_tag = False  # 程序设置 tag 中（不算用户编辑）
        self._pull_command_dirty = False
        self._updating_pull_command = False

        # ---------- 1. 准备基础镜像
        image_box = QGroupBox("1. 准备基础镜像")
        self.placeholder = QLabel("请先在「1. 服务器管理」勾选要登录的服务器")
        self.placeholder.setProperty("role", "hint")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setMinimumHeight(48)

        self.image_combo = QComboBox()
        setup_image_combo(self.image_combo)
        self.image_combo.setMinimumWidth(440)
        self.image_combo.currentTextChanged.connect(self._suggest_tag_if_empty)

        self.source_combo = QComboBox()
        self.source_combo.addItem("服务器已有镜像")
        self.source_combo.addItem("本地镜像包（离线导入）")
        self.source_combo.addItem("镜像仓库（服务器拉取）")
        self.source_combo.currentIndexChanged.connect(
            lambda *_: self._update_source_hint())

        self.source_stack = QStackedWidget()
        self.source_combo.currentIndexChanged.connect(
            self.source_stack.setCurrentIndex)

        existing_page = QWidget()
        existing_form = QFormLayout(existing_page)
        existing_form.addRow("基础镜像", self.image_combo)
        refresh_btn = QPushButton("🔄 刷新镜像列表")
        existing_btns = QHBoxLayout()
        existing_btns.addWidget(refresh_btn)
        existing_btns.addStretch(1)
        existing_form.addRow("", existing_btns)
        self.source_stack.addWidget(existing_page)

        tar_page = QWidget()
        tar_form = QFormLayout(tar_page)
        self.tar_path_edit = QLineEdit()
        self.tar_path_edit.setPlaceholderText(
            "选择本地镜像 tar / tar.gz / tgz 文件；仅在服务器无法访问仓库时使用")
        browse_tar_btn = QPushButton("浏览…")
        upload_btn = QPushButton("导入到服务器")
        browse_tar_btn.clicked.connect(
            lambda: self._pick_file(self.tar_path_edit, "镜像包 (*.tar *.tar.gz *.tgz)"))
        upload_btn.clicked.connect(self._upload_tar)
        tar_btns = QHBoxLayout()
        tar_btns.addWidget(browse_tar_btn)
        tar_btns.addWidget(upload_btn)
        tar_btns.addStretch(1)
        tar_form.addRow("镜像包", self.tar_path_edit)
        tar_form.addRow("", tar_btns)
        self.source_stack.addWidget(tar_page)

        registry_page = QWidget()
        registry_form = QFormLayout(registry_page)
        self.registry_ref_edit = QLineEdit()
        self.registry_ref_edit.setPlaceholderText(
            "quay.io/ascend/vllm-ascend:v0.30.0rc2-a5-openeuler")
        self.registry_ref_edit.setMinimumWidth(520)
        self.pull_command_edit = QPlainTextEdit()
        self.pull_command_edit.setPlaceholderText(
            "输入镜像地址后自动生成 docker pull 命令；可在服务器上执行前修改")
        self.pull_command_edit.setMinimumHeight(76)
        self.pull_command_edit.setToolTip(
            "命令将在目标服务器上执行；执行前可编辑，但必须以 docker pull 开头")
        pull_btn = QPushButton("在服务器上拉取")
        pull_btn.clicked.connect(self._pull_registry_image)
        registry_btns = QHBoxLayout()
        registry_btns.addWidget(pull_btn)
        registry_btns.addStretch(1)
        registry_form.addRow("镜像地址", self.registry_ref_edit)
        registry_form.addRow("", registry_btns)
        registry_form.addRow("服务器命令（可编辑）", self.pull_command_edit)
        self.source_stack.addWidget(registry_page)
        self.registry_ref_edit.textChanged.connect(self._update_pull_command)
        self.pull_command_edit.textChanged.connect(self._mark_pull_command_dirty)

        check_ucm_btn = QPushButton("✅ 检查所选镜像 UCM")
        check_ucm_btn.clicked.connect(self._check_ucm)
        refresh_btn.setToolTip("重新加载服务器上的 docker images")
        upload_btn.setToolTip("上传本地镜像包到服务器并执行 docker load")
        pull_btn.setToolTip(
            "在目标服务器上直接执行 docker pull；本地无需安装 Docker Desktop")
        check_ucm_btn.setToolTip("检查所选基础镜像内是否已安装 UCM（已装可直接跳过构建）")

        image_layout = QVBoxLayout(image_box)
        image_layout.addWidget(self.placeholder)
        source_form = QFormLayout()
        source_form.addRow("镜像来源", self.source_combo)
        image_layout.addLayout(source_form)
        image_layout.addWidget(self.source_stack)
        self.source_hint = QLabel()
        self.source_hint.setWordWrap(True)
        image_layout.addWidget(self.source_hint)
        source_btns = QHBoxLayout()
        source_btns.addWidget(check_ucm_btn)
        source_btns.addStretch(1)
        image_layout.addLayout(source_btns)
        self._update_source_hint()
        self._update_pull_command()

        # ---------- 2. 上传安装包
        build_box = QGroupBox("2. 安装包（仅在需要构建新 UCM 镜像时填写）")
        self.wrapt_whl_edit = QLineEdit()
        self.ucm_whl_edit = QLineEdit()
        self.toolkit_edit = QLineEdit()
        for w in (self.wrapt_whl_edit, self.ucm_whl_edit, self.toolkit_edit):
            w.setPlaceholderText("本地文件路径")
        wrapt_btn = QPushButton("浏览...")
        ucm_btn = QPushButton("浏览...")
        toolkit_btn = QPushButton("浏览...")
        wrapt_btn.clicked.connect(lambda: self._pick_file(self.wrapt_whl_edit, "wrapt whl (*.whl)"))
        ucm_btn.clicked.connect(lambda: self._pick_file(self.ucm_whl_edit, "UCM whl (*.whl)"))
        toolkit_btn.clicked.connect(
            lambda: self._pick_file(self.toolkit_edit, "ucm-toolkit whl (*.whl)"))

        # 在线下载提示（本地没有包时指引获取途径，链接可点击直达浏览器）
        self.ucm_dl_hint = QLabel(
            '⬇ 本地没有 UCM 包？<a href="https://github.com/ModelEngine-Group/'
            'unified-cache-management/releases">从 GitHub Releases 在线下载'
            ' uc_manager-*.whl</a>')
        self.wrapt_dl_hint = QLabel(
            '⬇ 离线依赖 wrapt：<a href="https://pypi.org/project/wrapt/1.17.2/#files">'
            '从 PyPI (wrapt 1.17.2) 文件列表在线下载</a>'
            '（选择匹配服务器 Python 版本与 CPU 架构的 whl，'
            '如 manylinux 的 x86_64 / aarch64）')
        self.toolkit_dl_hint = QLabel(
            '⬇ 本地没有 ucm-toolkit 包？<a href="https://github.com/ModelEngine-Group/'
            'unified-cache-management/releases">从 GitHub Releases 在线下载</a>'
            '（与 UCM 包同站点，pip install 安装）')
        for hint in (self.ucm_dl_hint, self.wrapt_dl_hint, self.toolkit_dl_hint):
            hint.setProperty("role", "hint")
            hint.setOpenExternalLinks(True)
        self.ucm_dl_hint.setToolTip(
            "https://github.com/ModelEngine-Group/unified-cache-management/releases")
        self.wrapt_dl_hint.setToolTip("https://pypi.org/project/wrapt/1.17.2/#files")
        self.toolkit_dl_hint.setToolTip(
            "https://github.com/ModelEngine-Group/unified-cache-management/releases")

        # whl 选择即时反馈：文件有效性 + 架构标记解析（帮助确认与服务器匹配）
        self.wrapt_fb = QLabel("")
        self.ucm_fb = QLabel("")
        self.toolkit_fb = QLabel("")
        for fb in (self.wrapt_fb, self.ucm_fb, self.toolkit_fb):
            fb.setProperty("role", "hint")
        self.wrapt_whl_edit.textChanged.connect(
            lambda t: self._update_whl_feedback(self.wrapt_fb, t))
        self.ucm_whl_edit.textChanged.connect(
            lambda t: (self._update_whl_feedback(self.ucm_fb, t),
                       self._suggest_tag_if_empty()))   # UCM 版本号影响建议名
        self.toolkit_edit.textChanged.connect(
            lambda t: self._update_whl_feedback(self.toolkit_fb, t))

        form = QFormLayout(build_box)
        row_w = QHBoxLayout()
        row_w.addWidget(self.wrapt_whl_edit, 1)
        row_w.addWidget(wrapt_btn)
        row_u = QHBoxLayout()
        row_u.addWidget(self.ucm_whl_edit, 1)
        row_u.addWidget(ucm_btn)
        row_t = QHBoxLayout()
        row_t.addWidget(self.toolkit_edit, 1)
        row_t.addWidget(toolkit_btn)
        form.addRow("wrapt whl 包*", row_w)
        form.addRow("", self.wrapt_fb)
        form.addRow("", self.wrapt_dl_hint)
        form.addRow("UCM whl 包*", row_u)
        form.addRow("", self.ucm_fb)
        form.addRow("", self.ucm_dl_hint)
        form.addRow("ucm-toolkit whl 包（可选）", row_t)
        form.addRow("", self.toolkit_fb)
        form.addRow("", self.toolkit_dl_hint)

        # ---------- 3. 新镜像名
        name_box = QGroupBox("3. 新镜像名")
        self.tag_edit = QLineEdit()
        self.tag_edit.setPlaceholderText("默认自动生成：<基础镜像名>:ucm-<UCM版本号>，可修改")
        self.tag_edit.textChanged.connect(self._on_tag_edited)
        self.platform_label = QLabel("-（进入本页后按服务器设备自动确定）")
        name_form = QFormLayout(name_box)
        name_form.addRow("新镜像名", self.tag_edit)
        name_form.addRow("构建平台(ENV PLATFORM)", self.platform_label)

        # ---------- 操作
        self.match_label = QLabel("匹配校验：未校验（构建前会自动强制校验）")
        self.match_label.setProperty("role", "hint")
        self.match_label.setWordWrap(True)
        match_btn = QPushButton("🔍 校验匹配（服务器/镜像/安装包）")
        build_btn = QPushButton("🔨 开始构建")
        build_btn.setProperty("accent", True)
        match_btn.clicked.connect(self._check_match)
        build_btn.clicked.connect(self._build)

        self.hint = QLabel(
            "若基础镜像已安装 UCM，无需填写安装包或构建：点「检查所选镜像 UCM」确认后直接进入步骤3。\n"
            "仅在确需制作新 UCM 镜像时填写下方安装包；构建会执行：上传三个包 -> "
            "生成 Dockerfile -> docker build -> 校验 UCM 安装。")
        self.hint.setWordWrap(True)

        self.panel = ParallelTaskPanel()

        layout = QVBoxLayout(self)
        layout.addWidget(image_box)
        layout.addWidget(build_box)
        layout.addWidget(name_box)
        layout.addWidget(self.match_label)
        btns = QHBoxLayout()
        btns.addWidget(match_btn)
        btns.addWidget(build_btn)
        btns.addStretch(1)
        layout.addLayout(btns)
        layout.addWidget(self.hint)
        layout.addWidget(self.panel, 2)

    # ------------------------------------------------------------ 初始化
    def on_enter(self) -> None:
        """进入本页：加载服务器镜像列表（有缓存则回显）。"""
        server = self.ctx.server
        self.placeholder.setVisible(server is None)
        self._update_platform_label()
        if server is None:
            return
        if self._loaded_sid != server.id:
            self._refresh_images()
        else:
            self._apply_images()

    def _update_platform_label(self) -> None:
        server = self.ctx.server
        dev = self.ctx.device_of(server) if server else None
        if dev is not None and dev.device_type == DeviceType.ASCEND:
            self.platform_label.setText("ascend（服务器设备：Ascend NPU）")
        elif dev is not None and dev.device_type == DeviceType.NVIDIA:
            self.platform_label.setText("cuda（服务器设备：NVIDIA GPU）")
        else:
            self.platform_label.setText("-（未探测设备，默认按 ascend；建议先在步骤1探测）")

    def _update_source_hint(self) -> None:
        hints = {
            0: "已选择服务器已有镜像：只需从列表选择并检查 UCM，无需填写仓库地址或上传镜像包。",
            1: "离线导入：选择本地 tar/tar.gz 镜像包，上传到服务器并 docker load；适合服务器无法访问镜像仓库。",
            2: "服务器直连拉取：在目标服务器上执行 docker pull，本地不需要安装 Docker Desktop。",
        }
        self.source_hint.setText(hints.get(self.source_combo.currentIndex(), ""))

    def _update_pull_command(self) -> None:
        if self._updating_pull_command:
            return
        server = self.ctx.server
        ref = self.registry_ref_edit.text().strip()
        if not ref:
            self._set_pull_command("")
            return
        platform = platform_from_arch(self.ctx.device_of(server).arch) if server else ""
        command = "docker pull"
        if platform:
            command += f" --platform {shq(platform)}"
        command += f" {shq(ref)}"
        self._set_pull_command(command)

    def _set_pull_command(self, command: str) -> None:
        self._updating_pull_command = True
        try:
            self.pull_command_edit.setPlainText(command)
            self._pull_command_dirty = False
        finally:
            self._updating_pull_command = False

    def _mark_pull_command_dirty(self) -> None:
        if not self._updating_pull_command:
            self._pull_command_dirty = True

    def _select_source(self, index: int) -> None:
        self.source_combo.setCurrentIndex(index)

    def _selected_base(self) -> Optional[str]:
        if self.source_combo.currentIndex() != 0:
            QMessageBox.information(
                self, "请先完成镜像准备",
                "请先导入本地镜像包或在服务器上拉取镜像。\n"
                "操作成功后会自动切换回“服务器已有镜像”，再检查 UCM。")
            return None
        ref = combo_ref(self.image_combo)
        if not ref:
            QMessageBox.warning(
                self, "参数错误",
                "未选择基础镜像。\n"
                "镜像列表来自服务器 docker images（进入本页自动加载，"
                "也可点「刷新镜像列表」），只能从列表中选择。")
            return None
        return ref

    def _suggest_tag_if_empty(self, *_):
        """镜像/UCM whl 变化时刷新建议名；用户手动编辑过则不覆盖。"""
        if self._tag_dirty:
            return
        ref = combo_ref(self.image_combo)
        if not ref:
            return
        ucm_ver = whl_version(self.ucm_whl_edit.text().strip())
        self._updating_tag = True
        self.tag_edit.setText(suggest_tag(ref, ucm_ver))
        self._updating_tag = False

    def _on_tag_edited(self, text: str):
        """区分用户编辑与程序设置：用户改过即停止自动建议。"""
        if not self._updating_tag and text.strip():
            self._tag_dirty = True

    def _update_whl_feedback(self, label: QLabel, text: str) -> None:
        """whl 路径即时反馈：文件存在性 + 架构标记解析。"""
        path = text.strip()
        if not path:
            label.setText("")
            return
        name = os.path.basename(path)
        if not os.path.isfile(path):
            label.setText(f"⚠ 文件不存在: {name}")
            return
        arch = whl_arch(path)
        if arch:
            label.setText(f"✓ 文件有效 · 架构标记 {arch}")
        else:
            label.setText("✓ 文件有效 · 纯 Python 包（跨架构）")

    def _pick_file(self, edit: QLineEdit, flt: str) -> None:
        from PySide6.QtCore import QSettings

        settings = QSettings("UCM-Deployer", "paths")
        start = str(settings.value("last_file_dir", ""))
        path, _ = QFileDialog.getOpenFileName(self, "选择文件", start, flt)
        if path:
            edit.setText(path)
            settings.setValue("last_file_dir", os.path.dirname(path))

    # ------------------------------------------------------------ 后台快任务
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

    # ------------------------------------------------------------ 镜像操作
    def _refresh_images(self) -> None:
        server = self.ctx.server
        if server is None:
            QMessageBox.information(self, "提示", "请先在步骤1勾选要登录的服务器")
            return

        def fn(ssh, tctx):
            tctx.progress(40, "获取镜像列表")
            dm = DockerManager(ssh)
            if not dm.check_docker():
                raise RuntimeError("docker 不可用")
            images = dm.list_images()
            self._images = images
            tctx.log(f"共 {len(images)} 个镜像：")
            for im in images:
                mark = " [疑似UCM]" if "ucm" in im.ref.lower() else ""
                tctx.log(f"  {im.ref}    {im.size}{mark}")
            tctx.progress(100, f"{len(images)} 个镜像")

        def on_finished(all_ok: bool) -> None:
            if all_ok:
                self._loaded_sid = server.id
                self._apply_images()
            else:
                fill_image_combo(self.image_combo, [])  # 显示加载失败占位提示

        self.panel.run_tasks([(server, fn)], "刷新镜像列表", on_finished=on_finished)

    def _apply_images(self) -> None:
        server = self.ctx.server
        current = self.ctx.images.get(server.id, "") if server else ""
        fill_image_combo(self.image_combo, self._images, current=current)
        self._suggest_tag_if_empty()

    def _upload_tar(self) -> None:
        server = self.ctx.server
        if server is None:
            QMessageBox.information(self, "提示", "请先在步骤1勾选要登录的服务器")
            return
        path = self.tar_path_edit.text().strip()
        if not path or not os.path.isfile(path):
            QMessageBox.warning(self, "参数错误", "请先选择有效的镜像 tar/tar.gz 文件")
            return

        def fn(ssh, tctx):
            dm = DockerManager(ssh)
            refs = upload_image_tar(
                ssh, dm, path,
                progress=lambda p, m: tctx.progress(p, m),
                log=tctx.log)
            if not refs:
                raise RuntimeError("docker load 未解析出镜像名")
            tctx.log("已加载: " + ", ".join(refs))
            self.ctx.images[server.id] = refs[0]
            for r in refs:
                if not any(im.ref == r for im in self._images):
                    self._images.append(image_from_ref(r))

        def on_finished(all_ok: bool) -> None:
            if all_ok:
                self._apply_images()
                self._select_source(0)
                QMessageBox.information(
                    self, "完成", "镜像已导入服务器，并已切换为“服务器已有镜像”。")

        self.panel.run_tasks([(server, fn)], "上传镜像", on_finished=on_finished)

    def _pull_registry_image(self) -> None:
        server = self.ctx.server
        if server is None:
            QMessageBox.information(self, "提示", "请先在步骤1勾选要登录的服务器")
            return
        try:
            ref = validate_image_ref(self.registry_ref_edit.text())
        except Exception as exc:
            QMessageBox.warning(self, "参数错误", str(exc))
            return
        self.registry_ref_edit.setText(ref)
        device = self.ctx.device_of(server)
        if not self._pull_command_dirty:
            self._update_pull_command()
        command = self.pull_command_edit.toPlainText().strip()
        try:
            command_tokens = shlex.split(command)
        except ValueError:
            command_tokens = []
        if (len(command_tokens) < 2
                or command_tokens[:2] != ["docker", "pull"]):
            QMessageBox.warning(
                self, "参数错误", "服务器拉取命令不能为空，且必须以 docker pull 开头")
            return
        command = " ".join(shq(token) for token in command_tokens)

        def fn(ssh, tctx):
            arch = device.arch or fetch_server_arch(ssh)
            platform = platform_from_arch(arch)
            manager = DockerManager(ssh)
            if not manager.check_docker():
                raise RuntimeError("目标服务器 Docker 不可用")
            tctx.log("$ " + command)
            tctx.progress(
                5, f"在服务器上拉取 {ref}"
                + (f"（{platform}）" if platform else "（服务器本机架构）"))
            result = ssh.exec_stream(command, on_line=tctx.log, timeout=7200)
            if not result.ok:
                raise RuntimeError(
                    f"服务器 docker pull 失败(退出码 {result.exit_code}): "
                    f"{(result.stderr or result.stdout).strip()[:400]}")
            if not manager.image_exists(ref):
                raise RuntimeError(f"docker pull 成功但未找到镜像 {ref}")
            tctx.progress(90, "确认服务器镜像列表")
            self.ctx.images[server.id] = ref
            if not any(im.ref == ref for im in self._images):
                self._images.append(image_from_ref(ref))
            tctx.progress(100, f"镜像已拉取到服务器: {ref}")

        def on_finished(all_ok: bool) -> None:
            if not all_ok:
                return
            self._apply_images()
            self._select_source(0)
            self._pull_command_dirty = False
            QMessageBox.information(
                self, "完成",
                f"镜像 {ref} 已在目标服务器上拉取完成。\n"
                "已切换为“服务器已有镜像”，可继续检查 UCM。")

        self.panel.run_tasks(
            [(server, fn)], f"服务器拉取镜像（{server.name}）",
            on_finished=on_finished)

    def _check_ucm(self) -> None:
        server = self.ctx.server
        if server is None:
            QMessageBox.information(self, "提示", "请先在步骤1勾选要登录的服务器")
            return
        base = self._selected_base()
        if base is None:
            return

        def fn(ssh, tctx):
            tctx.progress(50, f"检查 {base}")
            ucm = DockerManager(ssh).image_ucm_info(base)
            tctx.log(f"{base}: {ucm}")
            tctx.progress(100, str(ucm))
            if ucm.installed:
                self.ctx.images[server.id] = base

        def on_finished(all_ok: bool) -> None:
            if all_ok and server.id in self.ctx.images:
                QMessageBox.information(
                    self, "已记录",
                    f"镜像 {base} 已安装 UCM，已记录为本服务器使用的镜像，"
                    "可直接进入步骤3创建容器。")

        self.panel.run_tasks([(server, fn)], "检查镜像 UCM",
                             on_finished=on_finished)

    # ------------------------------------------------------------ 校验/构建
    def _local_args(self):
        """收集并本地校验输入；返回 (server, base, ucm, wrapt, toolkit) 或 None。"""
        server = self.ctx.server
        if server is None:
            QMessageBox.information(self, "提示", "请先在步骤1勾选要登录的服务器")
            return None
        base = self._selected_base()
        if base is None:
            return None
        ucm_whl = self.ucm_whl_edit.text().strip()
        if not ucm_whl or not os.path.isfile(ucm_whl):
            QMessageBox.warning(self, "参数错误", "请选择有效的 UCM whl 文件")
            return None
        wrapt_whl = self.wrapt_whl_edit.text().strip()
        if not wrapt_whl or not os.path.isfile(wrapt_whl):
            QMessageBox.warning(self, "参数错误", "请选择有效的 wrapt whl 文件")
            return None
        toolkit = self.toolkit_edit.text().strip()
        if toolkit and not os.path.isfile(toolkit):
            QMessageBox.warning(self, "参数错误", "ucm-toolkit whl 文件路径无效")
            return None
        return server, base, ucm_whl, wrapt_whl, toolkit

    def _check_match(self) -> None:
        args = self._local_args()
        if args is None:
            return
        server, base, ucm_whl, wrapt_whl, _toolkit = args
        dev = self.ctx.device_of(server)
        self.match_label.setText("匹配校验：正在校验（获取服务器架构 + 比对）...")

        def fn():
            from ...core.ssh_client import SSHClient
            with SSHClient(server) as ssh:
                arch = fetch_server_arch(ssh)
                return validate_build_match(dev, arch, base, ucm_whl, wrapt_whl)

        def on_done(result, error):
            if error is not None:
                self.match_label.setText(f"匹配校验：校验失败（{error}）")
                return
            if result.ok:
                self.match_label.setText(f"✓ 匹配校验：{result.message}"
                                         + ("".join(f"\n⚠ {w}" for w in result.warnings)))
            else:
                self.match_label.setText("✗ 匹配校验：" + result.message)
                QMessageBox.warning(self, "校验不通过", result.message)

        self._run_quick(fn, on_done)

    def _build(self) -> None:
        args = self._local_args()
        if args is None:
            return
        server, base, ucm_whl, wrapt_whl, toolkit = args
        tag = (self.tag_edit.text().strip()
               or suggest_tag(base, whl_version(ucm_whl)))
        dev = self.ctx.device_of(server)
        platform = "cuda" if dev.device_type == DeviceType.NVIDIA else "ascend"

        def fn(ssh, tctx):
            tctx.progress(3, "构建前校验匹配（服务器/镜像/安装包）")
            arch = fetch_server_arch(ssh)
            match = validate_build_match(dev, arch, base, ucm_whl, wrapt_whl)
            for w in match.warnings:
                tctx.log("[警告] " + w)
            if not match.ok:
                raise RuntimeError("匹配校验不通过: " + match.message)

            cfg = ImageBuildConfig(
                base_image=base, image_tag=tag,
                ucm_whl_local=ucm_whl, wrapt_whl_local=wrapt_whl,
                toolkit_whl_local=toolkit,
                platform=platform)

            result = ImageBuilder(ssh, DockerManager(ssh)).build(
                cfg, progress=tctx.progress, log=tctx.log,
                cancelled=tctx.cancelled)
            tctx.log(f"构建完成: {result.image}"
                     f" (UCM {result.ucm.version if result.ucm else '?'})")
            self.ctx.images[server.id] = result.image
            if not any(im.ref == result.image for im in self._images):
                self._images.append(image_from_ref(result.image))

        def on_finished(all_ok: bool) -> None:
            if not all_ok:
                return
            self._apply_images()
            QMessageBox.information(
                self, "构建完成",
                f"镜像构建完成: {tag}\n已记录为本服务器使用的 UCM 镜像，"
                "可进入步骤3创建容器。")

        self.panel.run_tasks([(server, fn)], f"构建镜像（{server.name}）",
                             on_finished=on_finished)
