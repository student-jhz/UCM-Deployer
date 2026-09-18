# -*- coding: utf-8 -*-
"""GUI 测试全集（仅在子进程中运行）。

由 tests/test_gui_smoke.py 以子进程方式启动：
同一进程内加载 PySide6 后，paramiko 的并发原生调用会间歇性
访问违例(0xC0000005)——Qt 与 paramiko 必须进程隔离。
"""
import os

import pytest

if os.environ.get("UCM_GUI_TEST_CHILD") != "1":
    pytest.skip(
        "GUI 测试须由 tests/test_gui_smoke.py 以子进程方式运行"
        "（Qt 与 paramiko 原生并发需进程隔离）",
        allow_module_level=True)
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


# ============================================================ 无网络 SSH 替身
# 背景：Anaconda Python + PySide6 + paramiko 在 QThread 中并发调用原生层
# 会间歇性访问违例(0xC0000005)使进程崩溃/挂起。GUI 测试只验证界面/线程/
# 信号/状态机，因此用 MockLinux 命令状态机做 SSH 替身（无 socket/无 paramiko）。
# 真实 SSH 全流程由 tests/test_mock_e2e.py（纯 paramiko、无 Qt）覆盖。
_FAKE_LINUX = {}


class LocalScriptSSH:
    """SSHClient 替身：exec/upload 等全部由 MockLinux 状态机应答。

    按 server.id 关联持久 MockLinux 实例（同一测试内跨页面共享 docker 状态）。
    """

    def __init__(self, server, connect_timeout=15.0):
        self.server = server

    # ---- 生命周期 ----
    def _linux(self):
        linux = _FAKE_LINUX.get(self.server.id)
        if linux is None:
            import tempfile

            from ucm_deployer.mock.mock_linux import MockLinux
            linux = MockLinux(tempfile.mkdtemp(prefix="ucm-gui-fake-"),
                              device="ascend", cards=8)
            _FAKE_LINUX[self.server.id] = linux
        return linux

    def connect(self):
        self._linux()

    def close(self):
        pass

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- 命令 ----
    def exec(self, cmd, timeout=None, combine_stderr=False):
        from ucm_deployer.core.models import CommandResult
        code, out, err = self._linux().execute(cmd)
        return CommandResult(cmd, code, out, err)

    def exec_stream(self, cmd, on_line=None, timeout=None):
        from ucm_deployer.core.models import CommandResult
        code, out, err = self._linux().execute(cmd)
        if on_line:
            for line in out.splitlines():
                on_line(line)
        return CommandResult(cmd, code, out, err)

    # ---- 文件 ----
    def upload_file(self, local_path, remote_path, progress_cb=None):
        import os
        import shutil

        real = self._linux()._real(remote_path)
        os.makedirs(os.path.dirname(real), exist_ok=True)
        shutil.copyfile(local_path, real)
        if progress_cb:
            size = os.path.getsize(local_path)
            progress_cb(size, size)

    def download_file(self, remote_path, local_path, progress_cb=None):
        import shutil

        shutil.copyfile(self._linux()._real(remote_path), local_path)
        if progress_cb:
            size = os.path.getsize(local_path)
            progress_cb(size, size)

    def file_size(self, remote_path):
        import os
        return os.path.getsize(self._linux()._real(remote_path))

    def write_file(self, remote_path, content):
        import os

        real = self._linux()._real(remote_path)
        os.makedirs(os.path.dirname(real), exist_ok=True)
        with open(real, "wb") as fh:
            fh.write(content.encode("utf-8"))

    def read_file(self, remote_path):
        with open(self._linux()._real(remote_path), "rb") as fh:
            return fh.read().decode("utf-8", errors="replace")

    def mkdirs(self, path):
        from ucm_deployer.core.shell import shq
        self.exec(f"mkdir -p {shq(path)} && test -d {shq(path)}")


@pytest.fixture(autouse=True)
def _use_fake_ssh(monkeypatch):
    """GUI 测试一律使用无网络 SSH 替身（避免 Qt+paramiko 原生并发崩溃）。"""
    from ucm_deployer.gui.widgets import common as _common
    monkeypatch.setattr(_common, "SSHClient", LocalScriptSSH)
    _FAKE_LINUX.clear()
    yield
    _FAKE_LINUX.clear()


# 历史上用于触发真实连接失败的地址（现为替身，不再真实连接）
UNREACHABLE_HOST, UNREACHABLE_PORT = "127.0.0.1", 1


@pytest.fixture(autouse=True)
def _shutdown_panels_after_test(qapp):
    """每个测试结束后关闭所有任务面板，防止 QThread 运行中被销毁导致退出崩溃。"""
    yield
    from PySide6.QtWidgets import QApplication

    from ucm_deployer.gui.widgets.common import ParallelTaskPanel
    for w in QApplication.topLevelWidgets():
        for panel in w.findChildren(ParallelTaskPanel):
            panel.shutdown()
    qapp.processEvents()


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
        win.goto_step(i)
        qapp.processEvents()
    # 步骤1 无服务器时所有切换被拒绝，页面停留在第0页
    assert win.pages.currentIndex() == 0
    win.close()


def test_nav_is_flat_buttons(qapp):
    """左侧步骤为平铺按钮（无滚动容器），文案为「步骤N：xxx」。"""
    from ucm_deployer.gui.main_window import MainWindow

    win = MainWindow()
    qapp.processEvents()
    assert len(win._nav_buttons) == 5
    assert win.nav_group.exclusive()
    for i, btn in enumerate(win._nav_buttons):
        # 不是 QListWidget 之类的滚动视图
        assert btn.objectName() == "navBtn"
        assert btn.text().startswith(f"步骤{i + 1}：")
        assert btn.isCheckable()
    assert win._nav_buttons[0].isChecked()
    # 程序化切换不触发守卫重入：goto_step 生效
    win.goto_step(0)
    assert win._nav_buttons[0].isChecked()
    win.close()


def test_panel_shutdown_orphans_slow_thread(qapp, tmp_path):
    """shutdown 超时线程必须脱离托管而非 terminate（曾引发进程级 abort）。"""
    import time as _t

    from ucm_deployer.core.models import ServerInfo
    from ucm_deployer.gui.widgets import common

    info = ServerInfo.create(name="m", host="127.0.0.1", port=1,
                             username="root", password="root")
    panel = common.ParallelTaskPanel()

    def slow(ssh, tctx):
        _t.sleep(3)

    assert panel.run_tasks([(info, slow)], "慢任务") is True
    _t.sleep(0.3)  # 等线程进入任务函数（替身连接瞬时完成）
    th = panel._threads[0]
    assert th.isRunning()
    panel.shutdown(100)   # 立即超时
    assert th.isRunning(), "shutdown 不应 terminate 线程"
    assert th in common.ParallelTaskPanel._ORPHANS, "超时线程应脱离托管"
    assert th.wait(10000), "脱离托管的线程应能自然结束（无状态破坏）"
    common.ParallelTaskPanel._ORPHANS.remove(th)


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
    # wrapt 下载指引：PyPI 链接 + 架构匹配提示
    assert "pypi.org/project/wrapt" in page.wrapt_dl_hint.text()
    assert page.wrapt_dl_hint.openExternalLinks()
    assert "aarch64" in page.wrapt_dl_hint.text(), "应提示选择匹配服务器架构的 whl"


def test_fill_image_combo(qapp):
    """镜像下拉：只可选择、显示大小、UCM 优先、ref 存 userData、空列表占位。"""
    from PySide6.QtWidgets import QComboBox

    from ucm_deployer.core.models import DockerImage
    from ucm_deployer.gui.widgets.common import combo_ref, fill_image_combo

    combo = QComboBox()
    images = [
        DockerImage("quay.io/ascend/vllm-ascend", "v0.23.0-a3", "id1", "18.2GB"),
        DockerImage("ucm-vllm", "v0.1", "id2", "5GB"),
    ]
    fill_image_combo(combo, images, current="quay.io/ascend/vllm-ascend:v0.23.0-a3")
    assert combo.count() == 2
    assert combo_ref(combo) == "quay.io/ascend/vllm-ascend:v0.23.0-a3"
    assert "18.2GB" in combo.currentText(), "应显示镜像大小"
    assert "ucm-vllm:v0.1" in combo.itemText(0), "UCM 镜像应排在最前"

    # 空列表 -> 占位提示，ref 为空
    fill_image_combo(combo, [])
    assert combo_ref(combo) == ""
    assert "尚未加载" in combo.currentText()


def test_nav_rejected_restores_previous_step(qapp, tmp_path):
    """点击未完成的前置步骤时：提示后导航应停留在当前步骤（不跳走）。"""
    from ucm_deployer.core.models import ServerInfo
    from ucm_deployer.gui.main_window import MainWindow

    s = ServerInfo.create(name="n", host=UNREACHABLE_HOST, port=UNREACHABLE_PORT)
    win = MainWindow()
    win.show()
    qapp.processEvents()

    # 无服务器 -> 点步骤2被拒，导航回 0
    win.goto_step(1)
    qapp.processEvents()
    assert win.current_step() == 0
    assert win.pages.currentIndex() == 0

    # 选服务器 -> 步骤2可进；无镜像 -> 步骤3被拒，导航回 1
    win.ctx.selected = [s]
    win.goto_step(1)
    qapp.processEvents()
    assert win.current_step() == 1
    assert win.pages.currentIndex() == 1
    win.goto_step(2)
    qapp.processEvents()
    assert win.current_step() == 1, "无镜像时步骤3应被拒并停留在步骤2"
    assert win._nav_buttons[1].isChecked(), "被拒后按钮选中态应回到步骤2"

    # 有镜像 -> 步骤3可进；无容器 -> 步骤4被拒；无脚本 -> 步骤5被拒
    win.ctx.images = {s.id: "img:t"}
    win.goto_step(2)
    qapp.processEvents()
    assert win.current_step() == 2
    win.goto_step(3)
    qapp.processEvents()
    assert win.current_step() == 2, "无容器时步骤4应被拒并停留在步骤3"
    win.ctx.containers = {s.id: "c1"}
    win.goto_step(3)
    qapp.processEvents()
    assert win.current_step() == 3
    win.goto_step(4)
    qapp.processEvents()
    assert win.current_step() == 3, "无脚本时步骤5应被拒并停留在步骤4"
    win.close()


def test_panel_bar_autocompletes_on_done(qapp, tmp_path):
    """任务函数未上报100%时，面板应在完成时自动补满进度条。"""
    from ucm_deployer.core.models import ServerInfo
    from ucm_deployer.gui.widgets.common import ParallelTaskPanel

    info = ServerInfo.create(name="m", host="127.0.0.1", port=1,
                             username="root", password="root")
    panel = ParallelTaskPanel()

    def fn(ssh, tctx):
        tctx.progress(50, "只做一半")

    assert panel.run_tasks([(info, fn)], "测试") is True
    assert _wait_panel(panel, qapp)
    bar = panel._bars[info.id]
    assert bar.value() == 100
    assert "完成" in bar.format()


def test_find_manual_path(qapp):
    from ucm_deployer.gui.main_window import MainWindow

    path = MainWindow.find_manual_path()
    assert path, "源码环境应能定位用户手册"
    assert path.endswith("用户手册.md")


def test_manual_renders_markdown(qapp):
    """手册应渲染为富文本：标题/表格/代码块转 HTML，而非原始 md 源码。"""
    from ucm_deployer.gui.main_window import MainWindow

    path = MainWindow.find_manual_path()
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    html = MainWindow.manual_html(text)
    assert html, "markdown 库可用时应产出 HTML"
    assert "<h1>" in html and "<h2>" in html
    assert "<table>" in html, "手册中的表格应转为 HTML 表格"
    assert "<pre>" in html or "<code>" in html, "代码块应保留代码样式"
    assert "|" not in html.split("<table>")[1].split("</table>")[0].replace("&#124;", ""), \
        "表格不应残留 markdown 竖线源码"


def test_manual_html_fallback_without_markdown(qapp, monkeypatch):
    """markdown 库缺失时返回空串（调用方回退纯文本显示）。"""
    import builtins

    from ucm_deployer.gui.main_window import MainWindow

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "markdown":
            raise ImportError("No module named markdown")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert MainWindow.manual_html("# 标题") == ""


def test_main_window_no_black_areas(qapp):
    """回归测试：布局间隙/边距不得出现未绘制黑色（QSS 规则顺序 bug 曾致窗口大片黑色）。

    原因：`QWidget{background:transparent}` 若声明在 `QMainWindow{background:...}`
    之后会覆盖窗口背景，导致所有未被实心控件覆盖的区域渲染为黑色。
    """
    from ucm_deployer.gui.main_window import MainWindow
    from ucm_deployer.gui.theme import apply_light_theme

    apply_light_theme(qapp)
    win = MainWindow()
    win.resize(1320, 880)
    win.show()
    qapp.processEvents()
    img = win.grab().toImage()

    # 采样：窗口边距区、导航下方留白区、底边距（历史上为黑色的区域）
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

    # 主题浅色背景整体生效：导航留白区应接近主题底色 #f3f5f8
    c = img.pixelColor(30, 440)
    assert c.red() > 230 and c.green() > 230 and c.blue() > 230, \
        f"留白区非浅色: #{c.red():02x}{c.green():02x}{c.blue():02x}"
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
    s1 = ServerInfo.create(name="p1", host=UNREACHABLE_HOST, port=UNREACHABLE_PORT)
    s2 = ServerInfo.create(name="d1", host=UNREACHABLE_HOST, port=UNREACHABLE_PORT)
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
    ctx.selected = [ServerInfo.create(name="n1", host=UNREACHABLE_HOST, port=UNREACHABLE_PORT)]
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


def test_parallel_panel_run_tasks_one_shot(qapp, tmp_path):
    """run_tasks 一次性回调：不残留、忙碌时拒绝且不排入回调。"""
    from ucm_deployer.core.models import ServerInfo
    from ucm_deployer.gui.widgets.common import ParallelTaskPanel

    info = ServerInfo.create(name="m", host="127.0.0.1", port=1,
                             username="root", password="root")
    panel = ParallelTaskPanel()
    calls = []

    def fn(ssh, tctx):
        tctx.log("hi")
        tctx.progress(50, "half")

    assert panel.run_tasks([(info, fn)], "测试",
                           on_finished=lambda all_ok: calls.append(all_ok)) is True
    assert _wait_panel(panel, qapp)
    assert calls == [True]

    # 忙碌时拒绝：回调不会被排入
    import time as _t

    def slow(ssh, tctx):
        _t.sleep(0.6)

    assert panel.run_tasks([(info, slow)], "忙碌") is True
    rejected_calls = []
    assert panel.run_tasks([(info, fn)], "应被拒绝",
                           on_finished=lambda ok: rejected_calls.append(ok)) is False
    assert _wait_panel(panel, qapp)
    assert rejected_calls == []  # 被拒任务的回调不应触发

    # 完成后可再次运行，回调正常（验证无残留误触发）
    assert panel.run_tasks([(info, fn)], "再跑一次",
                           on_finished=lambda all_ok: calls.append(all_ok)) is True
    assert _wait_panel(panel, qapp)
    assert calls == [True, True]


def test_container_page_per_server_commands(qapp, tmp_path):
    """多服务器时每台服务器有独立命令页签，编辑互不影响。"""
    from PySide6.QtCore import Qt

    from ucm_deployer.core.models import (DeviceInfo, DeviceType, ServerInfo)
    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.gui.state import AppContext
    from ucm_deployer.gui.widgets.container_page import ContainerPage

    ctx = AppContext(ServerRegistry(tmp_path))
    s1 = ServerInfo.create(name="n1", host=UNREACHABLE_HOST, port=UNREACHABLE_PORT)
    s2 = ServerInfo.create(name="n2", host=UNREACHABLE_HOST, port=UNREACHABLE_PORT)
    ctx.selected = [s1, s2]
    ctx.devices = {
        s1.id: DeviceInfo(DeviceType.ASCEND, "Ascend 910B3", 8),
        s2.id: DeviceInfo(DeviceType.ASCEND, "Ascend 910B3", 16),  # 卡数不同
    }
    ctx.images = {s1.id: "img:t1", s2.id: "img:t2"}

    page = ContainerPage(ctx)
    page.on_enter()
    qapp.processEvents()
    page._regen()
    qapp.processEvents()

    assert set(page.cmd_edits.keys()) == {s1.id, s2.id}
    cmd1 = page.command_text(s1.id)
    cmd2 = page.command_text(s2.id)
    assert "--device /dev/davinci7" in cmd1
    assert "--device /dev/davinci15" in cmd2   # 16 卡全量映射
    assert cmd1 != cmd2

    # 编辑服务器1的命令不影响服务器2
    page.cmd_edits[s1.id].setPlainText("docker run -itd --name edited img:t1 bash")
    assert page.command_text(s2.id) == cmd2
    assert page.command_text(s1.id).startswith("docker run -itd --name edited")


def test_deploy_page_regen_confirm(qapp, tmp_path):
    """脚本被编辑后重新生成需要确认：No 保留编辑，Yes 覆盖。"""
    from PySide6.QtWidgets import QMessageBox

    from ucm_deployer.core.models import DeviceInfo, DeviceType, ServerInfo
    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.gui.state import AppContext
    from ucm_deployer.gui.widgets.deploy_page import DeployPage

    ctx = AppContext(ServerRegistry(tmp_path))
    s1 = ServerInfo.create(name="n1", host=UNREACHABLE_HOST, port=UNREACHABLE_PORT)
    ctx.selected = [s1]
    ctx.devices = {s1.id: DeviceInfo(DeviceType.ASCEND, "Ascend 910B3", 8)}
    ctx.containers = {s1.id: "c1"}
    page = DeployPage(ctx)
    page.on_enter()
    page.model_edit.setText("/models/m")
    qapp.processEvents()
    page._generate()
    qapp.processEvents()
    assert ctx.scripts is not None

    first_key = next(iter(page.script_edits))
    page.script_edits[first_key].setPlainText("# edited by user\n")

    # 用户选择 No -> 不覆盖
    orig = QMessageBox.question
    QMessageBox.question = staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.No)
    try:
        page._generate()
    finally:
        QMessageBox.question = orig
    qapp.processEvents()
    assert page.script_edits[first_key].toPlainText() == "# edited by user\n"

    # 用户选择 Yes（默认自动应答）-> 覆盖
    page._generate()
    qapp.processEvents()
    assert page.script_edits[first_key].toPlainText() != "# edited by user\n"


def test_deploy_page_sync_edits(qapp, tmp_path):
    """生成后继续编辑，sync_edits 应把最新内容写回 ctx.scripts。"""
    from ucm_deployer.core.models import DeviceInfo, DeviceType, ServerInfo
    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.gui.state import AppContext
    from ucm_deployer.gui.widgets.deploy_page import DeployPage

    ctx = AppContext(ServerRegistry(tmp_path))
    s1 = ServerInfo.create(name="n1", host=UNREACHABLE_HOST, port=UNREACHABLE_PORT)
    ctx.selected = [s1]
    ctx.devices = {s1.id: DeviceInfo(DeviceType.ASCEND, "Ascend 910B3", 8)}
    ctx.containers = {s1.id: "c1"}
    page = DeployPage(ctx)
    page.on_enter()
    page.model_edit.setText("/models/m")
    qapp.processEvents()
    page._generate()
    qapp.processEvents()

    first = ctx.scripts.scripts[0]
    page.script_edits[f"{first.server_name}:{first.name}"].setPlainText(
        "# user edited content\n")
    assert ctx.scripts.scripts[0].content != "# user edited content\n"
    page.sync_edits()
    assert ctx.scripts.scripts[0].content == "# user edited content\n"


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


def _wait_phases(panel, qapp, timeout=180):
    """等待链式多阶段任务（构建->导出->分发）全部结束：面板需持续空闲 1s。"""
    import time

    deadline = time.time() + timeout
    stable = 0
    while time.time() < deadline:
        qapp.processEvents()
        if panel.is_running():
            stable = 0
        else:
            stable += 1
            if stable >= 20:
                return True
        time.sleep(0.05)
    return False


def test_image_page_build_and_distribute(qapp, tmp_path):
    """步骤2新流程：仅在构建服务器构建一份数据 -> 自动分发到第二台服务器。

    使用 SSH 替身（LocalScriptSSH+MockLinux，按 server.id 各自独立状态），
    断言：构建服务器选择、单行基础镜像下拉、构建后两台服务器均有该镜像
    （目标服务器镜像含 UCM），ctx.images 两台都已记录。
    """
    from ucm_deployer.core.models import ServerInfo
    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.gui.state import AppContext
    from ucm_deployer.gui.widgets.image_page import ImagePage

    ctx = AppContext(ServerRegistry(tmp_path))
    ia = ServerInfo.create(name="build", host="127.0.0.1", port=1,
                           username="root", password="root")
    ib = ServerInfo.create(name="target", host="127.0.0.2", port=1,
                           username="root", password="root")
    for i in (ia, ib):
        ctx.registry.upsert(i)
    ctx.selected = [ia, ib]

    ip = ImagePage(ctx)
    ip.on_enter()
    assert _wait_panel(ip.panel, qapp), "刷新镜像列表超时"
    # 构建服务器下拉列出两台；基础镜像只需选构建服务器的（单行）
    assert ip.build_combo.count() == 2
    assert len(ip.image_rows) == 1
    assert ia.id in ip.image_rows

    whl = str(tmp_path / "uc_manager-0.2.1-py3-none-any.whl")
    open(whl, "wb").write(b"PK fake")
    ip.ucm_whl_edit.setText(whl)
    qapp.processEvents()
    ip._build()
    # 链式阶段：构建 -> 导出 -> 分发（含结束汇总弹窗，已自动应答）
    assert _wait_phases(ip.panel, qapp), "构建/导出/分发链式任务超时"

    tag = "vllm-ascend-ucm:v0.23.0-a3"
    assert ctx.images.get(ia.id) == tag, "构建服务器应记录新镜像"
    assert ctx.images.get(ib.id) == tag, "目标服务器分发成功后应记录镜像"
    # 目标服务器的 docker 里确实有该镜像且含 UCM（替身状态按 server.id 区分）
    assert tag in _FAKE_LINUX[ib.id].images
    assert _FAKE_LINUX[ib.id].images[tag]["has_ucm"]
    assert _FAKE_LINUX[ib.id].images[tag]["ucm_version"] == "0.2.1"

    # 手动再分发构建出的镜像：下拉选中它，目标服务器已有 -> 跳过且不报错
    from PySide6.QtCore import Qt as _Qt

    combo = ip.image_rows[ia.id]
    for i in range(combo.count()):
        if str(combo.itemData(i, _Qt.UserRole) or "") == tag:
            combo.setCurrentIndex(i)
            break
    else:
        raise AssertionError("构建出的镜像应出现在构建服务器的镜像下拉")
    ip._distribute_selected()
    assert _wait_phases(ip.panel, qapp), "手动分发超时"
    assert ctx.images.get(ib.id) == tag


def test_gui_full_flow_with_fake_ssh(qapp, tmp_path):
    """GUI 页面 + ParallelTaskPanel 真实线程 + SSH 替身全链路。

    替身（LocalScriptSSH+MockLinux）提供与模拟服务器相同的命令语义
    （npu-smi/docker build/run/exec/cp/curl），但不建立真实 SSH 连接，
    规避 Qt+paramiko 原生并发崩溃；真实 SSH 全链路由
    tests/test_mock_e2e.py（无 Qt）覆盖。
    """
    import os

    from ucm_deployer.core.models import ServerInfo
    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.gui.state import AppContext
    from ucm_deployer.gui.widgets.container_page import ContainerPage
    from ucm_deployer.gui.widgets.deploy_page import DeployPage
    from ucm_deployer.gui.widgets.image_page import ImagePage
    from ucm_deployer.gui.widgets.server_page import ServerPage

    ctx = AppContext(ServerRegistry(tmp_path))
    info = ServerInfo.create(name="fake-01", host="127.0.0.1", port=1,
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
    # 镜像可输入关键字筛选但只能选中列表项；弹层限高 10 条
    from PySide6.QtWidgets import QComboBox

    from ucm_deployer.gui.widgets.common import combo_ref
    assert combo.isEditable()
    assert combo.insertPolicy() == QComboBox.InsertPolicy.NoInsert
    assert combo.maxVisibleItems() == 10
    assert combo.completer() is not None
    ref = combo_ref(combo)
    assert ref and ":" in ref
    assert "GB" in combo.currentText()

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
    assert _wait_panel(cp.panel, qapp), "进入页面自动刷新镜像超时"
    # 配置区在滚动容器内（小窗口可滚动，全屏布局稳定）
    from PySide6.QtWidgets import QScrollArea
    assert cp.findChild(QScrollArea) is not None
    # 容器页镜像下拉同样支持筛选、只能选列表项且已自动加载
    ccombo = cp.image_rows[info.id]
    assert ccombo.isEditable() and ccombo.maxVisibleItems() == 10
    assert combo_ref(ccombo) == ctx.images[info.id], "应回显已记录的 UCM 镜像"
    cp.kv_add_row.edit.setText("/mnt/nfs_share")
    cp._kv_add()
    cp.model_host.edit.setText("/models/Qwen3-32B")
    qapp.processEvents()
    cp._regen()
    cmd = cp.command_text(info.id)
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
    assert _wait_panel(dp.panel, qapp), "进入页面自动刷新容器超时"
    dp.model_edit.setText("/models/Qwen3-32B")
    qapp.processEvents()
    dp._validate()
    assert "❌" not in dp.validate_label.text()
    dp._generate()
    qapp.processEvents()
    assert ctx.scripts is not None
    assert any(s.role == "serve" for s in ctx.scripts.scripts)
    assert ctx.scripts.health_checks
