# -*- coding: utf-8 -*-
"""步骤5 批量部署页 GUI 测试（offscreen；多选、模板导入导出、批量执行）。"""
import json
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
    from PySide6.QtWidgets import QApplication

    from ucm_deployer.gui.widgets.common import ParallelTaskPanel, _task_threads
    for w in QApplication.topLevelWidgets():
        for panel in w.findChildren(ParallelTaskPanel):
            panel.shutdown()
    for t in list(_task_threads):
        t.join(15.0)
    qapp.processEvents()


def _wait_panel(panel, qapp, timeout=60):
    deadline = time.time() + timeout
    while panel.is_running() and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.05)
    qapp.processEvents()
    return not panel.is_running()


def make_ctx(tmp_path):
    from ucm_deployer.core import crypto
    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.gui.state import AppContext
    from ucm_deployer.utils import paths

    paths.default_config_dir = lambda: tmp_path
    crypto._key_path = lambda: tmp_path / ".secret.key"
    return AppContext(ServerRegistry(tmp_path))


def make_page(qapp, tmp_path):
    from ucm_deployer.gui.widgets.batch_page import BatchPage

    ctx = make_ctx(tmp_path)
    page = BatchPage(ctx)
    page.resize(1000, 860)
    return page


def add_servers(ctx, count=2):
    from ucm_deployer.core.models import ServerInfo

    servers = []
    for i in range(count):
        s = ServerInfo.create(name=f"node-{i + 1}", host=f"10.0.0.1{i + 1}",
                              username="root", password=f"pw{i + 1}")
        ctx.registry.upsert(s)
        servers.append(ctx.registry.get(s.id))
    return servers


def check_target(page, server, qapp, checked=True):
    from PySide6.QtCore import Qt

    for row in range(page.target_table.rowCount()):
        name = page.target_table.item(row, 1).text()
        if name == server.name:
            item = page.target_table.item(row, 0)
            page.target_table.itemChanged.disconnect(page._on_target_item_changed)
            item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            if checked:
                page._target_ids.add(server.id)
            else:
                page._target_ids.discard(server.id)
            page.target_table.itemChanged.connect(page._on_target_item_changed)
            page._update_target_count()
            return


# ============================================================ 页面与导航
def test_main_window_has_batch_step(qapp):
    from ucm_deployer.gui.main_window import MainWindow

    win = MainWindow()
    win.show()
    qapp.processEvents()
    win.goto_step(4)
    qapp.processEvents()
    assert win.current_step() == 4
    assert win.batch_page is not None
    assert "批量部署" in win._nav_buttons[4].text()
    win.close()


def test_batch_page_fits_one_screen(qapp, tmp_path):
    from ucm_deployer.gui.main_window import MainWindow

    win = MainWindow()
    win.resize(1280, 860)
    win.show()
    qapp.processEvents()
    win.goto_step(4)
    qapp.processEvents()
    page = win.batch_page
    available = win.centralWidget().height()
    assert page.minimumSizeHint().height() <= available, \
        f"步骤5 最小高度 {page.minimumSizeHint().height()}px 超过可用 {available}px"
    assert page.panel.height() >= 100
    win.close()


def test_batch_page_multi_select(qapp, tmp_path):
    page = make_page(qapp, tmp_path)
    servers = add_servers(page.ctx, 3)
    page.refresh_servers()
    qapp.processEvents()

    assert page.target_table.rowCount() == 3
    assert page._selected_targets() == []

    check_target(page, servers[0], qapp)
    check_target(page, servers[2], qapp)
    qapp.processEvents()
    targets = page._selected_targets()
    assert {t.name for t in targets} == {"node-1", "node-3"}
    assert page.target_count_label.text() == "已选 2 台"

    # 全选 / 清空
    page._set_all_targets(True)
    assert len(page._selected_targets()) == 3
    page._set_all_targets(False)
    assert page._selected_targets() == []


def test_batch_page_run_requires_targets(qapp, tmp_path):
    page = make_page(qapp, tmp_path)
    page.image_ref_edit.setText("ucm-vllm:0.3.6")
    page._run()      # 无目标（自动应答提示）后返回
    assert not page.panel.is_running()
    assert page._reports == {}


def test_batch_page_on_enter_prefills_from_context(qapp, tmp_path):
    page = make_page(qapp, tmp_path)
    servers = add_servers(page.ctx, 1)
    page.ctx.selected = [servers[0]]
    page.ctx.images[servers[0].id] = "ucm-vllm:0.3.6"
    page.on_enter()
    qapp.processEvents()
    assert page.image_ref_edit.text() == "ucm-vllm:0.3.6"
    assert page._source_index_of(servers[0].id) == page.source_combo.currentIndex()


# ============================================================ 模板导出/导入
def test_batch_page_template_export_import_roundtrip(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    page = make_page(qapp, tmp_path)
    servers = add_servers(page.ctx, 2)
    page.refresh_servers()
    check_target(page, servers[0], qapp)
    check_target(page, servers[1], qapp)
    page.source_combo.setCurrentIndex(
        page._source_index_of(servers[0].id))

    # 填充各配置
    page.image_ref_edit.setText("ucm-vllm:0.4.0")
    page.skip_existing_check.setChecked(False)
    page.recreate_check.setChecked(True)
    page.name_edit.setText("ucm-batch")
    page.shm_edit.setText("256g")
    page.kv_edit.setText("/mnt/nvme1")
    page._kv_add()
    page.kv_edit.setText("/mnt/nvme2")
    page._kv_add()
    page.model_host_edit.setText("/data/models")
    page.model_container_edit.setText("/models")
    page.mount_edit.setText("/mnt/nvme1")
    page.custom_cfg_check.setChecked(True)
    page.custom_cfg_edit.setPlainText('{"bandwidth": {"workers": 16}}')
    page.stage_precheck_check.setChecked(False)

    path = str(tmp_path / "tpl.json")
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (path, "")))
    page._export_template()
    assert os.path.isfile(path)

    # 新页面（同注册表）导入
    from ucm_deployer.gui.widgets.batch_page import BatchPage

    page2 = BatchPage(make_ctx(tmp_path))
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (path, "")))
    page2._import_template()
    qapp.processEvents()

    assert page2.image_ref_edit.text() == "ucm-vllm:0.4.0"
    assert page2.skip_existing_check.isChecked() is False
    assert page2.recreate_check.isChecked() is True
    assert page2.name_edit.text() == "ucm-batch"
    assert page2.shm_edit.text() == "256g"
    assert [page2.kv_list.item(i).text()
            for i in range(page2.kv_list.count())] == ["/mnt/nvme1", "/mnt/nvme2"]
    assert page2.model_host_edit.text() == "/data/models"
    assert page2.mount_edit.text() == "/mnt/nvme1"
    assert page2.custom_cfg_check.isChecked() is True
    assert json.loads(page2.custom_cfg_edit.toPlainText()) == {
        "bandwidth": {"workers": 16}}
    assert page2.stage_precheck_check.isChecked() is False
    # 导入的服务器自动勾选为目标
    assert len(page2._selected_targets()) == 2
    # 源服务器按模板 source_host 匹配选中
    assert page2._selected_source() is not None
    assert page2._selected_source().host == servers[0].host


def test_batch_page_import_template_bad_file(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    page = make_page(qapp, tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(bad), "")))
    page._import_template()      # 提示导入失败（自动应答）后返回
    assert page.image_ref_edit.text() == ""


# ============================================================ 批量执行
_NPU_SMI = """+--------------------------------+
| npu-smi 24.1.rc2               |
+===+============+===============+
| NPU     Name   | Health        |
|=================+===============|
| 0       910B3  | OK            |
| 1       910B3  | OK            |
+--------------------------------+"""

_PRECHECK_OUT = (
    "[INFO] serving_stack  vLLM 0.10.0\n"
    "[PASS] kernel  5.10.0\n"
    "[PASS] memory_shm  512 GiB\n"
    "Result: PASSED  |  2 pass, 0 warn, 0 fail\n"
)


def _script_common(fake_ssh):
    fake_ssh.add_default(r"^uname -m", stdout="aarch64\n")
    fake_ssh.add_default(r"^npu-smi info", stdout=_NPU_SMI)
    fake_ssh.add_default(r"for d in /dev/davinci", stdout="/dev/davinci0\n")
    fake_ssh.add_default(r"for p in /usr/local",
                         stdout="/usr/local/Ascend/driver\n")
    fake_ssh.add_default(r"docker run --rm", stdout="uc-manager==1.2.3\n")
    fake_ssh.add_default(r"^docker run -itd", stdout="container-id\n")
    fake_ssh.add_default(r"ucm-toolkit run precheck", stdout=_PRECHECK_OUT)


def test_batch_page_run_pull_mode_two_targets(qapp, tmp_path, fake_ssh):
    page = make_page(qapp, tmp_path)
    servers = add_servers(page.ctx, 2)
    page.refresh_servers()
    check_target(page, servers[0], qapp)
    check_target(page, servers[1], qapp)

    fake_ssh.add_default(r"^docker pull", stdout="Status: Downloaded\n")
    fake_ssh.add_default(r"docker image inspect", stdout="")
    _script_common(fake_ssh)

    page.mode_combo.setCurrentIndex(1)      # 镜像仓库拉取
    page.image_ref_edit.setText("quay.io/x/ucm-vllm:0.4.0")
    page._run()
    assert page.panel.is_running()
    assert _wait_panel(page.panel, qapp)

    # 两台各自完成 pull + docker run + 预检
    assert set(page._reports) == {s.id for s in servers}
    for rep in page._reports.values():
        assert rep.container == "ucm-vllm"
        assert rep.precheck_verdict == "passed"
    cmds = []
    for client in fake_ssh.instances:
        cmds.extend(c.executed_command for c in client.channels)
    pull_cmds = [c for c in cmds if c.startswith("docker pull")]
    run_cmds = [c for c in cmds if c.startswith("docker run -itd")]
    precheck_cmds = [c for c in cmds if "ucm-toolkit run precheck" in c]
    assert len(pull_cmds) == 2
    assert len(run_cmds) == 2
    assert len(precheck_cmds) == 2
    assert all("quay.io/x/ucm-vllm:0.4.0" in c for c in run_cmds)
    # 状态行/日志按服务器分列：面板为每台目标建了行与页签
    assert page.panel.table.rowCount() == 2
    assert page.panel.tabs.count() == 2


def test_batch_page_run_tar_mode_with_source_row(qapp, tmp_path, fake_ssh):
    page = make_page(qapp, tmp_path)
    servers = add_servers(page.ctx, 2)      # 0=源，1=目标
    page.refresh_servers()
    check_target(page, servers[1], qapp)

    fake_ssh.add_default(r"docker image inspect", stdout="")
    fake_ssh.add_default(r"bash -c", stdout="")            # docker save 导出
    fake_ssh.add_default(r"test -s", stdout="")            # 导出文件非空校验
    fake_ssh.add_default(r"rm -f", stdout="")
    fake_ssh.add_default(r"^docker load", stdout="Loaded image: ucm-vllm:0.4.0\n")
    _script_common(fake_ssh)

    # 源服务器选择第一台
    index = page._source_index_of(servers[0].id)
    page.source_combo.setCurrentIndex(index)
    page.image_ref_edit.setText("ucm-vllm:0.4.0")
    page.skip_existing_check.setChecked(False)             # 强制走上传/导入路径
    page._run()
    assert page.panel.is_running()
    assert _wait_panel(page.panel, qapp)

    # 源 + 目标两行（首行为源服务器准备镜像包）
    assert page.panel.table.rowCount() == 2
    assert page._reports[servers[1].id].image == "ucm-vllm:0.4.0"
    assert page._reports[servers[1].id].precheck_verdict == "passed"

    cmds = []
    for client in fake_ssh.instances:
        cmds.extend(c.executed_command for c in client.channels)
    assert any("docker save" in c and "gzip -1" in c for c in cmds)
    assert any(c.startswith("docker load -i /tmp/ucm-batch-") for c in cmds)
    assert any(c.startswith("docker run -itd") for c in cmds)
    # 镜像包在本机临时目录（执行完成后已清理）
    assert page._local_tar == ""


def test_batch_page_run_tar_source_also_target(qapp, tmp_path, fake_ssh):
    """源服务器同时在目标列表：镜像分发自动跳过，容器/预检照常执行。"""
    page = make_page(qapp, tmp_path)
    servers = add_servers(page.ctx, 2)      # 0=源+目标，1=目标
    page.refresh_servers()
    check_target(page, servers[0], qapp)
    check_target(page, servers[1], qapp)

    fake_ssh.add_default(r"docker image inspect", stdout="")
    fake_ssh.add_default(r"bash -c", stdout="")
    fake_ssh.add_default(r"test -s", stdout="")
    fake_ssh.add_default(r"rm -f", stdout="")
    _script_common(fake_ssh)

    index = page._source_index_of(servers[0].id)
    page.source_combo.setCurrentIndex(index)
    page.image_ref_edit.setText("ucm-vllm:0.4.0")
    page._run()
    assert _wait_panel(page.panel, qapp)

    # 源服务器两行（准备镜像包 + 自身部署），互不覆盖
    assert page.panel.table.rowCount() == 3
    assert set(page._reports) == {s.id for s in servers}
    # 源服务器自身跳过镜像分发（镜像已在本机）
    assert page._reports[servers[0].id].image_skipped is True
    assert page._reports[servers[0].id].container == "ucm-vllm"
    assert page._reports[servers[0].id].precheck_verdict == "passed"
    # 目标 1 未跳过（执行了上传/导入路径？此处 image inspect 恒成功 -> 跳过）
    assert page._reports[servers[1].id].image_skipped is True


def test_batch_page_run_stage_subset_only_image(qapp, tmp_path, fake_ssh):
    """仅勾选「镜像分发」：不创建容器、不预检。"""
    page = make_page(qapp, tmp_path)
    servers = add_servers(page.ctx, 1)
    page.refresh_servers()
    check_target(page, servers[0], qapp)

    fake_ssh.add_default(r"^docker pull", stdout="Status: Downloaded\n")
    fake_ssh.add_default(r"docker image inspect", stdout="")

    page.mode_combo.setCurrentIndex(1)
    page.image_ref_edit.setText("quay.io/x/ucm-vllm:0.4.0")
    page.stage_container_check.setChecked(False)
    page.stage_precheck_check.setChecked(False)
    page._run()
    assert _wait_panel(page.panel, qapp)

    rep = page._reports[servers[0].id]
    assert rep.container == ""
    assert rep.precheck_verdict == ""
    cmds = []
    for client in fake_ssh.instances:
        cmds.extend(c.executed_command for c in client.channels)
    assert not any(c.startswith("docker run -itd") for c in cmds)
    assert not any("ucm-toolkit run precheck" in c for c in cmds)
