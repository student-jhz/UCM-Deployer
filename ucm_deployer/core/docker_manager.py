# -*- coding: utf-8 -*-
"""远端 docker 操作封装（镜像 / 容器 / UCM 检查）。"""
from __future__ import annotations

import re
from typing import Callable, List, Optional

from .models import CommandResult, DockerContainer, DockerImage, UCMInfo
from .shell import shq
from .ssh_client import SSHClient

_IMAGE_FMT = r"{{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.Size}}\t{{.CreatedAt}}"
_CONTAINER_FMT = r"{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.State}}"

# 在镜像/容器内检查 UCM 是否安装（兼容包名 uc-manager / ucm 与不同 python 入口）
_PIP_SHOW = ("pip show uc-manager 2>/dev/null || pip show ucm 2>/dev/null "
             "|| python3 -m pip show uc-manager 2>/dev/null "
             "|| python -m pip show ucm 2>/dev/null")


class DockerError(Exception):
    """docker 操作失败。"""


class DockerManager:
    def __init__(self, ssh: SSHClient):
        self.ssh = ssh

    # ------------------------------------------------------------ 基础
    def check_docker(self) -> bool:
        return self.ssh.exec("docker info >/dev/null 2>&1", timeout=60).ok

    def docker_version(self) -> str:
        res = self.ssh.exec("docker --version", timeout=30)
        return res.stdout.strip() if res.ok else ""

    # ------------------------------------------------------------ 镜像
    def list_images(self) -> List[DockerImage]:
        res = self.ssh.exec(f"docker images --format '{_IMAGE_FMT}'", timeout=60)
        if not res.ok:
            raise DockerError("获取镜像列表失败: " + (res.stderr.strip() or res.stdout.strip())[:300])
        return self._parse_images(res.stdout)

    @staticmethod
    def _parse_images(stdout: str) -> List[DockerImage]:
        images: List[DockerImage] = []
        for line in stdout.strip().splitlines():
            parts = [p.strip() for p in line.split("\t")]
            if len(parts) < 3 or not parts[0]:
                continue
            images.append(DockerImage(
                repository=parts[0],
                tag=parts[1] if len(parts) > 1 else "",
                image_id=parts[2] if len(parts) > 2 else "",
                size=parts[3] if len(parts) > 3 else "",
                created=parts[4] if len(parts) > 4 else "",
            ))
        return images

    def image_exists(self, ref: str) -> bool:
        ref = ref.strip()
        for im in self.list_images():
            if im.ref == ref or im.image_id == ref:
                return True
        return False

    def load_image(self, remote_tar: str, on_line: Optional[Callable[[str], None]] = None,
                   timeout: float = 3600) -> List[str]:
        """docker load 并返回加载出的镜像引用列表。"""
        cmd = f"docker load -i {shq(remote_tar)}"
        res = self.ssh.exec_stream(cmd, on_line=on_line, timeout=timeout)
        if not res.ok:
            raise DockerError(f"docker load 失败(退出码 {res.exit_code})，详见日志")
        return re.findall(r"Loaded image(?: ID)?:\s*(\S+)", res.stdout)

    def pull_image(self, ref: str, on_line: Optional[Callable[[str], None]] = None,
                   timeout: float = 3600) -> CommandResult:
        res = self.ssh.exec_stream(f"docker pull {shq(ref)}", on_line=on_line, timeout=timeout)
        if not res.ok:
            raise DockerError(f"docker pull {ref} 失败(退出码 {res.exit_code})，详见日志")
        return res

    def build(self, context_dir: str, tag: str,
              on_line: Optional[Callable[[str], None]] = None,
              timeout: float = 7200) -> CommandResult:
        cmd = f"docker build -t {shq(tag)} {shq(context_dir)}"
        res = self.ssh.exec_stream(cmd, on_line=on_line, timeout=timeout)
        if not res.ok:
            raise DockerError(f"docker build 失败(退出码 {res.exit_code})，详见日志")
        return res

    # ------------------------------------------------------------ UCM 检查
    def image_ucm_info(self, image_ref: str) -> UCMInfo:
        """在镜像里跑 pip show 检查 UCM（--rm 一次性容器）。"""
        cmd = (f"docker run --rm --entrypoint /bin/sh {shq(image_ref)} "
               f"-c {shq(_PIP_SHOW)}")
        res = self.ssh.exec(cmd, timeout=600)
        if res.exit_code == 125:
            raise DockerError(f"无法运行镜像 {image_ref}: " + res.stderr.strip()[:300])
        return UCMInfo.from_pip_show(res.stdout)

    def container_ucm_info(self, name: str) -> UCMInfo:
        """在运行中的容器里检查 UCM。"""
        container = self.get_container(name)
        if container is not None and container.state != "running":
            raise DockerError(
                f"容器 {name} 未运行(state={container.state})，请先启动容器或改用镜像检查")
        cmd = f"docker exec {shq(name)} /bin/sh -c {shq(_PIP_SHOW)}"
        res = self.ssh.exec(cmd, timeout=120)
        if not res.ok and "is not running" in (res.stderr + res.stdout):
            raise DockerError(f"容器 {name} 未运行，无法在容器内检查 UCM")
        return UCMInfo.from_pip_show(res.stdout)

    # ------------------------------------------------------------ 容器
    def list_containers(self, all_containers: bool = True) -> List[DockerContainer]:
        flag = "-a" if all_containers else ""
        res = self.ssh.exec(f"docker ps {flag} --format '{_CONTAINER_FMT}'", timeout=60)
        if not res.ok:
            raise DockerError("获取容器列表失败: " + (res.stderr.strip() or res.stdout.strip())[:300])
        return self._parse_containers(res.stdout)

    @staticmethod
    def _parse_containers(stdout: str) -> List[DockerContainer]:
        containers: List[DockerContainer] = []
        for line in stdout.strip().splitlines():
            parts = [p.strip() for p in line.split("\t")]
            if len(parts) < 3 or not parts[0]:
                continue
            containers.append(DockerContainer(
                container_id=parts[0],
                names=parts[1] if len(parts) > 1 else "",
                image=parts[2] if len(parts) > 2 else "",
                status=parts[3] if len(parts) > 3 else "",
                state=parts[4] if len(parts) > 4 else "",
            ))
        return containers

    def get_container(self, name_or_id: str) -> Optional[DockerContainer]:
        for c in self.list_containers():
            if c.names == name_or_id or c.container_id.startswith(name_or_id):
                return c
        return None

    def run(self, full_cmd: str, on_line: Optional[Callable[[str], None]] = None,
            timeout: float = 600) -> CommandResult:
        """执行用户编辑后的完整 docker run 命令。"""
        if not full_cmd.strip().startswith("docker run"):
            raise DockerError("命令必须以 'docker run' 开头")
        res = self.ssh.exec_stream(full_cmd, on_line=on_line, timeout=timeout)
        if not res.ok:
            raise DockerError(f"docker run 失败(退出码 {res.exit_code})，详见日志")
        return res

    def exec_in_container(self, container: str, cmd: str, detach: bool = False,
                          on_line: Optional[Callable[[str], None]] = None,
                          timeout: Optional[float] = 300) -> CommandResult:
        d = "-d" if detach else ""
        full = f"docker exec {d} {shq(container)} /bin/bash -c {shq(cmd)}"
        if detach:
            return self.ssh.exec(full, timeout=timeout or 60)
        return self.ssh.exec_stream(full, on_line=on_line, timeout=timeout)

    def copy_to_container(self, src: str, container: str, dst: str,
                          timeout: float = 600) -> CommandResult:
        res = self.ssh.exec(f"docker cp {shq(src)} {shq(container + ':' + dst)}",
                            timeout=timeout)
        if not res.ok:
            raise DockerError("docker cp 失败: " + (res.stderr.strip() or res.stdout.strip())[:300])
        return res

    def stop_container(self, name: str, timeout_s: int = 30) -> CommandResult:
        return self.ssh.exec(f"docker stop -t {int(timeout_s)} {shq(name)}", timeout=timeout_s + 60)

    def remove_container(self, name: str, force: bool = True) -> CommandResult:
        f = "-f" if force else ""
        return self.ssh.exec(f"docker rm {f} {shq(name)}", timeout=120)
