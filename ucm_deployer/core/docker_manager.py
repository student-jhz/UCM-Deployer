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
    def image_ucm_info(self, image_ref: str) -> UCMInfo:
        """检查镜像内是否安装 UCM（pip show uc-manager）。"""
        cmd = (f"docker run --rm --entrypoint /bin/sh {shq(image_ref)} -c "
               f"{shq('pip show uc-manager 2>/dev/null || pip show ucm 2>/dev/null')}")
        res = self.ssh.exec(cmd, timeout=300)
        if not res.ok and not res.stdout.strip():
            return UCMInfo()
        return UCMInfo.from_pip_show(res.stdout + "\n" + res.stderr)

    def container_ucm_info(self, container: str) -> UCMInfo:
        """检查运行中容器内是否安装 UCM。"""
        res = self.ssh.exec(f"docker inspect -f '{{{{.State.Running}}}}' "
                            f"{shq(container)}", timeout=30)
        if not res.ok or res.stdout.strip() != "true":
            raise DockerError(f"容器 {container} 不存在或未运行")
        cmd = (f"docker exec {shq(container)} /bin/bash -c "
               f"{shq('pip show uc-manager 2>/dev/null || pip show ucm 2>/dev/null')}")
        res = self.ssh.exec(cmd, timeout=120)
        if not res.ok and not res.stdout.strip():
            return UCMInfo()
        return UCMInfo.from_pip_show(res.stdout + "\n" + res.stderr)

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
