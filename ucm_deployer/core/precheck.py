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
from .models import CommandResult, UCMInfo
from .shell import shq
from .ssh_client import SSHClient
from ..utils.log import get_logger

logger = get_logger(__name__)

# 检查项行：[PASS] kernel               5.10.0
_ITEM_RE = re.compile(r"^\[(PASS|WARN|FAIL|INFO|SKIP)\]\s+(\S+)\s*(.*)$")
# 汇总行：Result: PASSED (with warnings)  |  1 pass, 1 warn, 0 fail
_SUMMARY_RE = re.compile(r"^Result:\s*(.+?)\s*\|(.*)$")
_ASCEND_DRIVER_FILE_RE = re.compile(
    r"(?im)^\s*Version\s*=\s*([^\s#]+)")
_NPU_SMI_VERSION_RE = re.compile(
    r"(?im)(?:npu-smi\s+|Version:\s*)(\d+(?:\.[0-9A-Za-z.-]+)+)")
_NVIDIA_DRIVER_RE = re.compile(r"(?im)^\s*(\d+(?:\.\d+)+)\s*$")
_FACT_UCM_MARKER = "__DEPLOYER_UCM__"
_FACT_ASCEND_FILE_MARKER = "__DEPLOYER_ASCEND_DRIVER__"
_FACT_NPU_SMI_MARKER = "__DEPLOYER_NPU_SMI__"
_FACT_NVIDIA_MARKER = "__DEPLOYER_NVIDIA_DRIVER__"
_ASCEND_HDK_THRESHOLD = (25, 2, 0)

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


@dataclass
class PrecheckFacts:
    """Deployer 独立探测到的预检事实，用于增强 toolkit 的未知项。"""
    ucm: UCMInfo = field(default_factory=UCMInfo)
    driver_version: str = ""
    driver_source: str = ""


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


# 自定义预检配置在容器内的固定位置（run 时以 --config 引用）
CONFIG_CONTAINER_PATH = "/root/ucm-deploy/precheck.json"


class PrecheckRunner:
    def __init__(self, ssh: SSHClient, docker: Optional[DockerManager] = None):
        self.ssh = ssh
        self.docker = docker or DockerManager(ssh)

    # ------------------------------------------------------------ 内部
    def _exec_in_container(self, container: str, cmd: str,
                           timeout: float = 120) -> CommandResult:
        return self.ssh.exec(f"docker exec {shq(container)} /bin/bash -c {shq(cmd)}",
                             timeout=timeout)

    def collect_precheck_facts(self, container: str) -> PrecheckFacts:
        """用多个通用来源探测 UCM 与驱动版本，不绑定单一产品变体。"""
        probe_cmd = (
            f"printf '%s\\n' '{_FACT_UCM_MARKER}'; "
            "(python3 -m pip list --format=freeze 2>/dev/null "
            "|| pip list --format=freeze 2>/dev/null "
            "|| pip list 2>/dev/null)"
            " | grep -iE '^uc[-_.]?manager' | head -3; "
            f"printf '%s\\n' '{_FACT_ASCEND_FILE_MARKER}'; "
            "cat /usr/local/Ascend/driver/version.info 2>/dev/null; "
            f"printf '%s\\n' '{_FACT_NPU_SMI_MARKER}'; "
            "npu-smi info 2>/dev/null; "
            f"printf '%s\\n' '{_FACT_NVIDIA_MARKER}'; "
            "nvidia-smi --query-gpu=driver_version --format=csv,noheader"
            " 2>/dev/null | head -1"
        )
        res = self._exec_in_container(container, probe_cmd, timeout=90)
        if not (res.stdout or "").strip():
            return PrecheckFacts()

        sections = self._split_fact_sections(res.stdout)
        facts = PrecheckFacts(ucm=UCMInfo.from_freeze_output(
            sections.get(_FACT_UCM_MARKER, "")))
        ascend_file = sections.get(_FACT_ASCEND_FILE_MARKER, "")
        npu_smi = sections.get(_FACT_NPU_SMI_MARKER, "")
        nvidia = sections.get(_FACT_NVIDIA_MARKER, "")

        match = _ASCEND_DRIVER_FILE_RE.search(ascend_file)
        if match is not None:
            facts.driver_version = match.group(1)
            facts.driver_source = "/usr/local/Ascend/driver/version.info"
        else:
            match = _NPU_SMI_VERSION_RE.search(npu_smi)
            if match is not None:
                facts.driver_version = match.group(1)
                facts.driver_source = "npu-smi info"
            else:
                match = _NVIDIA_DRIVER_RE.search(nvidia)
                if match is not None:
                    facts.driver_version = match.group(1)
                    facts.driver_source = "nvidia-smi"
        return facts

    @staticmethod
    def _split_fact_sections(text: str) -> dict:
        markers = [_FACT_UCM_MARKER, _FACT_ASCEND_FILE_MARKER,
                   _FACT_NPU_SMI_MARKER, _FACT_NVIDIA_MARKER]
        positions = []
        for marker in markers:
            index = text.find(marker)
            if index >= 0:
                positions.append((index, marker))
        positions.sort()
        sections = {}
        for index, (marker_start, marker) in enumerate(positions):
            content_start = marker_start + len(marker)
            end = (positions[index + 1][0] if index + 1 < len(positions)
                   else len(text))
            sections[marker] = text[content_start:end].strip("\n")
        return sections

    def list_dirs(self, container: str, path: str = "/") -> List[str]:
        """列出容器内某目录下的子目录（GUI 目录浏览数据源，容器内视角）。"""
        path = path.rstrip("/") or "/"
        res = self._exec_in_container(
            container,
            f"find {shq(path)} -maxdepth 1 -mindepth 1 -type d 2>/dev/null"
            " | sort | head -200")
        if not res.ok:
            return []
        return [l.strip() for l in res.stdout.splitlines() if l.strip()]

    def _ensure_dir_in_container(self, container: str, path: str) -> None:
        """校验目录在容器内已存在（不存在则报错，绝不凭空创建）。"""
        res = self._exec_in_container(container, f"test -d {shq(path)}")
        if not res.ok:
            raise PrecheckError(
                f"挂载目录在容器内不存在: {path}\n"
                "请确认步骤3 已把该目录映射进容器（带宽测试须在容器内可见的"
                "挂载存储上执行）。")

    # ------------------------------------------------------------ 配置文件
    def locate_default_config(self, container: str) -> str:
        """定位容器内随 ucm-toolkit 安装的 precheck.defaults.json 路径。

        配置文件按 ucm_toolkit 包安装位置定位（config.py 以 __file__ 相对
        寻址），位于 <site-packages>/ucm_toolkit/tools/precheck/ 下。
        """
        py = ("import ucm_toolkit, os; "
              "print(os.path.join(os.path.dirname(ucm_toolkit.__file__), "
              "'tools', 'precheck', 'precheck.defaults.json'))")
        res = self._exec_in_container(container, f"python3 -c {shq(py)}")
        path = res.stdout.strip() if res.ok else ""
        return path if path.startswith("/") else ""

    def read_default_config(self, container: str) -> str:
        """读取容器内安装的出厂默认配置 JSON 文本（供界面加载编辑）。"""
        path = self.locate_default_config(container)
        if not path:
            raise PrecheckError(
                "未能在容器内定位 precheck.defaults.json（请确认镜像内已安装"
                " ucm-toolkit，且容器内 python3 可用）。")
        res = self._exec_in_container(container, f"cat {shq(path)}")
        if not res.ok:
            raise PrecheckError(
                f"读取默认配置失败 {path}: "
                f"{(res.stderr or res.stdout).strip()[:200]}")
        return res.stdout

    def config_exists(self, container: str) -> bool:
        """容器内是否已存在自定义预检配置。"""
        res = self._exec_in_container(
            container, f"test -f {shq(CONFIG_CONTAINER_PATH)}")
        return res.ok

    def read_custom_config(self, container: str) -> str:
        """读取容器内已保存的自定义配置 JSON 文本（须先 config_exists）。"""
        res = self._exec_in_container(
            container, f"cat {shq(CONFIG_CONTAINER_PATH)}")
        if not res.ok:
            raise PrecheckError(
                f"读取自定义预检配置失败 {CONFIG_CONTAINER_PATH}: "
                f"{(res.stderr or res.stdout).strip()[:200]}")
        return res.stdout

    def save_config(self, container: str, json_text: str) -> None:
        """校验 JSON 并写入容器内自定义配置（叠加于出厂默认之上）。"""
        try:
            import json as _json

            _json.loads(json_text)
        except ValueError as exc:
            raise PrecheckError(f"配置不是合法 JSON: {exc}")
        parent = CONFIG_CONTAINER_PATH.rsplit("/", 1)[0]
        res = self._exec_in_container(
            container,
            f"mkdir -p {shq(parent)} && "
            f"printf '%s' {shq(json_text)} > {shq(CONFIG_CONTAINER_PATH)}")
        if not res.ok:
            raise PrecheckError(
                f"保存预检配置到容器失败 {CONFIG_CONTAINER_PATH}: "
                f"{(res.stderr or res.stdout).strip()[:200]}")

    def remove_config(self, container: str) -> bool:
        """删除容器内自定义配置（恢复出厂默认）；返回删除前是否存在。"""
        if not self.config_exists(container):
            return False
        res = self._exec_in_container(
            container, f"rm -f {shq(CONFIG_CONTAINER_PATH)}")
        if not res.ok:
            raise PrecheckError(
                f"删除预检配置失败 {CONFIG_CONTAINER_PATH}: "
                f"{(res.stderr or res.stdout).strip()[:200]}")
        return True

    # ------------------------------------------------------------ API
    def run(self, container: str, mount_path: str = "",
            skip_bandwidth: bool = False, timeout: float = 3600,
            config_path: str = "",
            on_line: Optional[Callable[[str], None]] = None) -> PrecheckResult:
        """在容器内运行 ucm-toolkit run precheck。

        mount_path 非空时在其下创建临时子目录做带宽测试并自动清理；
        否则按 skip_bandwidth 决定是否跳过带宽检查（默认跳过）。
        config_path 非空时以 --config 引用容器内的自定义配置
        （叠加于出厂默认之上；文件不存在则报错）。
        临时子目录的创建/清理会通过 on_line 播报（[deployer] 前缀），
        便于用户在环境上核对目录是否存在/已被清除。
        """
        sub_dir = ""
        arg = ""
        if config_path:
            res = self._exec_in_container(container, f"test -f {shq(config_path)}")
            if not res.ok:
                raise PrecheckError(
                    f"自定义预检配置在容器内不存在: {config_path}\n"
                    "请重新打开「预检参数配置」保存，或恢复默认配置。")
            arg = f"--config {shq(config_path)}"
        if mount_path:
            mount_path = mount_path.rstrip("/") or "/"
            self._ensure_dir_in_container(container, mount_path)
            sub_dir = f"{mount_path}/ucm-precheck-{uuid.uuid4().hex[:8]}"
            res = self._exec_in_container(container, f"mkdir -p {shq(sub_dir)}")
            if not res.ok:
                raise PrecheckError(
                    f"创建预检临时目录失败 {sub_dir}: "
                    f"{(res.stderr or res.stdout).strip()[:200]}")
            if on_line:
                on_line(f"[deployer] 带宽测试临时目录: {sub_dir}（预检结束后自动清理，"
                        "可在环境上核对）")
            mount_arg = f"--mount-path {shq(sub_dir)}"
            arg = f"{arg} {mount_arg}".strip()
        elif skip_bandwidth:
            arg = f"{arg} --skip-bandwidth".strip()

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
        result = parse_precheck_output(res)
        if _precheck_needs_facts(result):
            enhance_precheck_result(result, self.collect_precheck_facts(container))
        return result


def _missing_summary(item: Optional[PrecheckItem]) -> bool:
    if item is None:
        return True
    summary = item.summary.strip().lower()
    return summary in ("", "-", "?", "none", "unknown", "hdk=?")


def _precheck_needs_facts(result: PrecheckResult) -> bool:
    by_name = {item.name: item for item in result.items}
    ucm = by_name.get("uc_manager")
    driver = by_name.get("accelerator_driver")
    ucm_unknown = ucm is not None and _missing_summary(ucm)
    driver_unknown = (driver is not None
                      and (_missing_summary(driver)
                           or "could not extract" in driver.detail.lower()))
    return ucm_unknown or driver_unknown


def _version_key(version: str) -> tuple:
    numbers = []
    for part in re.split(r"[._-]", version):
        if not part.isdigit():
            break
        numbers.append(int(part))
    return tuple(numbers)


def enhance_precheck_result(result: PrecheckResult,
                            facts: PrecheckFacts) -> PrecheckResult:
    """用独立探测事实修复 toolkit 对平台变体的未知项，并保留来源信息。"""
    by_name = {item.name: item for item in result.items}
    ucm_item = by_name.get("uc_manager")
    if (facts.ucm.installed and ucm_item is not None
            and _missing_summary(ucm_item)):
        ucm_item.status = "INFO"
        ucm_item.summary = f"{facts.ucm.package}={facts.ucm.version}"
        source = "[deployer] fact source: pip list"
        ucm_item.detail = (source + ("\n" + ucm_item.detail
                                     if ucm_item.detail else ""))

    driver_item = by_name.get("accelerator_driver")
    if facts.driver_version and driver_item is not None and _missing_summary(driver_item):
        driver_item.summary = (
            f"HDK={facts.driver_version}" if "npu-smi" in facts.driver_source
            or "Ascend" in facts.driver_source
            else f"driver={facts.driver_version}")
        source = f"[deployer] fact source: {facts.driver_source}"
        if "Ascend" in facts.driver_source or "npu-smi" in facts.driver_source:
            key = _version_key(facts.driver_version)
            driver_item.status = "PASS" if key > _ASCEND_HDK_THRESHOLD else "WARN"
            source += f"\nthreshold: HDK > {'.'.join(map(str, _ASCEND_HDK_THRESHOLD))}"
        elif driver_item.status != "PASS":
            driver_item.status = "PASS"
        driver_item.detail = (source + ("\ntoolkit output:\n" + driver_item.detail
                                        if driver_item.detail else ""))
    _refresh_precheck_summary(result)
    return result


def _refresh_precheck_summary(result: PrecheckResult) -> None:
    if not result.summary:
        return
    verdict = ("FAILED" if result.failed_count
               else "PASSED (with warnings)" if result.warn_count
               else "PASSED")
    info_count = sum(1 for item in result.items if item.status == "INFO")
    skip_count = sum(1 for item in result.items if item.status == "SKIP")
    result.summary = (
        f"Result: {verdict} |  {result.passed_count} pass, "
        f"{result.warn_count} warn, {result.failed_count} fail, "
        f"{skip_count} skip, {info_count} info")
