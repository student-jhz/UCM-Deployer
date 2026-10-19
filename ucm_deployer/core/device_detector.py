# -*- coding: utf-8 -*-
"""设备探测：Ascend NPU / NVIDIA GPU 识别、卡数、CPU 架构。"""
from __future__ import annotations

import re
from typing import Optional

from .models import DeviceInfo, DeviceType
from .ssh_client import SSHClient

_NPU_ROW = re.compile(
    r"^\|\s*(\d+)\s+([A-Za-z0-9][A-Za-z0-9\-]*)\s+\|\s*(OK|Warning|Alarm|Fault)\s*\|",
    re.M,
)


class DeviceDetector:
    def __init__(self, ssh: SSHClient):
        self.ssh = ssh

    def detect(self) -> DeviceInfo:
        """按 Ascend -> NVIDIA 顺序探测，并附带 CPU 架构（uname -m）。"""
        info = self._detect_ascend()
        if info is None or info.count <= 0:
            info = self._detect_nvidia()
        if info is None or info.count <= 0:
            info = DeviceInfo()
        info.arch = self.detect_arch()
        return info

    def detect_arch(self) -> str:
        """CPU 架构（uname -m）；仅返回 x86_64 / aarch64，失败返回 ''。"""
        res = self.ssh.exec("uname -m", timeout=15)
        if not res.ok:
            return ""
        arch = (res.stdout or "").strip().lower()
        return arch if arch in ("x86_64", "aarch64") else ""

    def _detect_ascend(self) -> Optional[DeviceInfo]:
        res = self.ssh.exec("npu-smi info", timeout=60)
        if not res.ok:
            return self._detect_ascend_by_dev()
        rows = _NPU_ROW.findall(res.stdout)
        if not rows:
            return self._detect_ascend_by_dev()
        ids = sorted({int(r[0]) for r in rows})
        raw_model = rows[0][1]
        model = raw_model if re.match(r"(?i)^(ascend|atlas)", raw_model) else f"Ascend {raw_model}"
        return DeviceInfo(DeviceType.ASCEND, model, len(ids), detail=res.stdout[:4000])

    def _detect_ascend_by_dev(self) -> Optional[DeviceInfo]:
        res = self.ssh.exec(
            "ls -1 /dev/ 2>/dev/null | grep -cE '^davinci[0-9]+$'", timeout=30)
        if res.ok:
            try:
                count = int(res.stdout.strip())
            except ValueError:
                return None
            if count > 0:
                return DeviceInfo(DeviceType.ASCEND, "Ascend NPU(型号未知)", count,
                                  detail="通过 /dev/davinci* 探测")
        return None

    def _detect_nvidia(self) -> Optional[DeviceInfo]:
        res = self.ssh.exec(
            "nvidia-smi --query-gpu=index,name --format=csv,noheader", timeout=60)
        if res.ok and res.stdout.strip():
            names = []
            for line in res.stdout.strip().splitlines():
                if not line.strip():
                    continue
                parts = line.split(",", 1)
                names.append(parts[1].strip() if len(parts) == 2 else line.strip())
            if names:
                return DeviceInfo(DeviceType.NVIDIA, names[0], len(names),
                                  detail=res.stdout[:4000])
        res2 = self.ssh.exec("lspci 2>/dev/null | grep -ci nvidia", timeout=30)
        if res2.ok:
            try:
                count = int(res2.stdout.strip())
            except ValueError:
                count = 0
            if count > 0:
                return DeviceInfo(DeviceType.NVIDIA, "NVIDIA GPU(型号未知)", count,
                                  detail="通过 lspci 探测")
        return None
