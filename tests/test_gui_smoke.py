# -*- coding: utf-8 -*-
"""GUI 冒烟测试（offscreen 模式，无显示器环境可跑）。

覆盖：三步导航守卫、服务器单选、镜像下拉筛选、安装包下载链接、
whl 选择即时反馈、docker run 命令生成（NPU/GPU 模板）、界面无黑块。
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")


def _automate_message_boxes() -> None:
    """offscreen 下模态对话框会永久阻塞，自动应答。"""
    from PySide6.QtWidgets import QMessageBox

    for name in ("information", "warning", "critical", "about"):
        setattr(QMessageBox, name,
                staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    QMessageBox.question = staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.Yes)
    QMessageBox.exec = lambda self, *a, **k: QMessageBox.StandardButton.Ok  # noqa: A001


_automate_message_boxes()

# 不可达地址使用 127.0.0.1:1（连接立即被拒），避免测试遗留连接超时线程
UNREACHABLE_HOST, UNREACHABLE_PORT = "127.0.0.1", 1


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _shutdown_tasks_after_test(qapp):
    """每个测试结束后等待/关闭所有后台任务线程，防止运行中线程被回收。"""
    yield
    from PySide6.QtWidgets import QApplication

    from ucm_deployer.gui.widgets.common import ParallelTaskPanel, _task_threads
    for w in QApplication.topLevelWidgets():
        for panel in w.findChildren(ParallelTaskPanel):
            panel.shutdown()
    for t in list(_task_threads):
        t.join(15.0)
    qapp.processEvents()


def _wait_panel(panel, qapp, timeout=60):
    import time

    deadline = time.time() + timeout
    while panel.is_running() and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.05)
    qapp.processEvents()
    return not panel.is_running()


# ============================================================ 主窗口
def test_main_window_pages(qapp):
    from ucm_deployer.gui.main_window import MainWindow

    win = MainWindow()
    win.show()
    qapp.processEvents()
    for i in range(4):
        win.goto_step(i)
        qapp.processEvents()
    # 自由跳转：无前置校验，页面跟随到最后一步
    assert win.pages.currentIndex() == 3
    win.close()


def test_nav_is_flat_buttons(qapp):
    from ucm_deployer.gui.main_window import MainWindow

    win = MainWindow()
    qapp.processEvents()
    assert len(win._nav_buttons) == 4
    assert win.nav_group.exclusive()
    for i, btn in enumerate(win._nav_buttons):
        assert btn.objectName() == "navBtn"
        assert btn.text().startswith(f"步骤{i + 1}：")
        assert btn.isCheckable()
    assert win._nav_buttons[0].isChecked()
    win.goto_step(0)
    assert win._nav_buttons[0].isChecked()
    win.close()


def test_nav_free_jump(qapp, tmp_path):
    """自由跳转：无服务器/无镜像也可进入任意步骤（操作时才拦截）。"""
    from ucm_deployer.core.models import ServerInfo
    from ucm_deployer.gui.main_window import MainWindow

    s = ServerInfo.create(name="n", host=UNREACHABLE_HOST, port=UNREACHABLE_PORT)
    win = MainWindow()
    win.show()
    qapp.processEvents()

    # 无服务器 -> 步骤2/3 均可直接进入
    win.goto_step(2)
    qapp.processEvents()
    assert win.current_step() == 2
    assert win.pages.currentIndex() == 2
    win.goto_step(1)
    qapp.processEvents()
    assert win.current_step() == 1
    win.goto_step(0)
    qapp.processEvents()
    assert win.current_step() == 0

    # 有服务器无镜像 -> 步骤3 也可进入
    win.ctx.selected = [s]
    win.goto_step(2)
    qapp.processEvents()
    assert win.current_step() == 2
    win.close()


def test_container_page_intercepts_without_server(qapp, tmp_path):
    """操作时拦截：未登录服务器时创建容器/刷新镜像被拦截且状态不变。"""
    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.gui.state import AppContext
    from ucm_deployer.gui.widgets.container_page import ContainerPage

    ctx = AppContext(ServerRegistry(tmp_path))
    page = ContainerPage(ctx)
    page.on_enter()
    qapp.processEvents()
    # 未登录：进入页面提示状态、下拉为占位
    assert "未登录服务器" in page.ucm_check_label.text()

    page._create()          # 弹提示（自动应答）后返回，不创建
    page._refresh_images()
    qapp.processEvents()
    assert ctx.containers == {}
    assert page._images == []


def test_container_page_fits_one_screen(qapp):
    """排版回归：步骤3 一屏可见（无滚动容器，关键区域获得合理布局尺寸）。"""
    from ucm_deployer.gui.main_window import MainWindow

    win = MainWindow()
    win.resize(1280, 860)
    win.show()
    qapp.processEvents()
    win.goto_step(2)
    qapp.processEvents()

    page = win.container_page
    # 布局最小需求须适配窗口可用高度（超出则内容被裁剪）
    available = win.centralWidget().height()
    assert page.minimumSizeHint().height() <= available, \
        f"步骤3 最小高度 {page.minimumSizeHint().height()}px 超过可用 {available}px"
    # 布局实际分配后：命令编辑框/挂载表格/任务面板均获得可用尺寸（未被压没）
    assert page.cmd_edit.height() >= 100, f"命令框被压缩: {page.cmd_edit.height()}px"
    assert page.extra_table.height() >= 60, f"自定义挂载表格被压缩: {page.extra_table.height()}px"
    assert page.panel.height() >= 100, f"任务面板被压缩: {page.panel.height()}px"
    win.close()


def test_status_bar_summary(qapp, tmp_path):
    from ucm_deployer.core.models import ServerInfo
    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.gui.main_window import MainWindow

    win = MainWindow()
    win._update_status()
    assert "未登录" in win.statusBar().currentMessage()
    s = ServerInfo.create(name="srv1", host="10.0.0.1")
    win.ctx.selected = [s]
    win._update_status()
    assert "srv1" in win.statusBar().currentMessage()
    assert "未确定" in win.statusBar().currentMessage()
    win.ctx.images = {s.id: "img:t"}
    win._update_status()
    assert "已确定" in win.statusBar().currentMessage()
    win.close()


def test_main_window_no_black_areas(qapp):
    """回归测试：布局间隙不得出现未绘制黑色（QSS 规则顺序 bug 曾致窗口大片黑色）。"""
    from ucm_deployer.gui.main_window import MainWindow
    from ucm_deployer.gui.theme import apply_light_theme

    apply_light_theme(qapp)
    win = MainWindow()
    win.resize(1320, 880)
    win.show()
    qapp.processEvents()
    img = win.grab().toImage()

    points = [
        (6, 6, "窗口左上边距"),
        (660, 6, "窗口顶边距"),
        (30, 440, "导航下方留白"),
        (660, 874, "窗口底边距"),
        (1314, 440, "窗口右边距"),
    ]
    blacks = []
    for x, y, label in points:
        c = img.pixelColor(x, y)
        hexs = f"#{c.red():02x}{c.green():02x}{c.blue():02x}"
        if c.lightness() < 100:
            blacks.append(f"{label}({x},{y})={hexs}")
    assert not blacks, "存在未绘制黑色区域: " + ", ".join(blacks)

    c = img.pixelColor(30, 440)
    assert c.red() > 230 and c.green() > 230 and c.blue() > 230, \
        f"留白区非浅色: #{c.red():02x}{c.green():02x}{c.blue():02x}"
    win.close()


# ============================================================ 步骤1：服务器单选
def test_server_page_single_selection(qapp, tmp_path):
    from PySide6.QtCore import Qt

    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.gui.state import AppContext
    from ucm_deployer.gui.widgets.server_page import ServerPage

    ctx = AppContext(ServerRegistry(tmp_path))
    ctx.registry.upsert(_mk_server(ctx, "node1", "10.0.0.1"))
    ctx.registry.upsert(_mk_server(ctx, "node2", "10.0.0.2"))
    page = ServerPage(ctx)
    # 回归：构造后（打开程序）即显示历史服务器，无需先添加一条
    assert page.table.rowCount() == 2

    page.table.item(0, 0).setCheckState(Qt.Checked)
    qapp.processEvents()
    assert len(ctx.selected) == 1
    assert ctx.selected[0].name == "node1"

    # 再勾选第二台 -> 单选：第一台自动取消
    page.table.item(1, 0).setCheckState(Qt.Checked)
    qapp.processEvents()
    assert len(ctx.selected) == 1
    assert ctx.selected[0].name == "node2"
    assert ctx.server.name == "node2"


def test_server_page_actions_target_checked(qapp, tmp_path):
    """回归：编辑/删除作用于勾选的服务器（不再用 currentRow，避免焦点转移后残留）。"""
    from PySide6.QtCore import Qt

    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.gui.state import AppContext
    from ucm_deployer.gui.widgets.server_page import ServerPage

    ctx = AppContext(ServerRegistry(tmp_path))
    s1 = _mk_server(ctx, "node1", "10.0.0.1")
    _mk_server(ctx, "node2", "10.0.0.2")
    page = ServerPage(ctx)

    # 未勾选时：操作对象为 None（各操作弹提示，不误用旧行）
    assert page._current_server() is None

    # 勾选 node1 后：即使焦点/选中行在其他位置，操作对象仍是勾选的 node1
    page.table.item(0, 0).setCheckState(Qt.Checked)
    qapp.processEvents()
    page.table.setCurrentCell(1, 2)      # 选中行在 node2（焦点漂移）
    qapp.processEvents()
    assert page._current_server().id == s1.id

    # 删除作用于勾选的 node1（而非选中行的 node2）
    page._delete()
    qapp.processEvents()
    names = [s.name for s in ctx.registry.list_servers()]
    assert names == ["node2"], "删除应作用于勾选的服务器而非选中行"
    assert ctx.selected == []


def test_server_page_add_auto_checks(qapp, tmp_path):
    """回归：添加服务器后自动勾选登录。"""
    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.gui.state import AppContext
    from ucm_deployer.gui.widgets.server_page import ServerEditDialog, ServerPage

    ctx = AppContext(ServerRegistry(tmp_path))
    page = ServerPage(ctx)

    orig_exec = ServerEditDialog.exec
    ServerEditDialog.exec = lambda self, *a, **k: ServerEditDialog.Accepted
    try:
        page._add()
    finally:
        ServerEditDialog.exec = orig_exec
    qapp.processEvents()
    assert page.table.rowCount() == 1
    assert len(ctx.selected) == 1, "新增后应自动勾选登录"
    assert ctx.server is not None


def _mk_server(ctx, name, host):
    from ucm_deployer.core.models import ServerInfo

    info = ServerInfo.create(name=name, host=host, password="pw")
    ctx.registry.upsert(info)
    return info


# ============================================================ 易用性：镜像筛选 + 下载链接
def test_image_combo_filter(qapp):
    """镜像下拉筛选：输入关键字提交后选中首个匹配项；未匹配回退当前选择。"""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QComboBox

    from ucm_deployer.core.models import DockerImage
    from ucm_deployer.gui.widgets.common import (combo_ref, fill_image_combo,
                                                 setup_image_combo)

    combo = QComboBox()
    setup_image_combo(combo)
    fill_image_combo(combo, [
        DockerImage("quay.io/ascend/vllm-ascend", "v0.23.0-a3", "id1", "18.2GB"),
        DockerImage("ucm-vllm", "v0.1", "id2", "5GB"),
        DockerImage("nginx", "latest", "id3", "100MB"),
    ])
    # 可编辑筛选 + 弹层限高 + 补全器包含匹配（不区分大小写）
    assert combo.isEditable() and combo.maxVisibleItems() == 10
    assert combo.insertPolicy() == QComboBox.InsertPolicy.NoInsert
    assert combo.completer() is not None
    assert combo.completer().filterMode() == Qt.MatchContains

    # 输入关键字结束编辑 -> 选中首个包含关键字的镜像
    combo.lineEdit().setText("nginx")
    combo.lineEdit().editingFinished.emit()
    assert combo_ref(combo) == "nginx:latest"

    # 输入未匹配文本 -> 回退为当前选择（不可自由输入）
    combo.lineEdit().setText("no-such-image")
    combo.lineEdit().editingFinished.emit()
    assert combo_ref(combo) == "nginx:latest"
    assert combo.currentText().startswith("nginx:latest")

    # 补全弹层选中项 -> 同步当前选择
    combo.completer().activated.emit(combo.itemText(0))
    assert combo_ref(combo) == "ucm-vllm:v0.1"


def test_theme_popup_contrast_rules(qapp):
    """回归：下拉弹层/补全列表必须有显式浅色样式（默认黑底黑字不可读）。"""
    from ucm_deployer.gui.theme import QSS

    assert "QComboBox QAbstractItemView" in QSS
    assert "#ffffff" in QSS.split("QComboBox QAbstractItemView")[1].split("}")[0]
    assert "QListView#imageFilterPopup" in QSS
    assert "#ffffff" in QSS.split("QListView#imageFilterPopup")[1].split("}")[0]


def test_fill_image_combo_ordering_and_placeholder(qapp):
    from PySide6.QtWidgets import QComboBox

    from ucm_deployer.core.models import DockerImage
    from ucm_deployer.gui.widgets.common import combo_ref, fill_image_combo

    combo = QComboBox()
    images = [
        DockerImage("quay.io/ascend/vllm-ascend", "v0.23.0-a3", "id1", "18.2GB"),
        DockerImage("ucm-vllm", "v0.1", "id2", "5GB"),
    ]
    fill_image_combo(combo, images,
                     current="quay.io/ascend/vllm-ascend:v0.23.0-a3")
    assert combo.count() == 2
    assert combo_ref(combo) == "quay.io/ascend/vllm-ascend:v0.23.0-a3"
    assert "18.2GB" in combo.currentText(), "应显示镜像大小"
    assert "ucm-vllm:v0.1" in combo.itemText(0), "疑似 UCM 镜像应排在最前"

    fill_image_combo(combo, [])
    assert combo_ref(combo) == ""
    assert "尚未加载" in combo.currentText()


def test_image_page_download_hints(qapp, tmp_path):
    """步骤2 包输入框下方应提供在线下载链接提示（本地无包时指引获取）。"""
    from ucm_deployer.core.models import ServerInfo
    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.gui.state import AppContext
    from ucm_deployer.gui.widgets.image_page import ImagePage

    ctx = AppContext(ServerRegistry(tmp_path))
    ctx.selected = [ServerInfo.create(name="n1", host=UNREACHABLE_HOST,
                                      port=UNREACHABLE_PORT)]
    page = ImagePage(ctx)
    # UCM whl 下载指引：GitHub Releases 链接可点击
    assert "unified-cache-management/releases" in page.ucm_dl_hint.text()
    assert page.ucm_dl_hint.openExternalLinks()
    assert "github.com" in page.ucm_dl_hint.toolTip()
    # wrapt 下载指引：PyPI 1.17.2 链接 + 架构匹配提示
    assert "pypi.org/project/wrapt/1.17.2" in page.wrapt_dl_hint.text()
    assert page.wrapt_dl_hint.openExternalLinks()
    assert "Python 版本" in page.wrapt_dl_hint.text()
    assert "manylinux/glibc" in page.wrapt_dl_hint.text()
    # ucm-toolkit 下载指引：与 UCM 同站点（GitHub Releases）
    assert "unified-cache-management/releases" in page.toolkit_dl_hint.text()
    assert page.toolkit_dl_hint.openExternalLinks()
    # 镜像仓库拉取入口与“可跳过构建”的流程提示
    assert page.source_combo.count() == 3
    assert page.source_combo.currentText() == "服务器已有镜像"
    assert page.source_stack.currentIndex() == 0
    assert "无需填写仓库地址" in page.source_hint.text()
    page.source_combo.setCurrentIndex(2)
    assert page.source_stack.currentIndex() == 2
    assert "本地不需要安装 Docker Desktop" in page.source_hint.text()
    page.source_combo.setCurrentIndex(1)
    assert page.source_stack.currentIndex() == 1
    assert "quay.io/ascend/vllm-ascend" in page.registry_ref_edit.placeholderText()
    assert "确需制作新 UCM 镜像" in page.hint.text()
    assert "无需填写安装包或构建" in page.hint.text()


def test_image_page_refresh_populates_combo(qapp, tmp_path, fake_ssh):
    """回归测试：刷新镜像列表后下拉框必须填充（日志有数据而下拉框空的 bug）。"""
    from ucm_deployer.core.models import ServerInfo
    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.gui.state import AppContext
    from ucm_deployer.gui.widgets.common import combo_ref
    from ucm_deployer.gui.widgets.image_page import ImagePage

    fake_ssh.add_default(r"^docker info", stdout="")
    fake_ssh.add_default(
        r"images --format",
        stdout=("repo1\tv1\tid1\t18.2GB\t2026-08-01 10:00:00\n"
                "repo2\tv2\tid2\t9.9GB\t2026-07-01 09:00:00\n"
                "ucm-engine\tv0.1\tid3\t5GB\t2026-09-01 08:00:00\n"))

    ctx = AppContext(ServerRegistry(tmp_path))
    ctx.selected = [ServerInfo.create(name="n1", host="127.0.0.1")]
    page = ImagePage(ctx)
    page._refresh_images()
    assert _wait_panel(page.panel, qapp), "刷新镜像列表超时"
    qapp.processEvents()

    assert page.image_combo.count() == 3, "下拉框应填充服务器上的镜像列表"
    assert combo_ref(page.image_combo) != "", "应默认选中一个镜像"
    # 疑似 UCM 镜像排在最前
    assert combo_ref(page.image_combo) == "ucm-engine:v0.1"
    # 默认镜像名已按所选镜像自动建议（<基础镜像名>:ucm）
    assert page.tag_edit.text().strip() == "ucm-engine:ucm"


def test_image_page_apply_images_triggers_runtime_probe(qapp, tmp_path, monkeypatch):
    """fill_image_combo 会阻断信号，刷新后必须手动触发运行时探测。"""
    from ucm_deployer.core.models import DockerImage, ServerInfo
    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.gui.state import AppContext
    from ucm_deployer.gui.widgets.image_page import ImagePage

    ctx = AppContext(ServerRegistry(tmp_path))
    ctx.selected = [ServerInfo.create(name="n1", host=UNREACHABLE_HOST,
                                      port=UNREACHABLE_PORT)]
    page = ImagePage(ctx)
    calls = []
    monkeypatch.setattr(page, "_on_base_image_changed",
                        lambda ref: calls.append(ref))
    page._images = [DockerImage("quay.io/ascend/vllm-ascend",
                                "v0.30.0rc2-a5-openeuler", "id", "10GB")]
    page._apply_images()
    assert calls == ["quay.io/ascend/vllm-ascend:v0.30.0rc2-a5-openeuler"]


def test_image_page_runtime_updates_package_suggestions(qapp, tmp_path, monkeypatch):
    """选中镜像后展示运行时，并按运行时给出精确 wheel 建议/校验。"""
    from ucm_deployer.core.image_builder import ImageRuntimeInfo
    from ucm_deployer.core.models import DeviceInfo, DeviceType, DockerImage, ServerInfo
    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.gui.state import AppContext
    from ucm_deployer.gui.widgets.common import combo_ref, fill_image_combo
    from ucm_deployer.gui.widgets.image_page import ImagePage

    ref = "quay.io/ascend/vllm-ascend:v0.30.0rc2-a5-openeuler"
    ctx = AppContext(ServerRegistry(tmp_path))
    server = ServerInfo.create(name="n1", host=UNREACHABLE_HOST,
                               port=UNREACHABLE_PORT)
    ctx.selected = [server]
    ctx.devices = {server.id: DeviceInfo(
        DeviceType.ASCEND, "Ascend950PR", 8, arch="aarch64")}
    page = ImagePage(ctx)
    fill_image_combo(page.image_combo,
                     [DockerImage("quay.io/ascend/vllm-ascend",
                                  "v0.30.0rc2-a5-openeuler", "id", "10GB")],
                     current=ref)
    assert combo_ref(page.image_combo) == ref

    ucm = tmp_path / (
        "uc_manager_cann920_a5-0.9.0rc3-cp312-cp312-"
        "manylinux_2_34_aarch64.whl")
    wrapt = tmp_path / (
        "wrapt-1.17.2-cp312-cp312-"
        "manylinux_2_17_aarch64.manylinux2014_aarch64.whl")
    ucm.write_bytes(b"PK")
    wrapt.write_bytes(b"PK")
    page.ucm_whl_edit.setText(str(ucm))
    page.wrapt_whl_edit.setText(str(wrapt))

    runtime = ImageRuntimeInfo(
        python_version="3.12", python_abi="cp312", arch="aarch64",
        glibc_version="2.34", cann_version="9.2", variant="a5", probe_ok=True)
    monkeypatch.setattr(
        page, "_run_quick",
        lambda fn, on_done: on_done(runtime, None))
    page._on_base_image_changed(ref)

    assert "Python 3.12" in page.runtime_label.text()
    assert "CANN 9.2" in page.runtime_label.text()
    assert "A5" in page.runtime_label.text()
    assert ("建议 UCM：uc_manager_cann920_a5-*-cp312-cp312-"
            "manylinux_2_34_aarch64.whl") in page.runtime_label.text()
    assert ("建议 wrapt：wrapt-1.17.2-cp312-cp312-"
            "manylinux_2_17_aarch64.manylinux2014_aarch64.whl") \
        in page.runtime_label.text()
    assert "与基础镜像运行时匹配" in page.ucm_fb.text()
    assert "与基础镜像运行时匹配" in page.wrapt_fb.text()


def test_image_page_pulls_registry_image_on_server(qapp, tmp_path, fake_ssh):
    """仓库镜像由目标服务器 docker pull，本地无需 Docker，成功后切回已有镜像。"""
    from ucm_deployer.core.models import DeviceInfo, DeviceType, ServerInfo
    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.gui.state import AppContext
    from ucm_deployer.gui.widgets.common import combo_ref
    from ucm_deployer.gui.widgets.image_page import ImagePage

    fake_ssh.add_default(r"^docker info", stdout="")
    fake_ssh.add_default(r"^docker pull", stdout="Status: Downloaded\n")
    fake_ssh.add_default(r"docker image inspect", stdout="")
    ref = "quay.io/ascend/vllm-ascend:v0.30.0rc2-a5-openeuler"

    ctx = AppContext(ServerRegistry(tmp_path))
    server = ServerInfo.create(name="n1", host="127.0.0.1")
    ctx.selected = [server]
    ctx.devices = {server.id: DeviceInfo(
        DeviceType.ASCEND, "Ascend950PR", 8, arch="aarch64")}
    page = ImagePage(ctx)
    page.registry_ref_edit.setText(ref)
    page.source_combo.setCurrentIndex(2)
    assert page.pull_command_edit.toPlainText() == (
        f"docker pull --platform linux/arm64 {ref}")
    page._pull_registry_image()

    assert _wait_panel(page.panel, qapp), "服务器拉取镜像任务超时"
    assert ctx.images[server.id] == ref
    assert page.source_combo.currentText() == "服务器已有镜像"
    assert combo_ref(page.image_combo) == ref
    commands = [c.executed_command for c in fake_ssh.instances[0].channels]
    pull = [c for c in commands if c.startswith("docker pull")][-1]
    assert pull == f"docker pull '--platform' linux/arm64 {ref}"
    assert not any(c.startswith("docker save") for c in commands)

    page.source_combo.setCurrentIndex(2)
    malicious = f"docker pull --platform linux/arm64 {ref}; touch /tmp/ucm-pwned"
    page.pull_command_edit.setPlainText(malicious)
    page._pull_registry_image()
    assert _wait_panel(page.panel, qapp), "服务器拉取镜像任务超时"
    commands = [c.executed_command for c in fake_ssh.instances[-1].channels]
    pull = next(c for c in commands if c.startswith("docker pull"))
    assert "; touch /tmp/ucm-pwned" not in pull
    assert f"'{ref};' touch /tmp/ucm-pwned" in pull


def test_image_page_tag_suggest_follows_ucm_whl(qapp, tmp_path):
    """新镜像名建议：选择 UCM whl 后带版本号；用户手动编辑后不再覆盖。"""
    from ucm_deployer.core.models import ServerInfo
    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.gui.state import AppContext
    from ucm_deployer.gui.widgets.common import fill_image_combo
    from ucm_deployer.gui.widgets.image_page import ImagePage
    from ucm_deployer.core.models import DockerImage

    ctx = AppContext(ServerRegistry(tmp_path))
    s = ServerInfo.create(name="n1", host="127.0.0.1")
    ctx.selected = [s]
    page = ImagePage(ctx)
    fill_image_combo(page.image_combo,
                     [DockerImage("vllm-ascend", "v0.23.0-a3", "i", "18GB")],
                     current="vllm-ascend:v0.23.0-a3")
    # fill_image_combo 内部 blockSignals，手动触发建议（与 _apply_images 一致）
    page._suggest_tag_if_empty()
    qapp.processEvents()
    assert page.tag_edit.text() == "vllm-ascend:ucm"

    # 选择 UCM whl -> 建议名带上版本号
    ucm = tmp_path / "uc_manager-0.8.0.dev20261015-py3-none-any.whl"
    ucm.write_bytes(b"PK")
    page.ucm_whl_edit.setText(str(ucm))
    qapp.processEvents()
    assert page.tag_edit.text() == "vllm-ascend:ucm-0.8.0.dev20261015"

    # 用户手动编辑 -> 自动建议停止覆盖
    page.tag_edit.setText("my-custom-image:1")
    qapp.processEvents()
    ucm2 = tmp_path / "uc_manager-9.9.9-py3-none-any.whl"
    ucm2.write_bytes(b"PK")
    page.ucm_whl_edit.setText(str(ucm2))
    qapp.processEvents()
    assert page.tag_edit.text() == "my-custom-image:1"


def test_image_page_whl_instant_feedback(qapp, tmp_path):
    """whl 选择即时反馈：文件存在性 + 架构标记解析。"""
    from ucm_deployer.core.models import ServerInfo
    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.gui.state import AppContext
    from ucm_deployer.gui.widgets.image_page import ImagePage

    ctx = AppContext(ServerRegistry(tmp_path))
    ctx.selected = [ServerInfo.create(name="n1", host=UNREACHABLE_HOST,
                                      port=UNREACHABLE_PORT)]
    page = ImagePage(ctx)

    wrapt = tmp_path / "wrapt-1.17.2-cp39-cp39-manylinux2014_aarch64.whl"
    wrapt.write_bytes(b"PK")
    page.wrapt_whl_edit.setText(str(wrapt))
    qapp.processEvents()
    assert "包名 wrapt" in page.wrapt_fb.text()
    assert "Python cp39" in page.wrapt_fb.text()
    assert "架构 aarch64" in page.wrapt_fb.text()
    assert "glibc ≥ 2.17" in page.wrapt_fb.text()
    assert "未能确认基础镜像 Python 版本" in page.wrapt_fb.text()
    assert "未能确认基础镜像 glibc 版本" in page.wrapt_fb.text()

    ucm = tmp_path / "uc_manager-0.2.1-py3-none-any.whl"
    ucm.write_bytes(b"PK")
    page.ucm_whl_edit.setText(str(ucm))
    qapp.processEvents()
    assert "包名 uc-manager" in page.ucm_fb.text()
    assert "Python py3" in page.ucm_fb.text()
    assert "架构 通用" in page.ucm_fb.text()
    assert "通用 UCM 包" in page.ucm_fb.text()

    page.ucm_whl_edit.setText(str(tmp_path / "missing.whl"))
    qapp.processEvents()
    assert "文件不存在" in page.ucm_fb.text()


def test_image_page_platform_label(qapp, tmp_path):
    from ucm_deployer.core.models import DeviceInfo, DeviceType, ServerInfo
    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.gui.state import AppContext
    from ucm_deployer.gui.widgets.image_page import ImagePage

    ctx = AppContext(ServerRegistry(tmp_path))
    page = ImagePage(ctx)
    page._update_platform_label()
    assert "未探测设备" in page.platform_label.text()

    s = ServerInfo.create(name="n", host="10.0.0.1")
    ctx.selected = [s]
    ctx.devices = {s.id: DeviceInfo(DeviceType.ASCEND, "Ascend 910B3", 8)}
    page._update_platform_label()
    assert "ascend" in page.platform_label.text()

    ctx.devices = {s.id: DeviceInfo(DeviceType.NVIDIA, "NVIDIA A800", 8)}
    page._update_platform_label()
    assert "cuda" in page.platform_label.text()


# ============================================================ 步骤3：命令生成
def test_container_page_command_gen(qapp, tmp_path):
    """命令生成：卡数全量映射、NVIDIA 模板带 --runtime/-p、命令可编辑。"""
    from ucm_deployer.core.models import (DeviceInfo, DeviceType, DockerImage,
                                          ServerInfo)
    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.gui.state import AppContext
    from ucm_deployer.gui.widgets.common import fill_image_combo
    from ucm_deployer.gui.widgets.container_page import ContainerPage
    from PySide6.QtWidgets import QScrollArea

    ctx = AppContext(ServerRegistry(tmp_path))
    s1 = ServerInfo.create(name="n1", host=UNREACHABLE_HOST, port=UNREACHABLE_PORT)
    ctx.selected = [s1]
    ctx.devices = {s1.id: DeviceInfo(DeviceType.ASCEND, "Ascend 910B3", 8)}
    ctx.images = {s1.id: "img:t1"}

    page = ContainerPage(ctx)
    page._ascend_devices = [
        f"/dev/davinci{i}" for i in range(8)
    ] + ["/dev/davinci_manager", "/dev/devmm_svm", "/dev/hisi_hdc"]
    page._ascend_mounts = [("/usr/local/Ascend/driver", "/usr/local/Ascend/driver")]
    # 排版回归：不再使用滚动容器（内容须一屏可见）
    assert page.findChild(QScrollArea) is None
    fill_image_combo(page.image_combo, [DockerImage("img", "t1", "i", "1GB")],
                     current="img:t1")
    page._regen()
    qapp.processEvents()

    cmd = page.command_text()
    assert cmd.startswith("docker run -itd")
    assert "--device /dev/davinci7" in cmd          # 8 卡全量映射
    assert "--device /dev/davinci_manager" in cmd
    assert "--net=host" in cmd

    # Ascend 950PR 等新型号：仅映射服务器实际存在的设备节点
    page._ascend_devices = [
        "/dev/davinci0", "/dev/davinci1", "/dev/davinci2",
        "/dev/davinci3", "/dev/davinci4", "/dev/davinci5",
        "/dev/davinci6", "/dev/davinci7", "/dev/davinci_manager",
        "/dev/hisi_hdc",
    ]
    page._regen()
    qapp.processEvents()
    cmd = page.command_text()
    assert "--device /dev/hisi_hdc" in cmd
    assert "/dev/devmm_svm" not in cmd

    # 命令可直接编辑，创建时按编辑框内容执行
    page.cmd_edit.setPlainText("docker run -itd --name edited img:t1 bash")
    assert page.command_text().startswith("docker run -itd --name edited")

    # NVIDIA 设备 -> --runtime nvidia + 端口映射
    ctx.devices = {s1.id: DeviceInfo(DeviceType.NVIDIA, "NVIDIA A800", 8)}
    fill_image_combo(page.image_combo, [DockerImage("img", "t1", "i", "1GB")],
                     current="img:t1")
    page._regen()
    qapp.processEvents()
    cmd = page.command_text()
    assert "--runtime nvidia" in cmd
    assert "-p 8000:8000" in cmd
    assert "davinci" not in cmd


def test_container_page_kv_and_mounts(qapp, tmp_path):
    """kvcache 多目录 + 模型挂载 + 自定义挂载进入命令。"""
    from PySide6.QtCore import Qt

    from ucm_deployer.core.models import DeviceInfo, DeviceType, DockerImage, ServerInfo
    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.gui.state import AppContext
    from ucm_deployer.gui.widgets.common import fill_image_combo
    from ucm_deployer.gui.widgets.container_page import ContainerPage

    ctx = AppContext(ServerRegistry(tmp_path))
    s1 = ServerInfo.create(name="n1", host=UNREACHABLE_HOST, port=UNREACHABLE_PORT)
    ctx.selected = [s1]
    ctx.devices = {s1.id: DeviceInfo(DeviceType.ASCEND, "Ascend 910B3", 8)}
    ctx.images = {s1.id: "img:t1"}

    page = ContainerPage(ctx)
    page._ascend_devices = [
        f"/dev/davinci{i}" for i in range(8)
    ] + ["/dev/davinci_manager", "/dev/devmm_svm", "/dev/hisi_hdc"]
    page._ascend_mounts = [("/usr/local/Ascend/driver", "/usr/local/Ascend/driver")]
    fill_image_combo(page.image_combo, [DockerImage("img", "t1", "i", "1GB")],
                     current="img:t1")

    page.kv_add_row.edit.setText("/mnt/kvcache1")
    page._kv_add()
    page.kv_add_row.edit.setText("/mnt/kvcache2")
    page._kv_add()
    assert page.kv_list.count() == 2

    page.model_host.edit.setText("/data/models/Qwen3")
    page.model_container.setText("/models")
    page._extra_add()
    page.extra_table.item(0, 0).setText("/workspace/logs")
    page.extra_table.item(0, 1).setText("/logs")
    qapp.processEvents()

    page._regen()
    cmd = page.command_text()
    assert "-v /mnt/kvcache1:/mnt/kvcache1" in cmd
    assert "-v /mnt/kvcache2:/mnt/kvcache2" in cmd
    assert "-v /data/models/Qwen3:/models:ro" in cmd
    assert "-v /workspace/logs:/logs" in cmd


# ============================================================ 步骤4：环境预检
_PRECHECK_SAMPLE = """[INFO] serving_stack        vllm=0.26.0
[PASS] kernel               5.10.0
        threshold: >= 5.10
[WARN] memory_shm           shm=0.06 GiB
[FAIL] accelerator_driver   -
        no nvidia-smi or npu-smi found
[SKIP] bandwidth            -
Result: FAILED  |  1 pass, 1 warn, 1 fail, 1 skip, 1 info
"""


def test_precheck_page_intercepts_and_labels(qapp, tmp_path):
    """步骤4：未登录/未建容器时拦截；容器名回显；带宽开关控制挂载目录。"""
    from ucm_deployer.core.models import ServerInfo
    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.gui.state import AppContext
    from ucm_deployer.gui.widgets.precheck_page import PrecheckPage

    ctx = AppContext(ServerRegistry(tmp_path))
    page = PrecheckPage(ctx)
    page.on_enter()
    assert "未登录" in page.container_label.text()

    # 带宽检查默认开启，挂载目录可用
    assert page.bandwidth_check.isChecked()
    assert page.mount_edit.isEnabled()

    s = ServerInfo.create(name="n1", host="127.0.0.1")
    ctx.selected = [s]
    page.on_enter()
    assert "未选择" in page.container_label.text()
    page._run()      # 未建容器 -> 提示（自动应答）后返回
    qapp.processEvents()
    assert page.result_table.rowCount() == 0
    page._browse_mount()   # 未建容器时浏览 -> 提示后返回（不弹目录对话框）
    qapp.processEvents()

    ctx.containers = {s.id: "ucm-vllm"}
    page.on_enter()
    assert "ucm-vllm" in page.container_label.text()
    # 带宽开启但未填挂载目录 -> 拦截
    page._run()
    qapp.processEvents()
    assert page.result_table.rowCount() == 0
    # 挂载目录非绝对路径 -> 拦截
    page.mount_edit.setText("relative/path")
    page._run()
    qapp.processEvents()
    assert page.result_table.rowCount() == 0

    # 关闭带宽检查：挂载目录禁用并清空，不校验目录
    page.bandwidth_check.setChecked(False)
    qapp.processEvents()
    assert not page.mount_edit.isEnabled()
    assert page.mount_edit.text() == ""


def test_precheck_page_explicitly_selects_existing_container(qapp, tmp_path, fake_ssh):
    """步骤4：显式刷新并选择已有容器，预检命令使用所选容器。"""
    import time as _t

    from ucm_deployer.core.models import ServerInfo
    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.gui.state import AppContext
    from ucm_deployer.gui.widgets.precheck_page import PrecheckPage

    fake_ssh.add_default(
        r"^docker ps -a",
        stdout="id1\texisting-ucm\tvllm-ascend-ucm:v0.30.0\tUp 2 hours\n"
               "id2\tnew-ucm\tvllm-ascend-ucm:v0.30.0\tUp 1 minute\n")
    fake_ssh.add_default(r"ucm-toolkit run precheck",
                         stdout="[PASS] kernel  5.10.0\n"
                                "Result: PASSED  |  1 pass, 0 warn, 0 fail\n")

    ctx = AppContext(ServerRegistry(tmp_path))
    server = ServerInfo.create(name="n1", host="127.0.0.1")
    ctx.selected = [server]
    page = PrecheckPage(ctx)
    page.on_enter()
    deadline = _t.time() + 15
    while page._quick_tasks and _t.time() < deadline:
        qapp.processEvents()
        _t.sleep(0.05)
    qapp.processEvents()

    assert [page.container_combo.itemText(i)
            for i in range(page.container_combo.count())] == ["existing-ucm", "new-ucm"]
    page.container_combo.setCurrentText("new-ucm")
    assert ctx.containers[server.id] == "new-ucm"
    page.bandwidth_check.setChecked(False)
    page._run()
    assert _wait_panel(page.panel, qapp), "预检任务超时"
    qapp.processEvents()

    cmds = [c.executed_command for inst in fake_ssh.instances for c in inst.channels]
    assert any("docker exec new-ucm ucm-toolkit run precheck" in cmd
               for cmd in cmds)


def test_precheck_page_runs_and_shows_result(qapp, tmp_path, fake_ssh):
    """步骤4：预检执行 + 结果表格逐项展示（含状态着色）+ 临时目录播报。"""
    from ucm_deployer.core.models import ServerInfo
    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.gui.state import AppContext
    from ucm_deployer.gui.widgets.precheck_page import PrecheckPage

    fake_ssh.add_default(r"test -d /mnt/kvcache", stdout="")
    fake_ssh.add_default(r"mkdir -p ", stdout="")
    fake_ssh.add_default(r"ucm-toolkit run precheck",
                         stdout=_PRECHECK_SAMPLE, exit_code=1)
    fake_ssh.add_default(r"rm -rf ", stdout="")

    ctx = AppContext(ServerRegistry(tmp_path))
    s = ServerInfo.create(name="n1", host="127.0.0.1")
    ctx.selected = [s]
    ctx.containers = {s.id: "ucm-vllm"}
    page = PrecheckPage(ctx)
    page.on_enter()
    page.mount_edit.setText("/mnt/kvcache")
    page._run()
    assert _wait_panel(page.panel, qapp), "预检任务超时"
    qapp.processEvents()

    # 结果表格：5 个检查项；摘要列只放首行摘要
    assert page.result_table.rowCount() == 5
    assert page.result_table.item(0, 0).text() == "INFO"
    assert page.result_table.item(1, 0).text() == "PASS"
    assert page.result_table.item(1, 1).text() == "内核版本（kernel）"
    assert page.result_table.item(1, 2).text() == "5.10.0"
    assert page.result_table.item(3, 0).text() == "FAIL"
    assert page.result_table.item(3, 1).text() == "加速器驱动（accelerator_driver）"
    # 摘要行：FAIL 判定为未通过
    assert "未通过" in page.summary_label.text()
    assert "FAILED" in page.summary_label.text()

    # 默认选中首个 WARN/FAIL 行，详情区立即显示完整内容
    assert page.result_table.currentRow() == 2      # memory_shm（首个 WARN）
    assert "共享内存" in page.detail_view.toPlainText()
    assert "排查建议" in page.detail_view.toPlainText()

    # 点击其他行 -> 详情区联动（kernel：阈值 + 排查建议完整可见）
    page.result_table.setCurrentCell(1, 0)
    qapp.processEvents()
    detail = page.detail_view.toPlainText()
    assert "内核版本" in detail
    assert "threshold: >= 5.10" in detail
    assert "排查建议" in detail and "升级" in detail

    # 命令时序：建子目录 -> precheck --mount-path 子目录 -> 清理
    cmds = [c.executed_command for inst in fake_ssh.instances
            for c in inst.channels]
    precheck_cmd = next(c for c in cmds if "ucm-toolkit run precheck" in c)
    assert "--mount-path" in precheck_cmd
    assert "ucm-precheck-" in precheck_cmd
    rm_cmd = next(c for c in cmds if "rm -rf" in c)
    assert "ucm-precheck-" in rm_cmd
    # 日志播报临时目录路径与清理结果
    log_text = page.panel._logs[s.id].toPlainText()
    assert "带宽测试临时目录" in log_text and "ucm-precheck-" in log_text
    assert "已清理临时目录" in log_text


def test_precheck_page_bandwidth_disabled_env_only(qapp, tmp_path, fake_ssh):
    """步骤4：关闭带宽检查时无需挂载目录，命令带 --skip-bandwidth。"""
    from ucm_deployer.core.models import ServerInfo
    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.gui.state import AppContext
    from ucm_deployer.gui.widgets.precheck_page import PrecheckPage

    fake_ssh.add_default(r"ucm-toolkit run precheck",
                         stdout=_PRECHECK_SAMPLE, exit_code=1)

    ctx = AppContext(ServerRegistry(tmp_path))
    s = ServerInfo.create(name="n1", host="127.0.0.1")
    ctx.selected = [s]
    ctx.containers = {s.id: "ucm-vllm"}
    page = PrecheckPage(ctx)
    page.on_enter()
    page.bandwidth_check.setChecked(False)
    qapp.processEvents()
    page._run()
    assert _wait_panel(page.panel, qapp), "预检任务超时"

    cmds = [c.executed_command for inst in fake_ssh.instances
            for c in inst.channels]
    precheck_cmd = next(c for c in cmds if "ucm-toolkit run precheck" in c)
    assert "--skip-bandwidth" in precheck_cmd
    assert "--mount-path" not in precheck_cmd
    assert not any("mkdir -p" in c for c in cmds)


def test_precheck_page_warn_verdict_wording(qapp, tmp_path, fake_ssh):
    """有 WARN 但退出码 0：界面须显示「预检完成（有警告）」而非「预检通过」。"""
    from ucm_deployer.core.models import ServerInfo
    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.gui.state import AppContext
    from ucm_deployer.gui.widgets.precheck_page import PrecheckPage

    sample = ("[PASS] kernel  5.10.0\n"
              "[WARN] memory_shm  0.06 GiB\n"
              "Result: PASSED (with warnings)  |  1 pass, 1 warn, 0 fail\n")
    fake_ssh.add_default(r"ucm-toolkit run precheck", stdout=sample, exit_code=0)

    ctx = AppContext(ServerRegistry(tmp_path))
    s = ServerInfo.create(name="n1", host="127.0.0.1")
    ctx.selected = [s]
    ctx.containers = {s.id: "ucm-vllm"}
    page = PrecheckPage(ctx)
    page.on_enter()
    page.bandwidth_check.setChecked(False)
    qapp.processEvents()
    page._run()
    assert _wait_panel(page.panel, qapp), "预检任务超时"
    qapp.processEvents()

    text = page.summary_label.text()
    assert "预检完成" in text, f"应为「预检完成」而非「预检通过」: {text}"
    assert "警告" in text and "1 项" in text
    assert "预检通过" not in text
    assert "PASSED (with warnings)" in text, "应附带 Result 汇总行"


def test_precheck_page_pass_verdict_wording(qapp, tmp_path, fake_ssh):
    """全部通过无警告：显示「预检通过」。"""
    from ucm_deployer.core.models import ServerInfo
    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.gui.state import AppContext
    from ucm_deployer.gui.widgets.precheck_page import PrecheckPage

    sample = ("[PASS] kernel  5.10.0\n"
              "Result: PASSED  |  1 pass, 0 warn, 0 fail\n")
    fake_ssh.add_default(r"ucm-toolkit run precheck", stdout=sample, exit_code=0)

    ctx = AppContext(ServerRegistry(tmp_path))
    s = ServerInfo.create(name="n1", host="127.0.0.1")
    ctx.selected = [s]
    ctx.containers = {s.id: "ucm-vllm"}
    page = PrecheckPage(ctx)
    page.on_enter()
    page.bandwidth_check.setChecked(False)
    qapp.processEvents()
    page._run()
    assert _wait_panel(page.panel, qapp), "预检任务超时"
    qapp.processEvents()

    assert "预检通过" in page.summary_label.text()
    assert "无警告" in page.summary_label.text()


def test_precheck_config_dialog_flow(qapp, tmp_path, fake_ssh):
    """预检参数配置对话框：加载默认 -> 校验 -> 保存并启用；页面状态联动。"""
    import time as _t

    from ucm_deployer.core.models import ServerInfo
    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.gui.state import AppContext
    from ucm_deployer.gui.widgets.precheck_config_dialog import PrecheckConfigDialog
    from ucm_deployer.gui.widgets.precheck_page import PrecheckPage

    defaults_json = '{"kernel_min": "5.10", "bandwidth": {"threshold_gb": 8.0}}'
    # 一次性队列（按调用时序消费）：页面探测(无自定义) -> 对话框探测(无自定义)
    # -> 定位安装位置 -> 读默认 -> 保存写入 -> 页面刷新(自定义已启用)
    fake_ssh.add_default(r"test -f /root/ucm-deploy/precheck.json",
                         exit_code=1, once=True)
    fake_ssh.add_default(r"test -f /root/ucm-deploy/precheck.json",
                         exit_code=1, once=True)
    fake_ssh.add_default(
        r"python3 -c ",
        stdout="/site/ucm_toolkit/tools/precheck/precheck.defaults.json\n",
        once=True)
    fake_ssh.add_default(r"cat /site/", stdout=defaults_json, once=True)
    fake_ssh.add_default(r"mkdir -p /root/ucm-deploy", stdout="", once=True)
    fake_ssh.add_default(r"test -f /root/ucm-deploy/precheck.json",
                         stdout="", once=True)

    ctx = AppContext(ServerRegistry(tmp_path))
    s = ServerInfo.create(name="n1", host="127.0.0.1")
    ctx.selected = [s]
    ctx.containers = {s.id: "ucm-vllm"}

    # 页面进入：探测到无自定义配置 -> 出厂默认
    page = PrecheckPage(ctx)
    page.on_enter()
    deadline = _t.time() + 15
    while page._quick_tasks and _t.time() < deadline:
        qapp.processEvents(); _t.sleep(0.05)
    qapp.processEvents()
    assert "出厂默认" in page.config_status_label.text()
    assert page._custom_config is False

    # 打开对话框：自动加载出厂默认（优先自定义，不存在则默认）
    dlg = PrecheckConfigDialog(ctx, "ucm-vllm")
    deadline = _t.time() + 15
    while dlg._quick_tasks and _t.time() < deadline:
        qapp.processEvents(); _t.sleep(0.05)
    qapp.processEvents()
    assert '"kernel_min"' in dlg.editor.toPlainText()
    # 加载成功（"已加载"提示覆盖实时校验文案；语法正确体现为可保存）
    assert "已加载" in dlg.validate_label.text()
    assert "出厂默认" in dlg.validate_label.text()
    assert dlg.save_btn.isEnabled()

    # 修改为非法 JSON -> 校验失败、保存禁用
    dlg.editor.setPlainText("{broken")
    qapp.processEvents()
    assert "语法错误" in dlg.validate_label.text()
    assert not dlg.save_btn.isEnabled()

    # 改回合法并保存 -> 容器收到写入命令
    dlg.editor.setPlainText('{"bandwidth": {"threshold_gb": 4.0}}')
    qapp.processEvents()
    dlg._save()
    deadline = _t.time() + 15
    while dlg._quick_tasks and _t.time() < deadline:
        qapp.processEvents(); _t.sleep(0.05)
    qapp.processEvents()
    cmds = [c.executed_command for inst in fake_ssh.instances
            for c in inst.channels]
    assert any("printf" in c and "/root/ucm-deploy/precheck.json" in c
               and '"threshold_gb": 4.0' in c for c in cmds)

    # 页面状态刷新为自定义已启用
    page._refresh_config_status()
    deadline = _t.time() + 15
    while page._quick_tasks and _t.time() < deadline:
        qapp.processEvents(); _t.sleep(0.05)
    qapp.processEvents()
    assert page._custom_config is True
    assert "自定义配置已启用" in page.config_status_label.text()
