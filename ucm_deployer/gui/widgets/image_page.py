# -*- coding: utf-8 -*-
"""步骤2：镜像构建 —— 在已登录服务器上构建带 UCM 的镜像。

流程：选择基础镜像（docker images / 上传 tar）
      -> 上传 wrapt whl、UCM whl、（可选）ucm-toolkit whl
      -> 校验服务器/镜像/安装包匹配 -> 填新镜像名 -> 构建并校验 UCM 安装。
"""
from __future__ import annotations

import os
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
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.docker_manager import DockerManager
from ...core.image_builder import (ImageBuildConfig, ImageBuilder,
                                   fetch_server_arch, suggest_tag,
                                   upload_image_tar, validate_build_match,
                                   whl_arch, whl_version)
from ...core.models import DeviceType, DockerImage
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

        # ---------- 1. 选择基础镜像
        image_box = QGroupBox("1. 选择基础镜像（服务器上已有镜像）")
        self.placeholder = QLabel("请先在「1. 服务器管理」勾选要登录的服务器")
        self.placeholder.setProperty("role", "hint")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setMinimumHeight(48)

        self.image_combo = QComboBox()
        setup_image_combo(self.image_combo)
        self.image_combo.setMinimumWidth(440)
        self.image_combo.currentTextChanged.connect(self._suggest_tag_if_empty)

        refresh_btn = QPushButton("🔄 刷新镜像列表")
        upload_btn = QPushButton("⬆ 上传镜像 tar/tar.gz")
        check_ucm_btn = QPushButton("✅ 检查所选镜像 UCM")
        refresh_btn.clicked.connect(self._refresh_images)
        upload_btn.clicked.connect(self._upload_tar)
        check_ucm_btn.clicked.connect(self._check_ucm)
        refresh_btn.setToolTip("重新加载服务器上的 docker images")
        upload_btn.setToolTip("把本地镜像 tar 包上传到服务器 docker load（可用于导入基础镜像）")
        check_ucm_btn.setToolTip("检查所选基础镜像内是否已安装 UCM（已装可直接跳过构建）")

        image_layout = QVBoxLayout(image_box)
        image_layout.addWidget(self.placeholder)
        img_form = QFormLayout()
        img_form.addRow("基础镜像", self.image_combo)
        image_layout.addLayout(img_form)
        img_btns = QHBoxLayout()
        img_btns.addWidget(refresh_btn)
        img_btns.addWidget(upload_btn)
        img_btns.addWidget(check_ucm_btn)
        img_btns.addStretch(1)
        image_layout.addLayout(img_btns)

        # ---------- 2. 上传安装包
        build_box = QGroupBox("2. 安装包（上传到服务器，随镜像一起构建）")
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
            "构建在服务器上执行：上传三个包 -> 生成 Dockerfile -> docker build -> 校验 UCM 安装。"
            "若所选基础镜像已安装 UCM，可点「检查所选镜像 UCM」后直接进入步骤3。")
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

    def _selected_base(self) -> Optional[str]:
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
            for r in refs:
                if not any(im.ref == r for im in self._images):
                    self._images.append(image_from_ref(r))

        def on_finished(all_ok: bool) -> None:
            if all_ok:
                self._apply_images()
                QMessageBox.information(self, "完成", "镜像已上传并加载到服务器")

        self.panel.run_tasks([(server, fn)], "上传镜像", on_finished=on_finished)

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
