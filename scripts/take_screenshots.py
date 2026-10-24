# -*- coding: utf-8 -*-
"""生成用户文档截图：offscreen 渲染 + 模拟数据注入（无需真实服务器）。

用法：.venv\\Scripts\\python scripts\\take_screenshots.py [输出目录]
输出：docs/screenshots/*.png（供 MkDocs 站点引用）
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)


def ensure_cjk_font(app):
    from PySide6.QtGui import QFont, QFontDatabase

    font_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    candidates = [
        font_dir / "msyh.ttc",
        font_dir / "simhei.ttf",
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ]
    for font_path in candidates:
        if not font_path.is_file():
            continue
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        if font_id < 0:
            continue
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            app.setFont(QFont(families[0], 10))
            return True
    return False


def main() -> int:
    import paramiko

    import tests.fake_ssh as fs

    paramiko.SSHClient = fs.FakeSSHClient

    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    from ucm_deployer.gui.theme import apply_light_theme

    apply_light_theme(app)
    ensure_cjk_font(app)

    # 静默弹窗（截图过程不阻塞）
    for name in ("information", "warning", "critical", "about"):
        setattr(QMessageBox, name,
                staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    QMessageBox.question = staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.Yes)

    from ucm_deployer.core.container_manager import ContainerCreateConfig, \
        ContainerManager
    from ucm_deployer.core.models import (DeviceInfo, DeviceType, DockerImage,
                                          ServerInfo, UCMInfo)
    from ucm_deployer.core.precheck import PrecheckItem, PrecheckResult
    from ucm_deployer.gui.main_window import MainWindow

    win = MainWindow()
    win.resize(1420, 920)

    def settle(ms=250):
        deadline = time.time() + 30
        busy = True
        while busy and time.time() < deadline:
            app.processEvents()
            time.sleep(0.03)
            busy = any(p.is_running() for p in win._all_panels()) or \
                bool(win.image_page._quick_tasks) or \
                bool(win.container_page._quick_tasks) or \
                bool(win.precheck_page._quick_tasks)
        for _ in range(ms // 30):
            app.processEvents()
            time.sleep(0.03)

    def shot(widget, name):
        widget.grab().save(str(OUT / f"{name}.png"))
        print("saved", name)

    def goto(row):
        win._nav_buttons[row].setChecked(True)
        win.pages.setCurrentIndex(row)
        win._active_row = row
        settle()

    # ============================================================ 步骤1
    ctx = win.ctx
    s1 = ServerInfo.create(name="npu-node1", host="10.0.10.11",
                           remark="910B3 训练机")
    s1.device = DeviceInfo(DeviceType.ASCEND, "Ascend 910B3", 8, arch="aarch64")
    s2 = ServerInfo.create(name="npu-node2", host="10.0.10.12")
    s3 = ServerInfo.create(name="gpu-node1", host="10.0.20.31",
                           username="ubuntu", remark="A800 推理机")
    for s in (s1, s2, s3):
        ctx.registry.upsert(s)
    ctx.selected = [s1]
    ctx.devices = {s1.id: s1.device}
    win.server_page.refresh()
    win._update_status()
    goto(0)
    shot(win, "step1-servers")

    # 添加服务器对话框
    from ucm_deployer.gui.widgets.server_page import ServerEditDialog

    dlg = ServerEditDialog(win)
    dlg.name_edit.setText("npu-node3")
    dlg.host_edit.setText("10.0.10.13")
    dlg.remark_edit.setText("新到货 910B3")
    dlg.show()
    app.processEvents()
    shot(dlg, "dialog-add-server")
    dlg.close()

    # ============================================================ 步骤2
    ip = win.image_page
    ip._loaded_sid = s1.id
    ip._images = [
        DockerImage("quay.io/ascend/vllm-ascend", "v0.26.0rc1", "id1", "18.2GB",
                    "2026-10-12 10:20:33"),
        DockerImage("vllm-ascend-ucm", "v0.26.0rc1", "id2", "19.0GB",
                    "2026-10-18 09:00:12"),
        DockerImage("quay.io/ascend/vllm-ascend", "v0.23.0-a3", "id3", "16.8GB",
                    "2026-08-01 10:00:00"),
    ]
    ip.placeholder.setVisible(False)
    ip._apply_images()
    ip._update_platform_label()
    ip.registry_ref_edit.setText(
        "quay.io/ascend/vllm-ascend:v0.30.0rc2-a5-openeuler")
    ip.ucm_whl_edit.setText(r"D:\packages\uc_manager-0.8.0.dev20261015-py3-none-any.whl")
    ip.wrapt_whl_edit.setText(r"D:\packages\wrapt-1.17.2-cp39-cp39-manylinux2014_aarch64.whl")
    ip.toolkit_edit.setText(r"D:\packages\ucm_toolkit-0.8.0.dev1-py3-none-any.whl")
    ip._suggest_tag_if_empty()
    ip.match_label.setText("✓ 匹配校验：校验通过：服务器 Ascend 910B3 x8（aarch64）"
                           " 与镜像/安装包匹配\n⚠ 无法从镜像名 repo1 判断平台——（演示略）")
    ip.match_label.setText("✓ 匹配校验：校验通过：服务器 Ascend 910B3 x8（aarch64）"
                           " 与镜像/安装包匹配")
    goto(1)
    shot(win, "step2-build")

    # ============================================================ 步骤3
    cp = win.container_page
    from ucm_deployer.gui.widgets.common import fill_image_combo

    fill_image_combo(cp.image_combo, ip._images,
                     current="quay.io/ascend/vllm-ascend:v0.26.0rc1")
    cp._ucm_state["quay.io/ascend/vllm-ascend:v0.26.0rc1"] = UCMInfo(
        installed=True, version="0.8.0.dev20261015",
        package="uc-manager-cann910-a2")
    cp._show_ucm_state("quay.io/ascend/vllm-ascend:v0.26.0rc1")
    cp.kv_add_row.edit.setText("/mnt/kvcache")
    cp._kv_add()
    cp.model_host.edit.setText("/data/models/Qwen3-32B")
    cp.model_container.setText("/models")
    cp._extra_add()
    cp.extra_table.item(0, 0).setText("/workspace/logs")
    cp.extra_table.item(0, 1).setText("/logs")
    cp._regen()
    goto(2)
    shot(win, "step3-container")

    # 远端目录浏览（容器内）对话框
    from ucm_deployer.gui.widgets.common import RemoteDirDialog

    dirs = RemoteDirDialog(
        win,
        lambda p: ["/mnt/kvcache", "/mnt/nfs_share", "/data", "/models"],
        start="/mnt", title="选择挂载目录（容器内）")
    dirs.show()
    app.processEvents()
    shot(dirs, "dialog-remote-dirs")
    dirs.close()

    # ============================================================ 步骤4
    pp = win.precheck_page
    pp.container_combo.blockSignals(True)
    pp.container_combo.clear()
    pp.container_combo.addItems(["ucm-vllm", "ucm-vllm-old", "sglang-test"])
    pp.container_combo.setCurrentText("ucm-vllm")
    pp.container_combo.blockSignals(False)
    pp._refresh_container_label()
    pp.bandwidth_check.setChecked(True)
    pp.mount_edit.setText("/mnt/kvcache")
    pp._custom_config = False
    pp.config_status_label.setText("预检参数：出厂默认")
    pp._result_items = []
    sample = PrecheckResult(
        exit_code=0, ok=True,
        summary="Result: PASSED (with warnings) |  2 pass, 2 warn, 0 fail, 0 skip, 3 info",
        items=[
            PrecheckItem("INFO", "serving_stack", "vllm=0.26.0, vllm-ascend=0.26.0rc1",
                         name_zh="服务栈版本", advice="信息项：记录 vLLM / vLLM-Ascend / SGLang 实际安装版本，仅展示。"),
            PrecheckItem("INFO", "uc_manager", "uc-manager-cann910-a2=0.8.1",
                         name_zh="UCM 安装版本", advice="信息项：记录 uc-manager（含平台变体包）实际安装版本，仅展示。"),
            PrecheckItem("WARN", "accelerator_driver", "-",
                         detail="threshold: HDK > 25.2.0\nnpu-smi info failed: rc=172\n"
                                "fix: ensure the Ascend driver is loaded and npu-smi is on PATH",
                         name_zh="加速器驱动",
                         advice="在宿主机运行 npu-smi info / nvidia-smi 确认驱动正常；容器内报 "
                                "'device is used (-8020)' 或 rc=172 通常为 NPU 正被其他进程/容器占用，"
                                "停用占用方后重试；确认容器已映射 npu-smi 与驱动目录。"),
            PrecheckItem("PASS", "kernel", "5.10.0", detail="threshold: >= 5.10",
                         name_zh="内核版本", advice="升级操作系统内核至 5.10 及以上系列（按 major.minor 比较）。"),
            PrecheckItem("PASS", "memory_shm", "shm=512.00 GiB (512.00 GiB free)",
                         detail="threshold: >= 512 GiB", name_zh="共享内存 /dev/shm",
                         advice="扩大共享内存至 512 GiB 及以上：容器需 --shm-size 512g"
                                "（本工具生成的命令已默认携带）；宿主机需以 size=512G 挂载 /dev/shm。"),
            PrecheckItem("INFO", "aio_resources", "max-nr=65536 nr=0 available=65536 max_aio_workers=16",
                         name_zh="AIO 资源",
                         advice="信息项：记录内核 AIO 上限与占用；带宽测试 aio 引擎超时时，"
                                "可提高 fs.aio-max-nr（sysctl fs.aio-max-nr=<更大值>）或改用 psync 引擎。"),
            PrecheckItem("WARN", "bandwidth",
                         "dump=4.786  load=6.612  comprehensive=5.699  mixed=5.549 GB/s",
                         detail="threshold: >= 8.0 GB/s\n"
                                "fix: metrics below threshold (dump, load, comprehensive, mixed)",
                         name_zh="存储带宽",
                         advice="检查挂载点所在磁盘/NVMe 的实际吞吐；尝试 aio 引擎或提高 --workers；"
                                "确认 io_direct/O_DIRECT 设置；网络存储（NFS 等）需同时检查网络带宽。"),
        ])
    pp._show_result(sample)
    goto(3)
    shot(win, "step4-precheck")
    win.close()

    # ============================================================ 清理对话框
    from ucm_deployer.gui.widgets.cleanup_dialog import CleanupDialog

    fs.FakeSSHClient.default_scripts.clear()
    fs.FakeSSHClient.default_queue.clear()

    class Ctx2:
        pass

    from ucm_deployer.gui.state import AppContext
    from ucm_deployer.core.server_registry import ServerRegistry
    import tempfile

    ctx2 = AppContext(ServerRegistry(Path(tempfile.mkdtemp())))
    ctx2.selected = [s1]
    ctx2.containers = {s1.id: "ucm-vllm"}

    fs.FakeSSHClient.add_default(
        r"^docker ps -a",
        stdout="id1\tucm-vllm\tvllm-ascend-ucm:v0.26.0rc1\tUp 3 hours\n"
               "id2\tucm-vllm-old\tvllm-ascend-ucm:v0.23.0-a3\tExited (0) 2 days ago\n"
               "id3\tsglang-test\tlmsysorg/sglang:v0.5.9\tUp 20 minutes\n")

    dlg = CleanupDialog(ctx2, "container")
    deadline = time.time() + 15
    while dlg._quick_tasks and time.time() < deadline:
        app.processEvents(); time.sleep(0.05)
    app.processEvents()
    dlg.filter_edit.setText("ucm")
    app.processEvents()
    dlg.show(); app.processEvents()
    shot(dlg, "dialog-cleanup-containers")
    dlg.close()

    fs.FakeSSHClient.default_scripts.clear()
    fs.FakeSSHClient.add_default(
        r"images --format",
        stdout="quay.io/ascend/vllm-ascend\tv0.26.0rc1\tid1\t18.2GB\t2026-10-12 10:20:33\n"
               "vllm-ascend-ucm\tv0.26.0rc1\tid2\t19.0GB\t2026-10-18 09:00:12\n"
               "quay.io/ascend/vllm-ascend\tv0.23.0-a3\tid3\t16.8GB\t2026-08-01 10:00:00\n")
    dlg = CleanupDialog(ctx2, "image")
    deadline = time.time() + 15
    while dlg._quick_tasks and time.time() < deadline:
        app.processEvents(); time.sleep(0.05)
    app.processEvents()
    dlg.show(); app.processEvents()
    shot(dlg, "dialog-cleanup-images")
    dlg.close()

    # ============================================================ 预检参数配置对话框
    fs.FakeSSHClient.default_scripts.clear()
    fs.FakeSSHClient.default_queue.clear()
    fs.FakeSSHClient.add_default(r"test -f /root/ucm-deploy/precheck.json",
                                 exit_code=1, once=True)
    fs.FakeSSHClient.add_default(
        r"python3 -c ",
        stdout="/usr/local/lib/python3.10/site-packages/ucm_toolkit/tools/"
               "precheck/precheck.defaults.json\n", once=True)
    fs.FakeSSHClient.add_default(
        r"cat ",
        stdout='{\n  "mount_path": null,\n  "kernel_min": "5.10",\n'
               '  "cuda_min_compute_cap": 8.0,\n  "ascend_min_hdk": "25.2.0",\n'
               '  "shm_min_gib": 512.0,\n  "bandwidth": {\n'
               '    "shard_sizes": ["180k", "8m"],\n'
               '    "worker_counts": [1, 8, 16],\n'
               '    "engines": ["psync", "aio"],\n'
               '    "block_number": 32,\n    "threshold_gb": 8.0\n  }\n}',
        once=True)

    from ucm_deployer.gui.widgets.precheck_config_dialog import PrecheckConfigDialog

    dlg = PrecheckConfigDialog(ctx2, "ucm-vllm")
    deadline = time.time() + 15
    while dlg._quick_tasks and time.time() < deadline:
        app.processEvents(); time.sleep(0.05)
    app.processEvents()
    dlg.show(); app.processEvents()
    shot(dlg, "dialog-precheck-config")
    dlg.close()

    print("done:", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
