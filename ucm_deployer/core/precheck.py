# -*- coding: utf-8 -*-
"""环境预检：在已创建的容器内运行 ucm-toolkit 的 precheck 命令。

- 纯环境检查：docker exec <容器> ucm-toolkit run precheck --skip-bandwidth
- 带宽测试：指定挂载目录时，在其下创建临时子目录 ucm-precheck-<uid>
  作为 --mount-path（读写负载不污染用户目录），结束后清理子目录。
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .docker_manager import DockerManager
from .models import CommandResult
from .shell import shq
from .ssh_client import SSHClient
from ..utils.log import get_logger

logger = get_logger(__name__)

# 检查项行：[PASS] kernel               5.10.0
_ITEM_RE = re.compile(r"^\[(PASS|WARN|FAIL|INFO|SKIP)\]\s+(\S+)\s*(.*)$")
# 汇总行：Result: PASSED (with warnings)  |  1 pass, 1 warn, 0 fail
_SUMMARY_RE = re.compile(r"^Result:\s*(.+?)\s*\|(.*)$")

# 检查项中文名与排查建议
_CHECK_ZH = {
    "serving_stack": (
        "服务栈版本",
        "信息项：记录 vLLM / vLLM-Ascend / SGLang 实际安装版本，仅展示。"),
    "uc_manager": (
        "UCM 安装版本",
        "信息项：记录 uc-manager（含平台变体包）实际安装版本，仅展示。"),
    "accelerator_driver": (
        "加速器驱动",
        "在宿主机运行 npu-smi info / nvidia-smi 确认驱动正常；容器内报 "
        "'device is used (-8020)' 或 rc=172 通常为 NPU 正被其他进程/容器占用，"
        "停用占用方后重试；确认容器已映射 npu-smi 与驱动目录。"),
    "kernel": (
        "内核版本",
        "升级操作系统内核至 5.10 及以上系列（按 major.minor 比较）。"),
    "memory_shm": (
        "共享内存 /dev/shm",
        "扩大共享内存至 512 GiB 及以上：容器需 --shm-size 512g"
        "（本工具生成的命令已默认携带）；宿主机需以 size=512G 挂载 /dev/shm。"),
    "aio_resources": (
        "AIO 资源",
        "信息项：记录内核 AIO 上限与占用；带宽测试 aio 引擎超时时，"
        "可提高 fs.aio-max-nr（sysctl fs.aio-max-nr=<更大值>）或改用 psync 引擎。"),
    "bandwidth": (
        "存储带宽",
        "检查挂载点所在磁盘/NVMe 的实际吞吐；尝试 aio 引擎或提高 --workers；"
        "确认 io_direct/O_DIRECT 设置；网络存储（NFS 等）需同时检查网络带宽。"),
}


@dataclass
class PrecheckItem:
    status: str            # PASS / WARN / FAIL / INFO / SKIP
    name: str              # 检查项名（如 kernel / accelerator_driver）
    summary: str = ""      # 行内摘要（如 5.10.0）
    detail: str = ""       # 缩进补充行（阈值/fix 建议等）
    name_zh: str = ""      # 中文名
    advice: str = ""       # 中文排查建议


@dataclass
class PrecheckResult:
    exit_code: int = -1
    ok: bool = False               # exit_code == 0（预检通过）
    items: List[PrecheckItem] = field(default_factory=list)
    summary: str = ""              # Result: FAILED  |  1 pass, ...
    raw_output: str = ""

    @property
    def passed_count(self) -> int:
        return sum(1 for i in self.items if i.status == "PASS")

    @property
    def failed_count(self) -> int:
        return sum(1 for i in self.items if i.status == "FAIL")

    @property
    def warn_count(self) -> int:
        return sum(1 for i in self.items if i.status == "WARN")

    @property
    def verdict(self) -> str:
        """三态判定：passed（全部通过）/ completed（完成但有警告）/ failed（未通过）。"""
        if not self.ok:
            return "failed"
        return "completed" if self.warn_count else "passed"


def parse_precheck_output(res: CommandResult) -> PrecheckResult:
    """解析 precheck 输出（--no-color 无 ANSI 码）为结构化结果。"""
    result = PrecheckResult(exit_code=res.exit_code, ok=res.exit_code == 0,
                            raw_output=res.stdout)
    current: Optional[PrecheckItem] = None
    for line in (res.stdout or "").splitlines():
        stripped = line.strip()
        m = _ITEM_RE.match(stripped)
        if m:
            name = m.group(2)
            name_zh, advice = _CHECK_ZH.get(name, (name, ""))
            current = PrecheckItem(status=m.group(1), name=name,
                                   summary=m.group(3).strip(),
                                   name_zh=name_zh, advice=advice)
            result.items.append(current)
            continue
        m = _SUMMARY_RE.match(stripped)
        if m:
            result.summary = f"Result: {m.group(1)} |{m.group(2).strip()}"
            current = None
            continue        # 缩进补充行 -> 上一检查项的 detail
        if current is not None and stripped and line.startswith(" "):
            if current.detail:
                current.detail += "\n" + stripped
            else:
                current.detail = stripped
    if not result.ok and not result.items:
        result.summary = result.summary or "预检命令未产出可解析结果（详见日志）"
    return result


class PrecheckError(Exception):
    """预检执行失败。"""


class PrecheckRunner:
    def __init__(self, ssh: SSHClient, docker: Optional[DockerManager] = None):
        self.ssh = ssh
        self.docker = docker or DockerManager(ssh)

    # ------------------------------------------------------------ 内部
    def _exec_in_container(self, container: str, cmd: str,
                           timeout: float = 120) -> CommandResult:
        return self.ssh.exec(f"docker exec {shq(container)} /bin/bash -c {shq(cmd)}",
                             timeout=timeout)

    # ------------------------------------------------------------ API
    def run(self, container: str, mount_path: str = "",
            skip_bandwidth: bool = False, timeout: float = 3600,
            on_line: Optional[Callable[[str], None]] = None) -> PrecheckResult:
        """在容器内运行 ucm-toolkit run precheck。

        mount_path 非空时在其下创建临时子目录做带宽测试并自动清理；
        否则按 skip_bandwidth 决定是否跳过带宽检查（默认跳过）。
        临时子目录的创建/清理会通过 on_line 播报（[deployer] 前缀），
        便于用户在环境上核对目录是否存在/已被清除。
        """
        sub_dir = ""
        arg = ""
        if mount_path:
            mount_path = mount_path.rstrip("/") or "/"
            sub_dir = f"{mount_path}/ucm-precheck-{uuid.uuid4().hex[:8]}"
            res = self._exec_in_container(container, f"mkdir -p {shq(sub_dir)}")
            if not res.ok:
                raise PrecheckError(
                    f"创建预检临时目录失败 {sub_dir}: "
                    f"{(res.stderr or res.stdout).strip()[:200]}")
            if on_line:
                on_line(f"[deployer] 带宽测试临时目录: {sub_dir}（预检结束后自动清理，"
                        "可在环境上核对）")
            arg = f"--mount-path {shq(sub_dir)}"
        elif skip_bandwidth:
            arg = "--skip-bandwidth"

        cmd = (f"docker exec {shq(container)} ucm-toolkit run precheck"
               f" --no-color {arg}").rstrip()
        try:
            res = self.ssh.exec_stream(cmd, on_line=on_line, timeout=timeout)
        finally:
            if sub_dir:
                # 清理预检临时子目录（尽力而为，失败提示用户手动清理）
                try:
                    self._exec_in_container(container, f"rm -rf {shq(sub_dir)}")
                except Exception:
                    logger.warning("清理预检临时目录失败: %s", sub_dir,
                                   exc_info=True)
                    if on_line:
                        on_line(f"[deployer] ⚠ 清理临时目录失败，请手动删除: {sub_dir}")
                else:
                    if on_line:
                        on_line(f"[deployer] 已清理临时目录: {sub_dir}")
        if res.exit_code == 2:
            raise PrecheckError(
                f"precheck 用法错误(退出码 2)：{res.stderr.strip()[:300]}")
        return parse_precheck_output(res)
