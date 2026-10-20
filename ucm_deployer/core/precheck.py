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
# 汇总行：Result: FAILED  |  1 pass, 1 warn, 1 fail, 1 skip, 3 info
_SUMMARY_RE = re.compile(r"^Result:\s*(\S+)\s*\|(.*)$")


@dataclass
class PrecheckItem:
    status: str            # PASS / WARN / FAIL / INFO / SKIP
    name: str              # 检查项名（如 kernel / accelerator_driver）
    summary: str = ""      # 行内摘要（如 5.10.0）
    detail: str = ""       # 缩进补充行（阈值/fix 建议等）


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


def parse_precheck_output(res: CommandResult) -> PrecheckResult:
    """解析 precheck 输出（--no-color 无 ANSI 码）为结构化结果。"""
    result = PrecheckResult(exit_code=res.exit_code, ok=res.exit_code == 0,
                            raw_output=res.stdout)
    current: Optional[PrecheckItem] = None
    for line in (res.stdout or "").splitlines():
        stripped = line.strip()
        m = _ITEM_RE.match(stripped)
        if m:
            current = PrecheckItem(status=m.group(1), name=m.group(2),
                                   summary=m.group(3).strip())
            result.items.append(current)
            continue
        m = _SUMMARY_RE.match(stripped)
        if m:
            result.summary = f"Result: {m.group(1)} |{m.group(2).strip()}"
            current = None
            continue
        # 缩进补充行 -> 上一检查项的 detail
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
