# -*- coding: utf-8 -*-
"""端到端自检：启动模拟服务器 -> 走完「探测/构建/容器/部署/拉起/停止」全流程。

同时被 `cli.py self-test` 与 tests/test_mock_e2e.py 复用。
"""
from __future__ import annotations

import os
import tempfile
from typing import Callable, List, Optional, Tuple

from ..core.command_generator import CommandGenerator
from ..core.container_manager import ContainerCreateConfig, ContainerManager, check_shared_fs
from ..core.device_detector import DeviceDetector
from ..core.device_detector import DeviceType  # noqa: F401  (re-export)
from ..core.docker_manager import DockerManager
from ..core.image_builder import ImageBuildConfig, ImageBuilder, suggest_tag
from ..core.models import DeviceType as _DT
from ..core.models import ServerInfo, VolumeMount
from ..core.ssh_client import SSHClient
from ..core.topology import DeployMode, DeployPlan, NodePlan, NodeRole, validate_plan
from ..service import deploy_service
from ..utils.log import get_logger
from .mock_server import MockSSHServer

logger = get_logger(__name__)


def _make_fake_whl(directory: str, name: str) -> str:
    path = os.path.join(directory, name)
    with open(path, "wb") as fh:
        fh.write(b"PK\x03\x04" + b"0" * 4096)
    return path


def run_full_flow(host: str, port: int, username: str, password: str,
                  report: List[str], device: str = "ascend",
                  progress: Optional[Callable[[str], None]] = None,
                  log: Optional[Callable[[str], None]] = None) -> bool:
    """对指定服务器（通常为模拟服务器）执行完整部署流程，报告写入 report。"""
    progress = progress or (lambda msg: None)
    log = log or (lambda line: None)
    results: List[Tuple[bool, str, str]] = []  # (ok, step, detail)

    def step(name: str, fn) -> bool:
        progress(name)
        try:
            detail = str(fn() or "")
            results.append((True, name, detail))
            report.append(f"[通过] {name}" + (f" - {detail}" if detail else ""))
            return True
        except Exception as exc:
            results.append((False, name, str(exc)))
            report.append(f"[失败] {name} - {exc}")
            return False

    info = ServerInfo.create(name="mock-server", host=host, port=port,
                             username=username, password=password)
    ssh = SSHClient(info)

    device_type = _DT.ASCEND if device == "ascend" else _DT.NVIDIA
    base_image = ("quay.io/ascend/vllm-ascend:v0.23.0-a3" if device == "ascend"
                  else "vllm/vllm-openai:latest")

    state: dict = {}

    def _connect():
        ssh.connect()
        return f"{host}:{port}"

    def _detect():
        info_ = DeviceDetector(ssh).detect()
        state["device"] = info_
        if info_.device_type == _DT.UNKNOWN or info_.count <= 0:
            raise RuntimeError("未探测到加速设备")
        return info_.display

    def _docker():
        dm = DockerManager(ssh)
        if not dm.check_docker():
            raise RuntimeError("docker 不可用")
        state["docker"] = dm
        return f"{len(dm.list_images())} 个镜像"

    def _build():
        workdir = tempfile.mkdtemp(prefix="ucm-selftest-")
        ucm_whl = _make_fake_whl(workdir, "uc_manager-0.2.1-py3-none-any.whl")
        wrapt_whl = _make_fake_whl(workdir, "wrapt-1.16.0-cp39-cp39-manylinux2014_x86_64.whl")
        cfg = ImageBuildConfig(base_image=base_image,
                               image_tag=suggest_tag(base_image),
                               ucm_whl_local=ucm_whl, wrapt_whl_local=wrapt_whl,
                               offline=True,
                               platform="ascend" if device == "ascend" else "cuda")
        result = ImageBuilder(ssh, state["docker"]).build(
            cfg, progress=lambda p, m: progress(f"构建 {p}% {m}"), log=log)
        state["image"] = result.image
        return result.image

    def _image_ucm():
        ucm = state["docker"].image_ucm_info(state["image"])
        if not ucm.installed:
            raise RuntimeError(f"镜像内未检测到 UCM: {ucm}")
        return f"uc-manager {ucm.version}"

    def _shared_fs():
        report_ = check_shared_fs({"mock": ssh}, ["/mnt/nfs_share"])
        if not report_.ok:
            raise RuntimeError(report_.message)
        return report_.message

    def _create_container():
        cm = ContainerManager(ssh, state["docker"])
        cards = state["device"].count
        cfg = ContainerCreateConfig(
            image=state["image"], name="ucm-vllm",
            device_type=state["device"].device_type, device_count=cards,
            kv_cache_dirs=["/mnt/nfs_share"],
            model_mount=VolumeMount("/models/Qwen3-32B", "/models", readonly=True),
        )
        cmd = cm.generate_run_command(cfg)
        res = cm.create(cmd, on_line=log)
        state["container"] = cfg.name
        return f"{cfg.name} (exit={res.exit_code})"

    def _container_ucm():
        ucm = state["docker"].container_ucm_info(state["container"])
        if not ucm.installed:
            raise RuntimeError(f"容器内未检测到 UCM: {ucm}")
        return f"uc-manager {ucm.version}"

    def _generate():
        node = NodePlan(server_id=info.id, server_name=info.name, host=host,
                        role=NodeRole.MIXED, dp=1, tp=state["device"].count,
                        container=state["container"], cards=state["device"].count)
        plan = DeployPlan(mode=DeployMode.COLOCATED, engine="vllm",
                          device_type=state["device"].device_type,
                          nodes=[node],
                          model_path="/models/Qwen3-32B",
                          served_model_name="qwen3",
                          kv_cache_dir="/mnt/nfs_share",
                          extra_args="--max-model-len 17000")
        issues = [i for i in validate_plan(plan) if i.is_error]
        if issues:
            raise RuntimeError("拓扑校验失败: " + "; ".join(i.message for i in issues))
        state["plan"] = plan
        ds = CommandGenerator(plan).generate()
        state["ds"] = ds
        return f"{len(ds.scripts)} 个脚本, {len(ds.config_files)} 个配置"

    def _deploy():
        ds = state["ds"]
        deploy_service.deploy_files(ssh, state["docker"], state["container"],
                                    ds.scripts + ds.config_files)
        return "脚本已落位 /root/ucm-deploy"

    def _launch():
        for s in deploy_service.ordered_scripts(state["ds"]):
            deploy_service.launch_script(ssh, state["docker"], state["container"], s)
        return "已拉起 " + ", ".join(s.name for s in state["ds"].scripts)

    def _health():
        failures = []
        for hc in state["ds"].health_checks:
            ok, msg = deploy_service.health_check(ssh, hc.url)
            if not ok:
                failures.append(f"{hc.label}: {msg}")
        if failures:
            raise RuntimeError("健康检查未通过: " + "; ".join(failures))
        return f"{len(state['ds'].health_checks)} 个服务均 200"

    def _logs():
        s = state["ds"].scripts[0]
        text = deploy_service.tail_log(ssh, state["docker"], state["container"],
                                       s.log_path)
        if "Application startup complete" not in text:
            raise RuntimeError("日志中未见启动完成标志")
        return "日志包含 Application startup complete"

    def _running():
        s = state["ds"].scripts[0]
        if not deploy_service.script_running(ssh, state["docker"], state["container"], s):
            raise RuntimeError("进程未在运行")
        return "进程运行中"

    def _stop():
        for s in state["ds"].scripts:
            deploy_service.stop_script(ssh, state["docker"], state["container"], s)
        hc = state["ds"].health_checks[0]
        ok, msg = deploy_service.health_check(ssh, hc.url)
        if ok:
            raise RuntimeError("停止后健康检查仍返回 200")
        return "服务已停止"

    steps = [
        ("1. SSH 连接", _connect),
        ("2. 设备探测", _detect),
        ("3. Docker 检查", _docker),
        ("4. 构建 UCM 镜像", _build),
        ("5. 镜像 UCM 校验", _image_ucm),
        ("6. 共享文件系统校验", _shared_fs),
        ("7. 创建容器", _create_container),
        ("8. 容器 UCM 校验", _container_ucm),
        ("9. 生成部署脚本", _generate),
        ("10. 部署脚本", _deploy),
        ("11. 拉起服务", _launch),
        ("12. 健康检查", _health),
        ("13. 日志检查", _logs),
        ("14. 进程检查", _running),
        ("15. 停止服务", _stop),
    ]
    for name, fn in steps:
        if not step(name, fn):
            break
    ssh.close()
    return all(ok for ok, _, _ in results) and len(results) == len(steps)


def run_selftest(device: str = "ascend", cards: int = 8,
                 progress: Optional[Callable[[str], None]] = None,
                 log: Optional[Callable[[str], None]] = None) -> Tuple[bool, List[str]]:
    """启动模拟服务器并执行完整流程（自检入口）。"""
    server = MockSSHServer(device=device, cards=cards)
    port = server.start()
    report: List[str] = [f"模拟服务器: 127.0.0.1:{port} ({device} x{cards})"]
    try:
        ok = run_full_flow("127.0.0.1", port, server.username, server.password,
                           report, device=device, progress=progress, log=log)
    finally:
        server.stop()
    report.append("结论: " + ("全部通过 ✓" if ok else "存在失败 ✗"))
    return ok, report
