# -*- coding: utf-8 -*-
"""UCM 镜像构建。

流程：上传 UCM whl（离线时含 wrapt whl / 可选 ucm-toolkit 源码包）
      -> 生成 Dockerfile -> docker build -> docker run 校验 UCM 安装。
"""
from __future__ import annotations

import os
import re
import tarfile
import tempfile
import uuid
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from .docker_manager import DockerError, DockerManager
from .models import UCMInfo
from .shell import shq
from .ssh_client import SSHClient, SSHError
from ..utils.log import get_logger

logger = get_logger(__name__)

ProgressFn = Callable[[int, str], None]
LogFn = Callable[[str], None]
CancelledFn = Callable[[], bool]


class ImageBuildError(Exception):
    """镜像构建配置或执行错误。"""


class BuildCancelled(Exception):
    """用户取消。"""


@dataclass
class ImageBuildConfig:
    """一次镜像构建的完整输入。"""

    base_image: str                 # 服务器上的基础镜像 (vllm-ascend/vllm/sglang)
    image_tag: str                  # 新镜像名（用户可改，有默认建议值）
    ucm_whl_local: str = ""         # 本地 UCM whl
    wrapt_whl_local: str = ""       # 本地 wrapt whl（离线模式需要）
    offline: bool = False           # 服务器是否无法联网
    toolkit_dir: str = ""           # 可选：ucm-toolkit 源码目录（源码安装）
    platform: str = "ascend"        # ascend | nvidia（决定 ENV PLATFORM）

    def validate(self) -> None:
        if not self.base_image or not self.base_image.strip():
            raise ImageBuildError("未选择基础镜像")
        if not re.match(r"^[\w.\-/:]+$", self.image_tag or ""):
            raise ImageBuildError(f"非法的镜像名: {self.image_tag!r}")
        if not self.ucm_whl_local or not os.path.isfile(self.ucm_whl_local):
            raise ImageBuildError(f"UCM whl 文件不存在: {self.ucm_whl_local}")
        if self.offline and self.wrapt_whl_local and not os.path.isfile(self.wrapt_whl_local):
            raise ImageBuildError(f"wrapt whl 文件不存在: {self.wrapt_whl_local}")
        for path in (self.ucm_whl_local, self.wrapt_whl_local):
            if path and re.search(r"\s", os.path.basename(path)):
                raise ImageBuildError(f"文件名不能包含空白字符: {path}")
        if self.toolkit_dir and not os.path.isdir(self.toolkit_dir):
            raise ImageBuildError(f"ucm-toolkit 源码目录不存在: {self.toolkit_dir}")


@dataclass
class BuildResult:
    ok: bool
    image: str = ""
    ucm: Optional[UCMInfo] = None
    error: str = ""


def suggest_tag(base_ref: str) -> str:
    """根据基础镜像生成默认新镜像名，如 vllm-ascend:v0.23.0-a3 -> vllm-ascend-ucm:v0.23.0-a3"""
    base_ref = (base_ref or "").strip()
    if not base_ref:
        return "ucm-engine:latest"
    repo, _, tag = base_ref.rpartition(":")
    if not repo or "/" in tag:
        repo, tag = base_ref, "latest"
    short = repo.split("/")[-1] or "engine"
    return f"{short}-ucm:{tag}"


def generate_dockerfile(config: ImageBuildConfig, ucm_name: str,
                        wrapt_name: str = "", toolkit_name: str = "") -> str:
    """生成构建带 UCM 引擎镜像的 Dockerfile（纯函数，便于测试）。"""
    offline_flags = "--no-index --find-links=/tmp/ucm-pkgs" if config.offline else ""
    lines = [
        "# 由 UCM Deployer 自动生成",
        f"FROM {config.base_image}",
        f"ENV PLATFORM={config.platform}",
        "WORKDIR /tmp/ucm-pkgs",
        f"COPY {ucm_name} /tmp/ucm-pkgs/",
    ]
    if config.offline and wrapt_name:
        lines.append(f"COPY {wrapt_name} /tmp/ucm-pkgs/")
    if toolkit_name:
        lines.append(f"COPY {toolkit_name} /tmp/ucm-pkgs/")
    lines.append("")
    if config.offline and wrapt_name:
        lines.append("# 离线安装 UCM 依赖 wrapt（本地 whl）")
        lines.append(f"RUN pip install --no-cache-dir {offline_flags} /tmp/ucm-pkgs/{wrapt_name}")
    lines.append("# 安装 UCM")
    run_ucm = " ".join(p for p in
                       ["RUN pip install --no-cache-dir", offline_flags,
                        f"/tmp/ucm-pkgs/{ucm_name}"] if p)
    lines.append(run_ucm)
    if toolkit_name:
        lines.append("")
        lines.append("# 源码安装 ucm-toolkit")
        lines.append(
            "RUN mkdir -p /tmp/ucm-toolkit-src \\"
        )
        lines.append(
            f"    && tar xzf /tmp/ucm-pkgs/{toolkit_name} -C /tmp/ucm-toolkit-src --strip-components=1 \\"
        )
        lines.append(
            "    && pip install --no-cache-dir /tmp/ucm-toolkit-src"
        )
    return "\n".join(lines) + "\n"


def _pack_dir_tarball(dir_path: str) -> str:
    """把本地目录打包为 tar.gz（保留顶层目录名）。"""
    dir_path = os.path.abspath(dir_path)
    parent = os.path.dirname(dir_path)
    name = os.path.basename(dir_path.rstrip("\\/"))
    fd, tar_path = tempfile.mkstemp(prefix="ucm-toolkit-", suffix=".tar.gz")
    os.close(fd)
    try:
        with tarfile.open(tar_path, "w:gz") as tf:
            tf.add(dir_path, arcname=name)
    except Exception:
        try:
            os.unlink(tar_path)
        except OSError:
            pass
        raise
    return tar_path


class ImageBuilder:
    def __init__(self, ssh: SSHClient, docker: Optional[DockerManager] = None,
                 workdir: str = "/tmp"):
        self.ssh = ssh
        self.docker = docker or DockerManager(ssh)
        self.workdir = workdir

    def build(self, config: ImageBuildConfig,
              progress: Optional[ProgressFn] = None,
              log: Optional[LogFn] = None,
              cancelled: Optional[CancelledFn] = None) -> BuildResult:
        progress = progress or (lambda pct, msg: None)
        log = log or (lambda line: None)
        config.validate()

        def _check_cancel():
            if cancelled is not None and cancelled():
                raise BuildCancelled("用户取消")

        def _upload(local: str, remote_dir: str, lo: int, hi: int, label: str) -> str:
            name = os.path.basename(local)
            remote = f"{remote_dir}/{name}"
            size = os.path.getsize(local)
            log(f"上传 {label}: {name} ({size / 1e6:.1f} MB)")

            def cb(sent: int, total: int) -> None:
                pct = int(lo + (hi - lo) * sent / max(total, 1))
                progress(pct, f"上传{label} {sent / 1e6:.1f}/{total / 1e6:.1f} MB")

            self.ssh.upload_file(local, remote, progress_cb=cb)
            return name

        uid = uuid.uuid4().hex[:8]
        remote_dir = f"{self.workdir}/ucm-build-{uid}"
        progress(2, f"创建构建目录 {remote_dir}")
        self.ssh.mkdirs(remote_dir)
        _check_cancel()

        try:
            wrapt_name = ""
            toolkit_name = ""
            toolkit_tar = None
            try:
                ucm_name = _upload(config.ucm_whl_local, remote_dir, 5, 30, "UCM whl")
                _check_cancel()
                if config.offline and config.wrapt_whl_local:
                    wrapt_name = _upload(config.wrapt_whl_local, remote_dir, 30, 40, "wrapt whl")
                    _check_cancel()
                elif config.offline and not config.wrapt_whl_local:
                    log("[提示] 离线模式但未提供 wrapt whl，若基础镜像内已有 wrapt 仍可继续")
                if config.toolkit_dir:
                    toolkit_tar = _pack_dir_tarball(config.toolkit_dir)
                    toolkit_name = _upload(toolkit_tar, remote_dir, 40, 55, "ucm-toolkit 源码包")
                    _check_cancel()

                progress(57, "生成 Dockerfile")
                dockerfile = generate_dockerfile(config, ucm_name, wrapt_name, toolkit_name)
                log("---- Dockerfile ----")
                for line in dockerfile.splitlines():
                    log(line)
                log("-------------------")
                self.ssh.write_file(f"{remote_dir}/Dockerfile", dockerfile)

                progress(60, "docker build ...")
                log(f"$ docker build -t {config.image_tag} {remote_dir}")
                self.docker.build(remote_dir, config.image_tag,
                                  on_line=lambda l: log(l))
                _check_cancel()

                progress(92, "校验镜像内 UCM 安装")
                ucm = self.docker.image_ucm_info(config.image_tag)
                log(f"UCM 检查: {ucm}")
                if not ucm.installed:
                    raise ImageBuildError(
                        f"镜像 {config.image_tag} 构建成功但未检测到 UCM 安装，请检查 whl 文件")
                progress(100, f"构建完成: {config.image_tag}")
                return BuildResult(ok=True, image=config.image_tag, ucm=ucm)
            finally:
                if toolkit_tar:
                    try:
                        os.unlink(toolkit_tar)
                    except OSError:
                        pass
        except (SSHError, DockerError) as exc:
            raise ImageBuildError(str(exc)) from exc
        finally:
            # 清理远端构建目录（尽力而为）
            try:
                self.ssh.exec(f"rm -rf {shq(remote_dir)}", timeout=60)
            except Exception:
                logger.debug("清理构建目录失败 %s", remote_dir, exc_info=True)


def upload_image_tar(ssh: SSHClient, docker: DockerManager, local_tar: str,
                     progress: Optional[ProgressFn] = None,
                     log: Optional[LogFn] = None) -> List[str]:
    """上传本地镜像 tar/tar.gz 到服务器并 docker load，返回加载出的镜像引用列表。"""
    progress = progress or (lambda pct, msg: None)
    log = log or (lambda line: None)
    if not os.path.isfile(local_tar):
        raise ImageBuildError(f"镜像包不存在: {local_tar}")
    name = os.path.basename(local_tar)
    remote = f"/tmp/{name}"
    size = os.path.getsize(local_tar)
    log(f"上传镜像包: {name} ({size / 1e6:.1f} MB)")

    def cb(sent: int, total: int) -> None:
        progress(int(70 * sent / max(total, 1)), f"上传镜像包 {sent / 1e6:.1f}/{total / 1e6:.1f} MB")

    ssh.upload_file(local_tar, remote, progress_cb=cb)
    progress(75, "docker load ...")
    log(f"$ docker load -i {remote}")
    refs = docker.load_image(remote, on_line=lambda l: log(l))
    progress(100, "docker load 完成")
    return refs
