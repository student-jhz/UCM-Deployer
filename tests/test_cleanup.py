# -*- coding: utf-8 -*-
"""清理容器/镜像功能测试（工具菜单 + CleanupDialog，FakeSSH 注入）。"""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")


def _automate_message_boxes() -> None:
    from PySide6.QtWidgets import QMessageBox

    for name in ("information", "warning", "critical", "about"):
        setattr(QMessageBox, name,
                staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    QMessageBox.question = staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.Yes)
    QMessageBox.exec = lambda self, *a, **k: QMessageBox.StandardButton.Ok  # noqa: A001


_automate_message_boxes()


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _shutdown_tasks_after_test(qapp):
    yield
    from ucm_deployer.gui.widgets.common import _task_threads

    for t in list(_task_threads):
        t.join(15.0)
    qapp.processEvents()


def _wait_tasks(obj, qapp, timeout=15):
    """等待对象的 QuickTask 全部结束。"""
    deadline = time.time() + timeout
    while obj._quick_tasks and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.05)
    qapp.processEvents()
    return not obj._quick_tasks


def _make_ctx(tmp_path):
    from ucm_deployer.core.models import ServerInfo
    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.gui.state import AppContext

    ctx = AppContext(ServerRegistry(tmp_path))
    ctx.selected = [ServerInfo.create(name="srv", host="127.0.0.1")]
    return ctx


# ============================================================ 主窗口菜单
def test_main_window_tool_menu(qapp, tmp_path):
    from ucm_deployer.gui.main_window import MainWindow

    win = MainWindow()
    tool_titles = [m.text() for m in win.menuBar().actions()]
    assert "工具(&T)" in tool_titles
    tool = next(m for m in win.menuBar().actions() if m.text() == "工具(&T)")
    acts = [a.text() for a in tool.menu().actions()]
    assert "清理容器..." in acts
    assert "清理镜像..." in acts
    # 未登录服务器：菜单动作弹提示（自动应答）不崩
    win.ctx.selected = []
    win._open_cleanup("container")
    win._open_cleanup("image")
    qapp.processEvents()
    win.close()


# ============================================================ 容器清理
def test_cleanup_dialog_loads_containers(qapp, tmp_path, fake_ssh):
    from ucm_deployer.gui.widgets.cleanup_dialog import CleanupDialog

    fake_ssh.add_default(
        r"^docker ps -a",
        stdout="id1\tctr1\timg:1\tUp 2 hours\n"
               "id2\tctr2\timg:2\tExited (0) 5 minutes ago\n")

    ctx = _make_ctx(tmp_path)
    dlg = CleanupDialog(ctx, "container")
    assert _wait_tasks(dlg, qapp)
    assert dlg.table.rowCount() == 2
    assert dlg.table.item(0, 1).text() == "ctr1"
    assert dlg.table.item(0, 3).text().startswith("Up")
    assert dlg.table.item(1, 1).text() == "ctr2"


def test_cleanup_dialog_deletes_containers(qapp, tmp_path, fake_ssh):
    from ucm_deployer.gui.widgets.cleanup_dialog import CleanupDialog

    # 一次性队列：首次加载 2 个容器 -> 删除 -> 再次加载为空
    fake_ssh.add_default(
        r"^docker ps -a",
        stdout="id1\tctr1\timg:1\tUp 2 hours\nid2\tctr2\timg:2\tExited\n",
        once=True)
    fake_ssh.add_default(r"^docker rm -f ", stdout="ctr1\nctr2\n")
    fake_ssh.add_default(r"^docker ps -a", stdout="", once=True)

    ctx = _make_ctx(tmp_path)
    dlg = CleanupDialog(ctx, "container")
    assert _wait_tasks(dlg, qapp)
    # 勾选两行 -> 删除（确认自动应答 Yes）
    from PySide6.QtCore import Qt

    dlg.table.item(0, 0).setCheckState(Qt.Checked)
    dlg.table.item(1, 0).setCheckState(Qt.Checked)
    dlg._delete_selected()
    assert _wait_tasks(dlg, qapp)

    # 实际发出的 docker rm 命令（在任一连接的 channel 里查找）
    rm_cmds = [c.executed_command for inst in fake_ssh.instances
               for c in inst.channels
               if c.executed_command.startswith("docker rm -f")]
    assert rm_cmds == ["docker rm -f ctr1 ctr2"]
    # 删除后自动刷新（列表清空）
    assert dlg.table.rowCount() == 0


# ============================================================ 镜像清理
def test_cleanup_dialog_loads_and_deletes_images(qapp, tmp_path, fake_ssh):
    from ucm_deployer.gui.widgets.cleanup_dialog import CleanupDialog

    # 一次性队列：首次加载 2 个镜像 -> 删除 repo1:v1 -> 再加载剩 1 个
    fake_ssh.add_default(
        r"images --format",
        stdout="repo1\tv1\tid1\t18GB\t2026-08-01 10:00:00\n"
               "repo2\tv2\tid2\t9GB\t2026-07-01 09:00:00\n",
        once=True)
    fake_ssh.add_default(r"^docker rmi -f ", stdout="Untagged: repo1:v1\n")
    fake_ssh.add_default(
        r"images --format", stdout="repo2\tv2\tid2\t9GB\t2026-07-01\n",
        once=True)

    ctx = _make_ctx(tmp_path)
    dlg = CleanupDialog(ctx, "image")
    assert _wait_tasks(dlg, qapp)
    assert dlg.table.rowCount() == 2
    assert dlg.table.item(0, 1).text() == "repo1:v1"
    assert dlg.table.item(0, 3).text() == "18GB"

    from PySide6.QtCore import Qt

    dlg.table.item(0, 0).setCheckState(Qt.Checked)
    dlg._delete_selected()
    assert _wait_tasks(dlg, qapp)

    rmi_cmds = [c.executed_command for inst in fake_ssh.instances
                for c in inst.channels
                if c.executed_command.startswith("docker rmi -f")]
    assert rmi_cmds == ["docker rmi -f repo1:v1"]
    # 删除后自动刷新（剩 1 个）
    assert dlg.table.rowCount() == 1
    assert dlg.table.item(0, 1).text() == "repo2:v2"


def test_cleanup_dialog_no_selection_prompt(qapp, tmp_path, fake_ssh):
    from ucm_deployer.gui.widgets.cleanup_dialog import CleanupDialog

    fake_ssh.add_default(r"images --format", stdout="repo1\tv1\tid1\t1GB\tt\n")
    ctx = _make_ctx(tmp_path)
    dlg = CleanupDialog(ctx, "image")
    assert _wait_tasks(dlg, qapp)
    dlg._delete_selected()      # 未勾选 -> 提示（自动应答）后返回
    qapp.processEvents()
    rmi_cmds = [c.executed_command for inst in fake_ssh.instances
                for c in inst.channels
                if c.executed_command.startswith("docker rmi")]
    assert not rmi_cmds
