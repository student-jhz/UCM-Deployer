# -*- coding: utf-8 -*-
"""远端 docker 操作封装：镜像/容器列表、load/build/run、UCM 检查。"""
from __future__ import annotations

import re
from typing import Callable, List, Optional

from .models import CommandResult, DockerImage, UCMInfo
from .ssh_client import SSHClient
from .shell import shq


class DockerError(Exception):
    """docker 操作失败。"""


class DockerManager:
    def __init__(self, ssh: SSHClient):
        self.ssh = ssh

    # ------------------------------------------------------------ 基础
    def check_docker(self) -> bool:
        return self.ssh.exec("docker info >/dev/null 2>&1", timeout=30).ok

    def docker_version(self) -> str:
        res = self.ssh.exec("docker --version", timeout=30)
        return res.stdout.strip() if res.ok else ""

    # ------------------------------------------------------------ 镜像
    def list_images(self) -> List[DockerImage]:
        res = self.ssh.exec(
            "docker images --format '{{.Repository}}\\t{{.Tag}}\\t{{.ID}}\\t{{.Size}}\\t{{.CreatedAt}}'",
            timeout=60)
        if not res.ok:
            raise DockerError(f"获取镜像列表失败: {res.stderr.strip()[:200]}")
        images = []
        for line in res.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                images.append(DockerImage(
                    repository=parts[0], tag=parts[1] if len(parts) > 1 else "",
                    image_id=parts[2] if len(parts) > 2 else "",
                    size=parts[3] if len(parts) > 3 else "",
                    created=parts[4] if len(parts) > 4 else ""))
        return images

    def image_exists(self, ref: str) -> bool:
        res = self.ssh.exec(
            f"docker image inspect {shq(ref)} >/dev/null 2>&1", timeout=30)
        return res.ok

    def load_image(self, remote_tar: str,
                   on_line: Optional[Callable[[str], None]] = None) -> List[str]:
        """docker load 并解析加载出的镜像引用列表。"""
        res = self.ssh.exec_stream(f"docker load -i {shq(remote_tar)}",
                                   on_line=on_line, timeout=3600)
        if not res.ok:
            raise DockerError(f"docker load 失败: {res.stderr.strip()[:300]}")
        refs = []
        for m in re.finditer(r"(?:Loaded image|Loaded image ID):\s*(\S+)",
                             res.stdout):
            refs.append(m.group(1))
        return refs

    def build(self, context_dir: str, tag: str,
              on_line: Optional[Callable[[str], None]] = None) -> CommandResult:
        cmd = f"docker build -t {shq(tag)} {shq(context_dir)}"
        res = self.ssh.exec_stream(cmd, on_line=on_line, timeout=7200)
        if not res.ok:
            raise DockerError(
                f"docker build 失败(退出码 {res.exit_code}): "
                f"{(res.stderr or res.stdout).strip()[:400]}")
        return res

    # ------------------------------------------------------------ UCM 检查
    # ------------------------------------------------------------ UCM 检查
    # UCM 包名存在平台变体（如 uc-manager-cann910-a2），用 pip list 宽泛匹配
    _UCM_LIST_CMD = (
        "pip list --format=freeze 2>/dev/null"
        " | grep -iE '^uc[-_.]?manager' | head -3"
    )

    def image_ucm_info(self, image_ref: str) -> UCMInfo:
        """检查镜像内是否安装 UCM（pip list 匹配 uc-manager* 及平台变体）。"""
        cmd = (f"docker run --rm --entrypoint /bin/sh {shq(image_ref)} -c "
               f"{shq(self._UCM_LIST_CMD)}")
        res = self.ssh.exec(cmd, timeout=300)
        return UCMInfo.from_freeze_output(res.stdout + "\n" + res.stderr)

    def container_ucm_info(self, container: str) -> UCMInfo:
        """检查运行中容器内是否安装 UCM。"""
        res = self.ssh.exec(f"docker inspect -f '{{{{.State.Running}}}}' "
                            f"{shq(container)}", timeout=30)
        if not res.ok or res.stdout.strip() != "true":
            raise DockerError(f"容器 {container} 不存在或未运行")
        cmd = (f"docker exec {shq(container)} /bin/bash -c "
               f"{shq(self._UCM_LIST_CMD)}")
        res = self.ssh.exec(cmd, timeout=120)
        return UCMInfo.from_freeze_output(res.stdout + "\n" + res.stderr)

    # ------------------------------------------------------------ 容器
    def run(self, command: str,
            on_line: Optional[Callable[[str], None]] = None) -> CommandResult:
        """执行（用户可编辑过的）docker run 命令。"""
        return self.ssh.exec_stream(command, on_line=on_line, timeout=600)

    def list_containers(self, all_states: bool = False) -> List[dict]:
        flag = " -a" if all_states else ""
        res = self.ssh.exec(
            "docker ps" + flag +
            " --format '{{.ID}}\\t{{.Names}}\\t{{.Image}}\\t{{.Status}}'",
            timeout=30)
        containers = []
        if res.ok:
            for line in res.stdout.splitlines():
                parts = line.split("\t")
                if len(parts) >= 3:
                    containers.append({
                        "id": parts[0], "names": parts[1],
                        "image": parts[2],
                        "status": parts[3] if len(parts) > 3 else "",
                    })
        return containers

    def remove_containers(self, names: List[str],
                          on_line: Optional[Callable[[str], None]] = None) -> CommandResult:
        """docker rm -f：删除容器（运行中的将被强制停止并删除）。"""
        if not names:
            raise DockerError("未指定要删除的容器")
        refs = " ".join(shq(n) for n in names)
        res = self.ssh.exec_stream(f"docker rm -f {refs}",
                                   on_line=on_line, timeout=600)
        if not res.ok:
            raise DockerError(
                f"docker rm 失败: {(res.stderr or res.stdout).strip()[:300]}")
        return res

    def remove_images(self, refs: List[str],
                      on_line: Optional[Callable[[str], None]] = None) -> CommandResult:
        """docker rmi -f：删除镜像（被运行中容器引用时可能失败，-f 强制）。"""
        if not refs:
            raise DockerError("未指定要删除的镜像")
        joined = " ".join(shq(r) for r in refs)
        res = self.ssh.exec_stream(f"docker rmi -f {joined}",
                                   on_line=on_line, timeout=600)
        if not res.ok:
            raise DockerError(
                f"docker rmi 失败: {(res.stderr or res.stdout).strip()[:300]}")
        return res
