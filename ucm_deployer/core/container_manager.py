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

# Ascend 容器候选系统挂载（按宿主机实际存在情况筛选；父目录存在时跳过子路径）
ASCEND_SYSTEM_MOUNT_CANDIDATES: List[str] = [
    "/usr/local/Ascend/driver",
    "/usr/local/Ascend/driver/lib64",
    "/usr/local/Ascend/driver/tools/hccn_tool",
    "/usr/local/Ascend/driver/version.info",
    "/usr/local/dcmi",
    "/usr/local/sbin",
    "/usr/local/bin/npu-smi",
    "/usr/lib64",
    "/etc/ascend_install.info",
    "/etc/hccl_rootinfo.json",
    "/etc/hixlep",
    "/root/.cache",
]

ASCEND_SYSTEM_MOUNTS: List[Tuple[str, str]] = [
    (path, path) for path in ASCEND_SYSTEM_MOUNT_CANDIDATES
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
    device_nodes: List[str] = field(default_factory=list)
    system_mounts: List[Tuple[str, str]] = field(default_factory=list)
    privileged: bool = False

    def validate(self) -> None:
        if not self.image:
            raise DockerError("未选择镜像")
        if not self.name or not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", self.name):
            raise DockerError(f"非法的容器名: {self.name!r}")
        for d in self.kv_cache_dirs:
            if not d.startswith("/"):
                raise DockerError(f"kvcache 挂载目录必须是绝对路径: {d}")
        for node in self.device_nodes:
            if not node.startswith("/dev/") or re.search(r"\s", node):
                raise DockerError(f"非法的 Ascend 设备节点: {node!r}")
        for host, container in self.system_mounts:
            if not host.startswith("/") or not container.startswith("/"):
                raise DockerError(f"非法的 Ascend 系统挂载: {host!r} -> {container!r}")


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
            if cfg.privileged:
                lines.append("    --privileged=true")
            if cfg.shm_size:
                lines.append(f"    --shm-size {cfg.shm_size}")
            device_nodes = cfg.device_nodes or (
                [f"/dev/davinci{i}" for i in range(cfg.device_count)]
                + ["/dev/davinci_manager", "/dev/devmm_svm", "/dev/hisi_hdc"])
            for node in device_nodes:
                lines.append(f"    --device {shq(node)}")
            system_mounts = cfg.system_mounts or ASCEND_SYSTEM_MOUNTS
            for host, cont in system_mounts:
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

    def list_ascend_devices(self) -> List[str]:
        """列出宿主机实际存在的 Ascend 设备节点（不同型号驱动节点不同）。"""
        cmd = (
            "for d in /dev/davinci* /dev/devmm_svm /dev/hisi_hdc "
            "/dev/ummu /dev/uburma; do "
            "if [ -e \"$d\" ]; then printf '%s\\n' \"$d\"; fi; done | sort -u -V"
        )
        res = self.ssh.exec(cmd, timeout=30)
        if not res.ok:
            raise DockerError(
                f"探测 Ascend 设备节点失败: {res.stderr.strip()[:300]}")
        nodes = [line.strip() for line in res.stdout.splitlines() if line.strip()]
        if not nodes:
            raise DockerError("未找到 /dev/davinci* 等 Ascend 设备节点")
        return nodes

    def list_ascend_mounts(self) -> List[Tuple[str, str]]:
        """列出宿主机实际存在的 Ascend 驱动/工具/配置挂载路径。"""
        candidates = " ".join(shq(path) for path in ASCEND_SYSTEM_MOUNT_CANDIDATES)
        cmd = (
            f"for p in {candidates}; do "
            "if [ -e \"$p\" ]; then printf '%s\\n' \"$p\"; fi; done"
        )
        res = self.ssh.exec(cmd, timeout=30)
        if not res.ok:
            raise DockerError(
                f"探测 Ascend 系统挂载失败: {res.stderr.strip()[:300]}")
        selected: List[Tuple[str, str]] = []
        for host in (line.strip().rstrip("/") for line in res.stdout.splitlines()
                     if line.strip()):
            nested = any(
                host == parent or host.startswith(parent + "/")
                for parent, _ in selected
            )
            if not nested:
                selected.append((host, host))
        return selected

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
