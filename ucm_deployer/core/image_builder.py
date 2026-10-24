# -*- coding: utf-8 -*-
"""UCM 镜像构建：匹配校验、Dockerfile 生成、上传构建、UCM 安装校验。"""
from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .docker_manager import DockerError, DockerManager
from .models import DeviceInfo, DeviceType, UCMInfo
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
    image_tag: str                  # 新镜像名（有默认建议值，用户可改）
    ucm_whl_local: str = ""         # 本地 UCM whl（必填）
    wrapt_whl_local: str = ""       # 本地 wrapt whl（必填，先于 UCM 离线安装）
    toolkit_whl_local: str = ""     # 可选：本地 ucm-toolkit whl（GitHub Releases 下载）
    platform: str = "ascend"        # ascend | cuda（写入 ENV PLATFORM）

    def validate(self) -> None:
        if not self.base_image or not self.base_image.strip():
            raise ImageBuildError("未选择基础镜像")
        if not re.match(r"^[\w.\-/:]+$", self.image_tag or ""):
            raise ImageBuildError(f"非法的镜像名: {self.image_tag!r}")
        if not self.ucm_whl_local or not os.path.isfile(self.ucm_whl_local):
            raise ImageBuildError(f"UCM whl 文件不存在: {self.ucm_whl_local}")
        if not self.wrapt_whl_local or not os.path.isfile(self.wrapt_whl_local):
            raise ImageBuildError(f"wrapt whl 文件不存在: {self.wrapt_whl_local}")
        if self.toolkit_whl_local and not os.path.isfile(self.toolkit_whl_local):
            raise ImageBuildError(f"ucm-toolkit whl 文件不存在: {self.toolkit_whl_local}")
        for path in (self.ucm_whl_local, self.wrapt_whl_local, self.toolkit_whl_local):
            if path and re.search(r"\s", os.path.basename(path)):
                raise ImageBuildError(f"文件名不能包含空白字符: {path}")


@dataclass
class BuildResult:
    ok: bool
    image: str = ""
    ucm: Optional[UCMInfo] = None
    error: str = ""


@dataclass
class MatchResult:
    """服务器/镜像/安装包 匹配校验结果。"""

    ok: bool
    message: str = ""
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


@dataclass
class ImageRuntimeInfo:
    """基础镜像运行时信息，用于精确匹配 UCM/wrapt wheel。"""

    python_version: str = ""
    python_abi: str = ""
    arch: str = ""
    glibc_version: str = ""
    cann_version: str = ""
    cuda_version: str = ""
    variant: str = ""          # Ascend A2/A3/A5
    probe_ok: bool = False

    @classmethod
    def from_image_ref(cls, image_ref: str) -> "ImageRuntimeInfo":
        ref = (image_ref or "").lower()
        info = cls()
        if any(keyword in ref for keyword in ("ascend", "cann")):
            match = re.search(r"cann[.-]?(\d+)\.(\d+)", ref)
            if match is None:
                match = re.search(r"cann(\d)(\d)0", ref)
            if match is not None:
                info.cann_version = f"{int(match.group(1))}.{int(match.group(2))}"
        match = (re.search(r"cu(?:da)?[.-]?(\d{2})\.(\d+)", ref)
                 or re.search(r"cu(\d{2})(\d)", ref))
        if match is not None:
            info.cuda_version = f"{int(match.group(1))}.{int(match.group(2))}"
        variant = re.search(r"(?:^|[-_/.])(a[235])(?:[-_/.]|$)", ref)
        if variant is not None:
            info.variant = variant.group(1).lower()
        return info

    @classmethod
    def from_probe(cls, text: str, image_ref: str = "") -> "ImageRuntimeInfo":
        info = cls.from_image_ref(image_ref)
        output = text or ""

        def section(marker: str) -> str:
            pattern = rf"{re.escape(marker)}\n(.*?)(?=\n__[A-Z0-9_]+__\n|\Z)"
            match = re.search(pattern, output, re.S)
            return match.group(1).strip() if match else ""

        python_line = next(
            (line.strip() for line in section("__PYTHON__").splitlines()
             if line.strip().startswith("python=")), "")
        if python_line:
            info.python_version = python_line.split("=", 1)[1]
            major, _, minor = info.python_version.partition(".")
            if major and minor:
                info.python_abi = f"cp{major}{minor}"
            info.probe_ok = True

        arch_line = next(
            (line.strip() for line in section("__PYTHON__").splitlines()
             if line.strip().startswith("arch=")), "")
        if arch_line:
            arch = arch_line.split("=", 1)[1].lower()
            if arch in ("x86_64", "aarch64"):
                info.arch = arch
            info.probe_ok = True

        glibc_line = section("__GLIBC__")
        match = re.search(r"(\d+\.\d+)(?:\s|$)", glibc_line)
        if match is not None:
            info.glibc_version = match.group(1)
            info.probe_ok = True

        cuda_line = next(
            (line.strip() for line in section("__CUDA__").splitlines()
             if line.strip().startswith("cuda=")), "")
        if cuda_line:
            info.cuda_version = cuda_line.split("=", 1)[1]
            info.probe_ok = True

        cann_text = section("__CANN__")
        match = re.search(
            r"(?im)^\s*(?:Version|version)\s*=\s*(\d+\.\d+(?:\.\d+)?)",
            cann_text)
        if match is not None:
            info.cann_version = match.group(1)
            info.probe_ok = True
        return info

    @property
    def cann_code(self) -> str:
        if not self.cann_version:
            return ""
        parts = self.cann_version.split(".")
        if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
            return ""
        return f"cann{parts[0]}{int(parts[1]) * 10:02d}"

    @property
    def cuda_code(self) -> str:
        if not self.cuda_version:
            return ""
        parts = self.cuda_version.split(".")
        if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
            return ""
        return f"cu{parts[0]}{parts[1]}"

    @property
    def ucm_wheel_glibc(self) -> str:
        if self.cann_code == "cann920":
            return "2.34"
        return "2.28"

    @property
    def summary(self) -> str:
        parts = []
        if self.python_version:
            parts.append(f"Python {self.python_version}（{self.python_abi}）")
        if self.arch:
            parts.append(self.arch)
        if self.glibc_version:
            parts.append(f"glibc {self.glibc_version}")
        if self.cann_version:
            parts.append(f"CANN {self.cann_version}")
        if self.cuda_version:
            parts.append(f"CUDA {self.cuda_version}")
        if self.variant:
            parts.append(self.variant.upper())
        return " · ".join(parts)


def suggest_tag(base_ref: str, ucm_version: str = "") -> str:
    """根据基础镜像与 UCM 版本生成新镜像名。

    如 vllm-ascend:v0.23.0-a3 + UCM 0.8.0 -> vllm-ascend:ucm-0.8.0；
    未提供版本时为 vllm-ascend:ucm。
    """
    base_ref = (base_ref or "").strip()
    repo, _, tag = base_ref.rpartition(":")
    if not repo or "/" in tag:
        repo = base_ref or "ucm-engine"
    suffix = f"ucm-{ucm_version}" if ucm_version else "ucm"
    return f"{repo}:{suffix}"


def whl_version(filename: str) -> str:
    """从 whl 文件名解析版本号（{name}-{version}-{python}-{abi}-{platform}.whl）。"""
    name = os.path.basename(filename or "")
    if not name.lower().endswith(".whl"):
        return ""
    parts = name[:-4].split("-")
    return parts[1] if len(parts) >= 5 else ""


def image_platform(base_image: str) -> str:
    """从镜像名推断平台：'ascend' / 'nvidia'；无法识别返回 ''。"""
    ref = (base_image or "").lower()
    if any(k in ref for k in ("ascend", "cann")):
        return "ascend"
    if any(k in ref for k in ("nvidia", "cuda")):
        return "nvidia"
    return ""


def whl_arch(filename: str) -> str:
    """从 whl 文件名解析 CPU 架构标记（x86_64 / aarch64）。

    纯 Python 包（platform tag 为 any，如 py3-none-any）返回空串（跨平台）。
    """
    name = os.path.basename(filename or "")
    if not name.lower().endswith(".whl"):
        return ""
    parts = name[:-4].split("-")
    if len(parts) < 5:
        return ""
    tag = parts[-1].lower()
    if tag.endswith("any"):
        return ""
    if "x86_64" in tag or "amd64" in tag:
        return "x86_64"
    if "aarch64" in tag or "arm64" in tag or "armv7" in tag:
        return "aarch64"
    return ""


def whl_python_abi(filename: str) -> str:
    """解析 wheel 的 Python/ABI 标记（如 cp312、py3）。"""
    name = os.path.basename(filename or "")
    if not name.lower().endswith(".whl"):
        return ""
    parts = name[:-4].split("-")
    if len(parts) < 5:
        return ""
    tag = parts[2].lower()
    return tag if tag.startswith(("cp", "py")) else ""


def whl_platform_tag(filename: str) -> str:
    """解析 wheel 的平台标签（如 manylinux_2_28_aarch64）。"""
    name = os.path.basename(filename or "")
    if not name.lower().endswith(".whl"):
        return ""
    parts = name[:-4].split("-")
    return parts[-1].lower() if len(parts) >= 5 else ""


def whl_glibc(filename: str) -> str:
    """解析 manylinux wheel 要求的最低 glibc 版本。"""
    tag = whl_platform_tag(filename)
    match = re.search(r"manylinux_2_(\d+)", tag)
    if match is not None:
        return f"2.{match.group(1)}"
    if "manylinux2014" in tag:
        return "2.17"
    if "manylinux1" in tag:
        return "2.5"
    return ""


def wheel_distribution(filename: str) -> str:
    """解析 wheel 的 distribution 名并归一化为连字符。"""
    name = os.path.basename(filename or "")
    if not name.lower().endswith(".whl"):
        return ""
    return name[:-4].split("-")[0].replace("_", "-").lower()


def ascend_variant_from_device(model: str) -> str:
    """从 npu-smi 型号推断 UCM 的 A2/A3/A5 变体。"""
    text = (model or "").lower().replace(" ", "")
    if "950" in text or "a5" in text:
        return "a5"
    if "9391" in text or "a3" in text:
        return "a3"
    if "910b" in text or "a2" in text:
        return "a2"
    return ""


def ucm_wheel_components(filename: str) -> tuple:
    """返回 UCM wheel 的 (平台, runtime, 变体)。"""
    distribution = wheel_distribution(filename)
    if not distribution.startswith("uc-manager"):
        return "", "", ""
    if "cann" in distribution:
        match = re.search(r"cann(\d{3})", distribution)
        runtime = match.group(1) if match else ""
        variant = re.search(r"(?:^|-)(a[235])(?:$|-)", distribution)
        return "cann", runtime, variant.group(1) if variant else ""
    if "cuda" in distribution:
        match = re.search(r"cu(\d{3})", distribution)
        return "cuda", match.group(1) if match else "", ""
    return "generic", "", ""


def expected_ucm_components(device: Optional[DeviceInfo], base_image: str,
                            runtime: Optional[ImageRuntimeInfo]) -> tuple:
    """根据设备与基础镜像运行时推断应选择的 UCM wheel 组件。"""
    info = runtime or ImageRuntimeInfo.from_image_ref(base_image)
    if device is not None and device.device_type == DeviceType.NVIDIA:
        return "cuda", info.cuda_code[2:] if info.cuda_code else "", ""
    if device is not None and device.device_type == DeviceType.ASCEND:
        variant = info.variant or ascend_variant_from_device(device.model)
        return "cann", info.cann_code[4:] if info.cann_code else "", variant
    if "cuda" in (base_image or "").lower():
        return "cuda", info.cuda_code[2:] if info.cuda_code else "", ""
    return "cann", info.cann_code[4:] if info.cann_code else "", info.variant


def validate_whl(filename: str, expected_distribution: str,
                 runtime: Optional[ImageRuntimeInfo] = None,
                 fallback_arch: str = "") -> tuple:
    """校验单个 wheel 与基础镜像运行时的匹配性，返回 (errors, warnings)。"""
    errors: List[str] = []
    warnings: List[str] = []
    name = os.path.basename(filename or "")
    distribution = wheel_distribution(filename)
    if expected_distribution and not distribution.startswith(expected_distribution):
        errors.append(f"{name} 不是 {expected_distribution} 包")

    wheel_abi = whl_python_abi(filename)
    runtime_abi = runtime.python_abi if runtime is not None else ""
    if (wheel_abi.startswith("cp") and runtime_abi
            and wheel_abi != runtime_abi):
        errors.append(
            f"{name} Python 标记不匹配：wheel 为 {wheel_abi}，"
            f"基础镜像为 {runtime_abi}")
    elif (wheel_abi.startswith("py") and runtime_abi
          and wheel_abi == "py2" and runtime_abi.startswith("cp3")):
        errors.append(f"{name} 是 Python 2 wheel，与基础镜像 {runtime_abi} 不匹配")
    elif wheel_abi.startswith("cp") and not runtime_abi:
        warnings.append(f"未能确认基础镜像 Python 版本，跳过 {name} 的 {wheel_abi} 校验")

    wheel_arch = whl_arch(filename)
    target_arch = (runtime.arch if runtime is not None else "") or fallback_arch
    if wheel_arch and target_arch and wheel_arch != target_arch:
        errors.append(
            f"{name} 架构不匹配：wheel 为 {wheel_arch}，基础镜像为 {target_arch}")
    elif wheel_arch and not target_arch:
        warnings.append(f"未能确认基础镜像架构，跳过 {name} 的架构校验")

    tag = whl_platform_tag(filename)
    required_glibc = whl_glibc(filename)
    image_glibc = runtime.glibc_version if runtime is not None else ""
    if "musllinux" in tag and image_glibc:
        errors.append(f"{name} 是 musllinux 包，与基础镜像 glibc 环境不匹配")
    if required_glibc and image_glibc:
        if tuple(map(int, required_glibc.split("."))) > tuple(
                map(int, image_glibc.split("."))):
            errors.append(
                f"{name} 要求 glibc ≥ {required_glibc}，基础镜像为 {image_glibc}")
    elif required_glibc and not image_glibc:
        warnings.append(f"未能确认基础镜像 glibc 版本，跳过 {name} 的 manylinux 校验")
    if tag and tag != "any" and "linux" not in tag:
        errors.append(f"{name} 不是 Linux wheel（平台标签 {tag}）")
    return errors, warnings


def fetch_server_arch(ssh: SSHClient) -> str:
    """获取服务器 CPU 架构（uname -m）；仅返回 x86_64 / aarch64，失败返回 ''。"""
    try:
        res = ssh.exec("uname -m", timeout=15)
    except SSHError:
        return ""
    if not res.ok:
        return ""
    arch = (res.stdout or "").strip().lower()
    return arch if arch in ("x86_64", "aarch64") else ""


def fetch_image_runtime_info(ssh: SSHClient, image_ref: str) -> ImageRuntimeInfo:
    """在基础镜像内探测 Python、架构、glibc、CANN/CUDA 信息。"""
    python_code = (
        "import platform,sys; "
        "print('python=%d.%d' % (sys.version_info.major, sys.version_info.minor)); "
        "print('arch=' + platform.machine())")
    probe = (
        "printf '%s\\n' '__PYTHON__'; "
        f"python3 -c {shq(python_code)} 2>/dev/null || "
        f"python -c {shq(python_code)} 2>/dev/null || true; "
        "printf '%s\\n' '__GLIBC__'; ldd --version 2>/dev/null | head -1; "
        "printf '%s\\n' '__CUDA__'; "
        "python3 -c 'import torch; print(\"cuda=\" + str(torch.version.cuda or \"\"))'"
        " 2>/dev/null || true; "
        "printf '%s\\n' '__CANN__'; "
        "for f in /usr/local/Ascend/ascend-toolkit/version.info "
        "/usr/local/Ascend/ascend-toolkit/latest/version.info "
        "/usr/local/Ascend/ascend-toolkit/*/version.info; do "
        "[ -f \"$f\" ] && cat \"$f\" && break; done"
    )
    command = (f"docker run --rm --entrypoint /bin/sh {shq(image_ref)} "
               f"-c {shq(probe)}")
    try:
        result = ssh.exec(command, timeout=120)
    except SSHError:
        return ImageRuntimeInfo.from_image_ref(image_ref)
    if not result.ok:
        return ImageRuntimeInfo.from_image_ref(image_ref)
    return ImageRuntimeInfo.from_probe(result.stdout, image_ref)


def validate_build_match(device: Optional[DeviceInfo], server_arch: str,
                         base_image: str, ucm_whl: str,
                         wrapt_whl: str,
                         image_runtime: Optional[ImageRuntimeInfo] = None,
                         toolkit_whl: str = "") -> MatchResult:
    """校验「服务器设备 / 基础镜像 / UCM、wrapt 安装包」是否匹配。

    - 设备已探测且卡数 > 0
    - 镜像平台（按名称推断）与服务器设备类型一致（无法识别则警告）
    - 基础镜像运行时（Python/ABI、架构、glibc）与 UCM、wrapt wheel 一致
    - UCM wheel 的 CANN/CUDA、A2/A3/A5 变体与基础镜像和设备一致
    """
    errors: List[str] = []
    warnings: List[str] = []
    runtime = image_runtime or ImageRuntimeInfo.from_image_ref(base_image)

    if device is None or device.device_type == DeviceType.UNKNOWN or device.count <= 0:
        errors.append("服务器未探测到加速设备（请先在步骤1「探测设备」）")
    else:
        plat = image_platform(base_image)
        if not plat:
            warnings.append(
                f"无法从镜像名 {base_image} 识别平台（ascend/nvidia），"
                f"请自行确认镜像与服务器设备 {device.display} 匹配")
        elif plat != device.device_type.value:
            errors.append(
                f"镜像 {base_image} 疑似 {plat} 平台镜像，与服务器设备 "
                f"{device.device_type.value}（{device.display}）不匹配")

    expected_platform, expected_runtime, expected_variant = \
        expected_ucm_components(device, base_image, runtime)
    device_variant = ascend_variant_from_device(device.model) if device is not None else ""
    if (device_variant and runtime.variant
            and device_variant != runtime.variant):
        errors.append(
            f"基础镜像变体 {runtime.variant.upper()} 与服务器设备变体 "
            f"{device_variant.upper()} 不匹配")

    wheel_platform, wheel_runtime, wheel_variant = ucm_wheel_components(ucm_whl)
    if wheel_platform == "generic":
        if expected_platform or expected_variant:
            warnings.append(
                f"{os.path.basename(ucm_whl)} 为通用 UCM 包，"
                "请确认其支持当前 CANN/CUDA 与设备变体")
    elif wheel_platform != expected_platform:
        errors.append(
            f"UCM whl 平台不匹配：{os.path.basename(ucm_whl)} 为 {wheel_platform}，"
            f"当前应为 {expected_platform}")
    else:
        if wheel_runtime and expected_runtime and wheel_runtime != expected_runtime:
            errors.append(
                f"UCM whl runtime 不匹配：{os.path.basename(ucm_whl)} 为 "
                f"{wheel_runtime}，基础镜像为 {expected_runtime}")
        elif expected_runtime and not wheel_runtime:
            warnings.append("无法从 UCM whl 文件名识别 CANN/CUDA runtime 版本")
        if wheel_variant and expected_variant and wheel_variant != expected_variant:
            errors.append(
                f"UCM whl 设备变体不匹配：{os.path.basename(ucm_whl)} 为 "
                f"{wheel_variant.upper()}，当前应为 {expected_variant.upper()}")
        elif expected_variant and not wheel_variant:
            warnings.append(f"无法从 UCM whl 文件名识别 {expected_variant.upper()} 变体")

    target_arch = runtime.arch or server_arch
    for label, whl, distribution in (
            ("UCM whl", ucm_whl, "uc-manager"),
            ("wrapt whl", wrapt_whl, "wrapt"),
            ("ucm-toolkit whl", toolkit_whl, "ucm-toolkit")):
        if not whl:
            continue
        wheel_errors, wheel_warnings = validate_whl(
            whl, distribution, runtime=runtime, fallback_arch=server_arch)
        errors.extend(f"{label} {error}" for error in wheel_errors)
        warnings.extend(f"{label} {warning}" for warning in wheel_warnings)
    if not target_arch:
        warnings.append("未能获取基础镜像/服务器 CPU 架构，已跳过 whl 架构校验")
    if not runtime.probe_ok:
        warnings.append("基础镜像运行时探测不完整，构建前请重点核对 whl 兼容性")

    if errors:
        return MatchResult(False, "；".join(errors), warnings, errors)
    detail = f"服务器 {device.display}" if device and device.count > 0 else "服务器"
    if target_arch:
        detail += f"（{target_arch}）"
    if runtime.summary:
        detail += f"；基础镜像 {runtime.summary}"
    return MatchResult(True, f"校验通过：{detail} 与镜像/安装包匹配", warnings)


def generate_dockerfile(config: ImageBuildConfig, ucm_name: str,
                        wrapt_name: str, toolkit_name: str = "") -> str:
    """生成构建带 UCM 引擎镜像的 Dockerfile（纯函数，便于测试）。

    wrapt 为 UCM 的唯一外部依赖，与 UCM、ucm-toolkit 一同本地离线安装
    （--no-index --find-links）。
    """
    offline_flags = "--no-index --find-links=/tmp/ucm-pkgs"
    lines = [
        "# 由 UCM Deployer 自动生成",
        f"FROM {config.base_image}",
        f"ENV PLATFORM={config.platform}",
        "WORKDIR /tmp/ucm-pkgs",
        f"COPY {ucm_name} /tmp/ucm-pkgs/",
        f"COPY {wrapt_name} /tmp/ucm-pkgs/",
    ]
    if toolkit_name:
        lines.append(f"COPY {toolkit_name} /tmp/ucm-pkgs/")
    lines.append("")
    lines.append("# 离线安装 UCM 依赖 wrapt（本地 whl）")
    lines.append(f"RUN pip install --no-cache-dir {offline_flags} /tmp/ucm-pkgs/{wrapt_name}")
    lines.append("# 安装 UCM")
    lines.append(f"RUN pip install --no-cache-dir {offline_flags} /tmp/ucm-pkgs/{ucm_name}")
    if toolkit_name:
        lines.append("")
        lines.append("# 安装 ucm-toolkit")
        lines.append(
            f"RUN pip install --no-cache-dir {offline_flags} /tmp/ucm-pkgs/{toolkit_name}")
    return "\n".join(lines) + "\n"


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
            ucm_name = _upload(config.ucm_whl_local, remote_dir, 5, 30, "UCM whl")
            _check_cancel()
            wrapt_name = _upload(config.wrapt_whl_local, remote_dir, 30, 40, "wrapt whl")
            _check_cancel()
            toolkit_name = ""
            if config.toolkit_whl_local:
                toolkit_name = _upload(config.toolkit_whl_local, remote_dir,
                                       40, 55, "ucm-toolkit whl")
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
                    f"镜像 {config.image_tag} 构建成功但未检测到 UCM 安装，"
                    "请检查 whl 文件")
            progress(100, f"构建完成: {config.image_tag}")
            return BuildResult(ok=True, image=config.image_tag, ucm=ucm)
        except (SSHError, DockerError) as exc:
            raise ImageBuildError(str(exc)) from exc
        finally:
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
        progress(int(70 * sent / max(total, 1)),
                 f"上传镜像包 {sent / 1e6:.1f}/{total / 1e6:.1f} MB")

    ssh.upload_file(local_tar, remote, progress_cb=cb)
    progress(75, "docker load ...")
    log(f"$ docker load -i {remote}")
    refs = docker.load_image(remote, on_line=lambda l: log(l))
    progress(100, "docker load 完成")
    return refs
