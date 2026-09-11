# -*- coding: utf-8 -*-
"""部署编排服务：把 core 能力组合成 GUI/CLI 可直接调用的操作。

脚本落位约定：
- 容器内 /root/ucm-deploy/     脚本与配置
- 容器内 /root/ucm-deploy/logs/  各脚本日志
拉起方式：宿主机 ssh -> docker exec -d <容器> bash -c 'cd /root/ucm-deploy && nohup bash x.sh > logs/x.log 2>&1'
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..core.command_generator import LOG_DIR, REMOTE_DIR, DeploymentScripts, NodeScript
from ..core.docker_manager import DockerManager
from ..core.shell import shq
from ..core.ssh_client import SSHClient
from ..utils.log import get_logger

logger = get_logger(__name__)

# 拉起顺序（依赖优先）
LAUNCH_ORDER = [
    "mooncake-master",
    "ray-head",
    "ray-worker",
    "prefill",
    "decode",
    "serve",
    "serve_dp",
    "load-balancer",
]


def ordered_scripts(ds: DeploymentScripts) -> List[NodeScript]:
    """按依赖顺序排列可执行脚本。"""

    def sort_key(s: NodeScript):
        try:
            return LAUNCH_ORDER.index(s.role)
        except ValueError:
            return len(LAUNCH_ORDER)

    return sorted(ds.scripts, key=sort_key)


def scripts_by_server(ds: DeploymentScripts) -> Dict[str, List[NodeScript]]:
    grouped: Dict[str, List[NodeScript]] = {}
    for s in ordered_scripts(ds):
        grouped.setdefault(s.server_id, []).append(s)
    return grouped


def config_files_by_server(ds: DeploymentScripts) -> Dict[str, List[NodeScript]]:
    grouped: Dict[str, List[NodeScript]] = {}
    for s in ds.config_files:
        grouped.setdefault(s.server_id, [])
        if s not in grouped[s.server_id]:
            grouped[s.server_id].append(s)
    return grouped


def deploy_files(ssh: SSHClient, docker: DockerManager, container: str,
                 scripts: List[NodeScript]) -> None:
    """把脚本/配置写入容器（宿主机临时文件 + docker cp）。"""
    if not scripts:
        return
    res = docker.exec_in_container(container, f"mkdir -p {REMOTE_DIR} {LOG_DIR}",
                                   timeout=30)
    if not res.ok:
        raise RuntimeError(f"容器 {container} 内创建目录失败: {res.stderr.strip()[:200]}")
    for s in scripts:
        tmp = f"/tmp/ucm_deploy_{s.name}"
        ssh.write_file(tmp, s.content)
        docker.copy_to_container(tmp, container, s.path)
        ssh.exec(f"rm -f {shq(tmp)}", timeout=30)
        logger.info("已部署 %s -> %s:%s", s.name, container, s.path)


def launch_script(ssh: SSHClient, docker: DockerManager, container: str,
                  script: NodeScript) -> None:
    """后台拉起容器内脚本。"""
    cmd = (f"cd {REMOTE_DIR} && nohup bash {shq(script.path)} "
           f"> {shq(script.log_path)} 2>&1")
    res = docker.exec_in_container(container, cmd, detach=True, timeout=60)
    if not res.ok:
        raise RuntimeError(f"拉起 {script.name} 失败: {res.stderr.strip()[:200]}")
    logger.info("已拉起 %s @ %s (日志: %s)", script.name, container, script.log_path)


def tail_log(ssh: SSHClient, docker: DockerManager, container: str,
             log_path: str, lines: int = 200) -> str:
    """读取容器内日志尾部。"""
    res = docker.exec_in_container(container,
                                   f"tail -n {int(lines)} {shq(log_path)}",
                                   timeout=60)
    return (res.stdout or "") + (("\n[stderr] " + res.stderr) if res.stderr.strip() else "")


def script_running(ssh: SSHClient, docker: DockerManager, container: str,
                   script: NodeScript) -> bool:
    """脚本对应进程是否仍在运行。"""
    patterns = [script.path]
    if script.role in ("serve", "serve_dp", "prefill", "decode"):
        patterns.append("vllm serve")
    elif script.role == "load-balancer":
        patterns.append("load_balance_proxy_server")
    elif script.role == "mooncake-master":
        patterns.append("mooncake_master")
    for pattern in patterns:
        res = docker.exec_in_container(container,
                                       f"pgrep -fc {shq(pattern)}", timeout=30)
        try:
            if res.ok and int(res.stdout.strip() or "0") > 0:
                return True
        except ValueError:
            continue
    return False


def health_check(ssh: SSHClient, url: str, timeout: int = 5) -> Tuple[bool, str]:
    """在服务器上执行 curl 健康检查（容器为 host 网络）。"""
    res = ssh.exec(
        f"curl -s -o /dev/null -w '%{{http_code}}' -m {int(timeout)} {shq(url)}",
        timeout=timeout + 15)
    code = res.stdout.strip()
    if code == "200":
        return True, "HTTP 200"
    return False, f"HTTP {code or '-'}" + (f" ({res.stderr.strip()[:80]})" if res.stderr.strip() else "")


def stop_script(ssh: SSHClient, docker: DockerManager, container: str,
                script: NodeScript) -> None:
    """停止脚本对应服务。"""
    if script.stop_command:
        docker.exec_in_container(container, script.stop_command, timeout=120)
        logger.info("已停止 %s @ %s", script.name, container)
