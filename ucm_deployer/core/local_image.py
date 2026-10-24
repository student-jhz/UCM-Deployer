# -*- coding: utf-8 -*-
"""本地拉取 Docker 镜像并导出为 tar.gz，用于上传到目标服务器。"""
from __future__ import annotations

import gzip
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

ProgressFn = Callable[[int, str], None]
LogFn = Callable[[str], None]
CancelledFn = Callable[[], bool]


class LocalImageError(Exception):
    """本地镜像拉取或导出失败。"""


def validate_image_ref(ref: str) -> str:
    ref = (ref or "").strip()
    if not ref:
        raise LocalImageError("请输入镜像地址，例如 quay.io/ascend/vllm-ascend:v0.30.0rc2")
    if ref.startswith(("http://", "https://", "docker://")):
        raise LocalImageError("请输入 Docker 镜像引用，不要包含 http:// 或 https:// 协议头")
    if re.search(r"\s", ref):
        raise LocalImageError("镜像地址不能包含空白字符")
    if re.search(r"[\"'`$;&|<>]", ref):
        raise LocalImageError("镜像地址包含非法字符")
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]*$", ref):
        raise LocalImageError(f"镜像地址格式无效: {ref}")
    return ref


def platform_from_arch(arch: str) -> str:
    arch = (arch or "").strip().lower()
    if arch in ("aarch64", "arm64"):
        return "linux/arm64"
    if arch in ("x86_64", "amd64"):
        return "linux/amd64"
    return ""


def _creation_flags() -> int:
    if os.name == "nt":
        return subprocess.CREATE_NO_WINDOW
    return 0


def _docker_pull(ref: str, platform: str, log: LogFn,
                 cancelled: Optional[CancelledFn]) -> None:
    if shutil.which("docker") is None:
        raise LocalImageError("本地未找到 docker 命令；请先安装并启动 Docker Desktop")
    cmd = ["docker", "pull"]
    if platform:
        cmd += ["--platform", platform]
    cmd.append(ref)
    log("$ " + " ".join(cmd))
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        bufsize=1, creationflags=_creation_flags())
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            log(line.rstrip())
            if cancelled is not None and cancelled():
                proc.terminate()
                raise LocalImageError("用户取消本地镜像拉取")
        if proc.wait() != 0:
            raise LocalImageError(f"docker pull 失败（退出码 {proc.returncode}）")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def _docker_save(ref: str, output_path: Path, log: LogFn) -> None:
    cmd = ["docker", "save", "-o", str(output_path), ref]
    log("$ " + " ".join(cmd))
    proc = subprocess.run(
        cmd, capture_output=True, text=True, creationflags=_creation_flags())
    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout or "").strip()
        raise LocalImageError(f"docker save 失败: {message[:400]}")


def _gzip_file(source: Path, target: Path, progress: ProgressFn) -> None:
    total = source.stat().st_size
    copied = 0
    with source.open("rb") as src, gzip.open(target, "wb", compresslevel=6) as dst:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            dst.write(chunk)
            copied += len(chunk)
            progress(int(copied * 100 / max(total, 1)), f"压缩镜像包 {copied / 1e9:.2f}/{total / 1e9:.2f} GB")


def pull_image_to_tar(ref: str, platform: str = "",
                      output_dir: Optional[str] = None,
                      progress: Optional[ProgressFn] = None,
                      log: Optional[LogFn] = None,
                      cancelled: Optional[CancelledFn] = None) -> str:
    """本地拉取镜像并导出 tar.gz，返回本地包路径。"""
    ref = validate_image_ref(ref)
    progress = progress or (lambda pct, msg: None)
    log = log or (lambda line: None)
    output_root = Path(output_dir or tempfile.gettempdir()) / "ucm-deployer-images"
    output_root.mkdir(parents=True, exist_ok=True)
    raw_tar = output_root / "image.tar"
    tar_gz = output_root / "image.tar.gz"

    try:
        raw_tar.unlink(missing_ok=True)
        tar_gz.unlink(missing_ok=True)
        progress(5, f"本地拉取镜像 {ref}" + (f"（{platform}）" if platform else ""))
        _docker_pull(ref, platform, log, cancelled)
        progress(45, "导出镜像为 tar")
        _docker_save(ref, raw_tar, log)
        progress(55, "压缩为 tar.gz")
        _gzip_file(raw_tar, tar_gz, lambda pct, msg: progress(55 + int(pct * 0.45), msg))
        progress(100, f"本地镜像包已生成: {tar_gz}")
        return str(tar_gz)
    finally:
        raw_tar.unlink(missing_ok=True)
