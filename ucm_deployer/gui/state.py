# -*- coding: utf-8 -*-
"""GUI 全局状态。"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..core.command_generator import DeploymentScripts
from ..core.models import DeviceInfo, ServerInfo
from ..core.server_registry import ServerRegistry


class AppContext:
    """五个步骤页面共享的状态。"""

    def __init__(self, registry: Optional[ServerRegistry] = None):
        self.registry = registry or ServerRegistry()
        # 步骤1
        self.selected: List[ServerInfo] = []          # 本次部署的服务器
        self.devices: Dict[str, DeviceInfo] = {}      # server_id -> DeviceInfo
        # 步骤2
        self.images: Dict[str, str] = {}              # server_id -> 选定 UCM 镜像
        # 步骤3
        self.containers: Dict[str, str] = {}          # server_id -> 容器名
        # 步骤4/5
        self.scripts: Optional[DeploymentScripts] = None

    # ------------------------------------------------------------ 辅助
    def device_of(self, server: ServerInfo) -> DeviceInfo:
        return self.devices.get(server.id) or (server.device or DeviceInfo())

    def cards_of(self, server: ServerInfo) -> int:
        return self.device_of(server).count

    def reset_after_servers_changed(self) -> None:
        """步骤1选择变化时，后续步骤的依赖状态失效。"""
        ids = {s.id for s in self.selected}
        self.images = {k: v for k, v in self.images.items() if k in ids}
        self.containers = {k: v for k, v in self.containers.items() if k in ids}
        self.scripts = None
