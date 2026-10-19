# -*- coding: utf-8 -*-
"""容器创建：Ascend/NVIDIA 的 docker run 命令生成与执行、
kvcache 挂载目录文件系统校验、宿主机目录浏览。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .docker_manager import DockerError, DockerManager
from .models import CommandResult, DeviceType, VolumeMount
from .shell import flatten_command, shq
from .ssh_client import SSHClient

# Ascend 容器必需的系统挂载（驱动/工具）
ASCEND_SYSTEM_MOUNTS: List[Tuple[str, str]] = [
    ("/usr/local/dcmi", "/usr/local/dcmi"),
    ("/usr/local/Ascend/driver/tools/hccn_tool", "/usr/local/Ascend/driver/tools/hccn_tool"),
    ("/usr/local/bin/npu-smi", "/usr/local/bin/npu-smi"),
    ("/usr/local/Ascend/driver/lib64/", "/usr/local/Ascend/driver/lib64/"),
    ("/usr/local/Ascend/driver/version.info", "/usr/local/Ascend/driver/version.info"),
    ("/etc/ascend_install.info", "/etc/ascend_install.info"),
    ("/root/.cache", "/root/.cache"),
]

# 视为可跨服务器共享的网络文件系统
NETWORK_FS_TYPES = {
    "nfs", "nfs4", "3fs", "beegfs", "lustre", "ceph", "cephfs",
    "glusterfs", "gpfs", "cifs", "smbfs", "juicefs", "gds",
}


@dataclass
class ContainerCreateConfig:
    image: str
    name: str
    device_type: DeviceType = DeviceType.UNKNOWN
    device_count: int = 0
    shm_size: str = "512g"
    network: str = "host"                 # Ascend 使用（--net=host）
    ports: List[str] = field(default_factory=lambda: ["8000:8000"])  # NVIDIA 使用（-p）
    kv_cache_dirs: List[str] = field(default_factory=list)
    model_mount: Optional[VolumeMount] = None
    extra_mounts: List[VolumeMount] = field(default_factory=list)
    extra_args: str = ""

    def validate(self) -> None:
        if not self.image:
            raise DockerError("未选择镜像")
        if not self.name or not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", self.name):
            raise DockerError(f"非法的容器名: {self.name!r}")
        for d in self.kv_cache_dirs:
            if not d.startswith("/"):
                raise DockerError(f"kvcache 挂载目录必须是绝对路径: {d}")


@dataclass
class FsEntry:
    path: str
    source: str = ""
    fstype: str = ""
    error: str = ""


@dataclass
class SharedFsReport:
    entries: List[FsEntry] = field(default_factory=list)
    ok: bool = False
    message: str = ""
    warnings: List[str] = field(default_factory=list)


def _is_network_fs(entry: FsEntry) -> bool:
    return entry.fstype.lower() in NETWORK_FS_TYPES or ":" in entry.source


def evaluate_shared_fs(entries: List[FsEntry]) -> SharedFsReport:
    """评估 kvcache 挂载目录的文件系统（单服务器）。"""
    report = SharedFsReport(entries=entries)
    if not entries:
        report.ok = False
        report.message = "没有待校验的目录"
        return report

    errors = [e for e in entries if e.error]
    if errors:
        report.ok = False
        report.message = "；".join(f"{e.path} {e.error}" for e in errors)
        return report

    sources = {e.source for e in entries}
    if len(sources) > 1:
        report.ok = False
        report.message = ("kvcache 目录不在同一文件系统: "
                          + ", ".join(f"{e.path}({e.source})" for e in entries))
        return report

    sample = entries[0]
    if _is_network_fs(sample):
        report.ok = True
        report.message = f"校验通过：目录位于网络文件系统({sample.source}, {sample.fstype})"
    else:
        report.ok = True
        report.message = f"目录位于本地文件系统({sample.source}, {sample.fstype})"
        report.warnings.append(
            "当前为本地磁盘；如后续需共用 kvcache，建议改用共享文件系统(NFS/3FS等)")
    return report


def check_shared_fs(ssh: SSHClient, dirs: List[str]) -> SharedFsReport:
    """df 校验 kvcache 挂载目录所在文件系统。"""
    entries: List[FsEntry] = []
    for d in dirs:
        res = ssh.exec(f"df --output=source,fstype {shq(d)} 2>/dev/null", timeout=30)
        lines = [l for l in res.stdout.strip().splitlines() if l.strip()]
        if not res.ok or len(lines) < 2:
            entries.append(FsEntry(d, error="目录不存在或无法读取"))
            continue
        parts = lines[-1].split()
        entries.append(FsEntry(d,
                               source=parts[0] if parts else "",
                               fstype=parts[1] if len(parts) > 1 else ""))
    return evaluate_shared_fs(entries)


class ContainerManager:
    def __init__(self, ssh: SSHClient, docker: Optional[DockerManager] = None):
        self.ssh = ssh
        self.docker = docker or DockerManager(ssh)

    # ------------------------------------------------------------ 命令生成
    @staticmethod
    def generate_run_command(cfg: ContainerCreateConfig) -> str:
        cfg.validate()
        lines = ["docker run -itd"]
        lines.append(f"    --name {shq(cfg.name)}")

        if cfg.device_type == DeviceType.ASCEND:
            # Ascend: --net=host + 全部 davinci 设备 + 驱动/工具挂载
            if cfg.network:
                lines.append(f"    --net={cfg.network}")
            if cfg.shm_size:
                lines.append(f"    --shm-size {cfg.shm_size}")
            for i in range(cfg.device_count):
                lines.append(f"    --device /dev/davinci{i}")
            lines.append("    --device /dev/davinci_manager")
            lines.append("    --device /dev/devmm_svm")
            lines.append("    --device /dev/hisi_hdc")
            for host, cont in ASCEND_SYSTEM_MOUNTS:
                lines.append(f"    -v {shq(host)}:{shq(cont)}")
        elif cfg.device_type == DeviceType.NVIDIA:
            # NVIDIA: --runtime nvidia --gpus all + 端口映射 + --ipc=host
            lines.append("    --runtime nvidia")
            lines.append("    --gpus all")
            for p in cfg.ports:
                if p.strip():
                    lines.append(f"    -p {p.strip()}")
            lines.append("    --ipc=host")
            if cfg.shm_size:
                lines.append(f"    --shm-size {cfg.shm_size}")
        else:
            # 设备未知：保守输出通用参数
            if cfg.network:
                lines.append(f"    --net={cfg.network}")
            if cfg.shm_size:
                lines.append(f"    --shm-size {cfg.shm_size}")

        for d in cfg.kv_cache_dirs:
            d = d.rstrip("/") or "/"
            lines.append(f"    -v {shq(d)}:{shq(d)}")
        if cfg.model_mount is not None:
            lines.append("    " + cfg.model_mount.to_docker_arg())
        for m in cfg.extra_mounts:
            lines.append("    " + m.to_docker_arg())
        for extra in cfg.extra_args.splitlines():
            if extra.strip():
                lines.append("    " + extra.strip())
        lines.append(f"    {shq(cfg.image)}")
        lines.append("    bash")
        return " \\\n".join(lines)

    # ------------------------------------------------------------ 执行
    def create(self, command: str,
               on_line=None) -> CommandResult:
        """执行（可能被用户编辑过的）docker run 命令。"""
        flat = flatten_command(command)
        return self.docker.run(flat, on_line=on_line)

    # ------------------------------------------------------------ 浏览
    def list_host_dirs(self, path: str = "/") -> List[str]:
        """列出宿主机某目录下的子目录（GUI 目录浏览数据源）。"""
        path = path.rstrip("/") or "/"
        res = self.ssh.exec(
            f"find {shq(path)} -maxdepth 1 -mindepth 1 -type d 2>/dev/null | sort | head -200",
            timeout=30)
        if not res.ok:
            return []
        return [l.strip() for l in res.stdout.splitlines() if l.strip()]
