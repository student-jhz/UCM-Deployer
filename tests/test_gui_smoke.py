# -*- coding: utf-8 -*-
"""GUI 冒烟测试（offscreen 模式，无显示器环境可跑）。"""
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
    # 实例方法 exec()（如构造 QMessageBox 后 exec）
    QMessageBox.exec = lambda self, *a, **k: QMessageBox.StandardButton.Ok  # noqa: A001


_automate_message_boxes()


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def test_main_window_pages(qapp):
    from ucm_deployer.gui.main_window import MainWindow

    win = MainWindow()
    win.show()
    qapp.processEvents()
    for i in range(5):
        win.nav.setCurrentRow(i)
        qapp.processEvents()
    # 步骤1 无服务器时应停留在第0页
    assert win.pages.currentIndex() == 0
    win.close()


def test_server_page_with_mock_servers(qapp, tmp_path):
    from ucm_deployer.core.models import DeviceInfo, DeviceType, ServerInfo
    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.gui.state import AppContext
    from ucm_deployer.gui.widgets.server_page import ServerPage

    ctx = AppContext(ServerRegistry(tmp_path))
    ctx.registry.upsert(ServerInfo.create(
        name="node1", host="10.0.0.1", password="pw"))
    ctx.registry.upsert(ServerInfo.create(
        name="node2", host="10.0.0.2", password="pw"))
    page = ServerPage(ctx)
    page.refresh()
    qapp.processEvents()
    assert page.table.rowCount() == 2

    # 勾选第一台
    from PySide6.QtCore import Qt
    page.table.item(0, 0).setCheckState(Qt.Checked)
    qapp.processEvents()
    assert len(ctx.selected) == 1
    assert ctx.selected[0].name == "node1"


def test_deploy_page_validation(qapp, tmp_path):
    from ucm_deployer.core.models import DeviceInfo, DeviceType, ServerInfo
    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.gui.state import AppContext
    from ucm_deployer.gui.widgets.deploy_page import DeployPage

    ctx = AppContext(ServerRegistry(tmp_path))
    s1 = ServerInfo.create(name="p1", host="10.0.0.1")
    s2 = ServerInfo.create(name="d1", host="10.0.0.2")
    ctx.selected = [s1, s2]
    ctx.devices = {
        s1.id: DeviceInfo(DeviceType.ASCEND, "Ascend 910B3", 8),
        s2.id: DeviceInfo(DeviceType.ASCEND, "Ascend 910B3", 8),
    }
    ctx.containers = {s1.id: "c1", s2.id: "c2"}
    page = DeployPage(ctx)
    page.on_enter()
    qapp.processEvents()
    assert page.node_table.rowCount() == 2

    # 混部模式 + 模型路径 -> 无错误
    page.model_edit.setText("/models/Qwen3-32B")
    page._validate()
    qapp.processEvents()
    assert "❌" not in page.validate_label.text()

    # PD 模式（默认 p1=P, d1=D）
    page.pd_radio.setChecked(True)
    page._validate()
    qapp.processEvents()
    assert "❌" not in page.validate_label.text()

    # 生成脚本
    page._generate()
    qapp.processEvents()
    assert ctx.scripts is not None
    assert page.script_tabs.count() >= 3  # mooncake master + prefill + decode + lb

    from ucm_deployer.core.topology import DeployMode
    assert ctx.scripts.plan.mode == DeployMode.PD


def test_image_page_form(qapp, tmp_path):
    from ucm_deployer.core.models import ServerInfo
    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.gui.state import AppContext
    from ucm_deployer.gui.widgets.image_page import ImagePage

    ctx = AppContext(ServerRegistry(tmp_path))
    ctx.selected = [ServerInfo.create(name="n1", host="10.0.0.1")]
    page = ImagePage(ctx)
    page.on_enter()
    qapp.processEvents()
    assert len(page.image_rows) == 1
    assert page.offline_radio.isChecked() is False
    page.offline_radio.setChecked(True)
    qapp.processEvents()
    assert page.wrapt_whl_edit.isEnabled()


def _wait_panel(panel, qapp, timeout=60):
    import time

    deadline = time.time() + timeout
    while panel.is_running() and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.05)
    qapp.processEvents()
    return not panel.is_running()


def test_gui_with_mock_server_flow(qapp, tmp_path):
    """GUI 页面 + ParallelTaskPanel 真实线程 + 模拟服务器全链路。"""
    import os

    from ucm_deployer.core.models import ServerInfo
    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.gui.state import AppContext
    from ucm_deployer.gui.widgets.container_page import ContainerPage
    from ucm_deployer.gui.widgets.deploy_page import DeployPage
    from ucm_deployer.gui.widgets.image_page import ImagePage
    from ucm_deployer.gui.widgets.server_page import ServerPage
    from ucm_deployer.mock.mock_server import MockSSHServer

    server = MockSSHServer(device="ascend", cards=8, root_dir=str(tmp_path / "root"))
    port = server.start()
    try:
        ctx = AppContext(ServerRegistry(tmp_path))
        info = ServerInfo.create(name="mock", host="127.0.0.1", port=port,
                                 username="root", password="root")
        ctx.registry.upsert(info)

        # ---- 步骤1: 选择 + 探测设备
        sp = ServerPage(ctx)
        sp.refresh()
        from PySide6.QtCore import Qt
        sp.table.item(0, 0).setCheckState(Qt.Checked)
        qapp.processEvents()
        assert len(ctx.selected) == 1
        sp._detect_devices()
        assert _wait_panel(sp.panel, qapp), "设备探测超时"
        assert ctx.devices[info.id].count == 8
        assert ctx.devices[info.id].model == "Ascend 910B3"

        # ---- 步骤2: 刷新镜像 + 离线构建
        ip = ImagePage(ctx)
        ip.on_enter()
        assert _wait_panel(ip.panel, qapp), "刷新镜像超时"
        combo = ip.image_rows[info.id]
        assert combo.count() >= 2

        whl = str(tmp_path / "uc_manager-0.2.1-py3-none-any.whl")
        open(whl, "wb").write(b"PK fake")
        wrapt = str(tmp_path / "wrapt-1.16.0-cp39.whl")
        open(wrapt, "wb").write(b"PK wrapt")
        ip.ucm_whl_edit.setText(whl)
        ip.offline_radio.setChecked(True)
        ip.wrapt_whl_edit.setText(wrapt)
        qapp.processEvents()
        ip._build()
        assert _wait_panel(ip.panel, qapp, 120), "构建镜像超时"
        assert ctx.images.get(info.id) == "vllm-ascend-ucm:v0.23.0-a3"

        # ---- 步骤3: 生成命令 + 创建容器 + 检查 UCM
        cp = ContainerPage(ctx)
        cp.on_enter()
        cp.kv_add_row.edit.setText("/mnt/nfs_share")
        cp._kv_add()
        cp.model_host.edit.setText("/models/Qwen3-32B")
        qapp.processEvents()
        cp._regen()
        cmd = cp.cmd_edit.toPlainText()
        assert cmd.startswith("docker run -itd")
        assert "--device /dev/davinci7" in cmd
        assert "-v /mnt/nfs_share:/mnt/nfs_share" in cmd
        cp._create()
        assert _wait_panel(cp.panel, qapp), "创建容器超时"
        assert ctx.containers.get(info.id) == "ucm-vllm"
        cp._check_container_ucm()
        assert _wait_panel(cp.panel, qapp), "容器UCM检查超时"

        # ---- 步骤4: 生成混部脚本（单服务器）
        dp = DeployPage(ctx)
        dp.on_enter()
        dp.model_edit.setText("/models/Qwen3-32B")
        qapp.processEvents()
        dp._validate()
        assert "❌" not in dp.validate_label.text()
        dp._generate()
        qapp.processEvents()
        assert ctx.scripts is not None
        assert any(s.role == "serve" for s in ctx.scripts.scripts)
        assert ctx.scripts.health_checks
    finally:
        server.stop()
