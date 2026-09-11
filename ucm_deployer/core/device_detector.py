# -*- coding: utf-8 -*-
"""设备探测：识别 Ascend NPU / NVIDIA GPU、卡数、型号一致性校验。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .models import DeviceInfo, DeviceType
from .ssh_client import SSHClient

# npu-smi info 表格中每个 NPU 的第一行： | <id>  <name> | OK | ...
_NPU_ROW = re.compile(
    r"^\|\s*(\d+)\s+([A-Za-z0-9][A-Za-z0-9\-]*)\s+\|\s*(OK|Warning|Alarm|Fault)\s*\|",
    re.M,
)


@dataclass
class ConsistencyResult:
    ok: bool
    message: str
    warnings: List[str] = field(default_factory=list)


class DeviceDetector:
    def __init__(self, ssh: SSHClient):
        self.ssh = ssh

    # ------------------------------------------------------------ 探测
    def detect(self) -> DeviceInfo:
        """按 Ascend -> NVIDIA 顺序探测。"""
        info = self._detect_ascend()
        if info is not None and info.count > 0:
            return info
        info = self._detect_nvidia()
        if info is not None and info.count > 0:
            return info
        return DeviceInfo()

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
        return DeviceInfo(DeviceType.ASCEND, model, len(ids), res.stdout[:4000])

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
                                  "通过 /dev/davinci* 探测")
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
                return DeviceInfo(DeviceType.NVIDIA, names[0], len(names), res.stdout[:4000])
        # 回退：lspci
        res2 = self.ssh.exec("lspci 2>/dev/null | grep -ci nvidia", timeout=30)
        if res2.ok:
            try:
                count = int(res2.stdout.strip())
            except ValueError:
                count = 0
            if count > 0:
                return DeviceInfo(DeviceType.NVIDIA, "NVIDIA GPU(型号未知)", count,
                                  "通过 lspci 探测")
        return None

    # ------------------------------------------------------------ 一致性
    @staticmethod
    def verify_consistency(infos: Dict[str, DeviceInfo]) -> ConsistencyResult:
        """校验多台服务器设备型号一致（步骤1）。

        infos: {服务器名: 设备信息}；单台也允许（跳过一致性）。
        """
        if not infos:
            return ConsistencyResult(False, "没有待校验的服务器")
        undetected = [name for name, d in infos.items()
                      if d.device_type == DeviceType.UNKNOWN or d.count <= 0]
        if undetected:
            return ConsistencyResult(
                False, "以下服务器未能探测到加速设备: " + ", ".join(undetected))

        names = list(infos)
        if len(names) == 1:
            d = infos[names[0]]
            return ConsistencyResult(True, f"单台服务器 {names[0]}: {d.display}")

        first = infos[names[0]]
        mismatched = [n for n in names
                      if infos[n].device_type != first.device_type
                      or infos[n].model != first.model]
        if mismatched:
            detail = "; ".join(f"{n}: {infos[n].display}" for n in names)
            return ConsistencyResult(
                False, "服务器设备型号不一致 -> " + detail)

        warnings = []
        counts = {n: infos[n].count for n in names}
        if len(set(counts.values())) > 1:
            warnings.append(
                "各服务器卡数不同(" + ", ".join(f"{n}={c}卡" for n, c in counts.items())
                + ")，请确认拓扑规划时每节点卡数按实际值配置")
        summary = f"全部 {len(names)} 台服务器设备一致: {first.display}"
        return ConsistencyResult(True, summary, warnings)
