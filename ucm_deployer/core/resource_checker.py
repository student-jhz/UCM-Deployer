# -*- coding: utf-8 -*-
"""卡资源占用检查（步骤4）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from .docker_manager import DockerManager
from .models import DeviceInfo, DeviceType
from .ssh_client import SSHClient


@dataclass
class ResourceReport:
    server: str
    running_containers: List[str] = field(default_factory=list)
    engine_processes: List[str] = field(default_factory=list)
    gpu_processes: List[str] = field(default_factory=list)
    error: str = ""

    @property
    def occupied(self) -> bool:
        return bool(self.running_containers or self.engine_processes
                    or self.gpu_processes)

    @property
    def message(self) -> str:
        if self.error:
            return f"检查失败: {self.error}"
        if not self.occupied:
            return "空闲：未发现占卡进程/容器"
        parts = []
        if self.running_containers:
            parts.append("运行中容器: " + "; ".join(self.running_containers))
        if self.engine_processes:
            parts.append("推理引擎进程: " + "; ".join(self.engine_processes[:5]))
        if self.gpu_processes:
            parts.append("GPU 计算进程: " + "; ".join(self.gpu_processes[:5]))
        return "可能被占用 -> " + " | ".join(parts)


def check_resources(ssh: SSHClient, device: DeviceInfo,
                    docker: DockerManager = None) -> ResourceReport:
    """检查服务器上的卡资源是否被占用（运行容器 / vllm 等进程 / GPU 计算进程）。"""
    report = ResourceReport(server="")
    docker = docker or DockerManager(ssh)
    try:
        report.running_containers = [
            f"{c.names}({c.image})" for c in docker.list_containers(all_containers=False)
        ]
    except Exception as exc:
        report.error = f"docker 查询失败: {exc}"
        return report
    try:
        res = ssh.exec("ps aux | grep -E 'vllm|sglang|ray|mooncake' | grep -v grep | head -20",
                       timeout=30)
        report.engine_processes = [l.strip() for l in res.stdout.splitlines() if l.strip()]
    except Exception as exc:
        report.error = f"进程查询失败: {exc}"
    if device.device_type == DeviceType.NVIDIA:
        try:
            res = ssh.exec(
                "nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader",
                timeout=30)
            report.gpu_processes = [l.strip() for l in res.stdout.splitlines() if l.strip()]
        except Exception as exc:
            report.error = f"GPU 进程查询失败: {exc}"
    return report
