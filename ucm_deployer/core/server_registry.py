# -*- coding: utf-8 -*-
"""服务器登录信息注册表：本地加密持久化（~/.ucm_deployer/servers.json）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from ..utils.log import get_logger
from ..utils.paths import default_config_dir
from . import crypto
from .models import DeviceInfo, ServerInfo

logger = get_logger(__name__)


class ServerRegistry:
    """服务器登录信息的增删改查（密码加密落盘）。"""

    def __init__(self, config_dir: Optional[Path] = None):
        self.dir = Path(config_dir) if config_dir else default_config_dir()
        self.path = self.dir / "servers.json"

    # ------------------------------------------------------------ 持久化
    def _load_raw(self) -> dict:
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            logger.exception("读取服务器注册表失败，忽略并重建")
            return {}

    def _save_raw(self, data: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(self.path)

    # ------------------------------------------------------------ API
    def list_servers(self) -> List[ServerInfo]:
        servers = []
        for item in self._load_raw().get("servers", []):
            try:
                info = ServerInfo.from_dict(item)
                if item.get("_password"):
                    info.password = crypto.decrypt(item["_password"])
                if item.get("_passphrase"):
                    info.passphrase = crypto.decrypt(item["_passphrase"])
                servers.append(info)
            except Exception:
                logger.exception("解析服务器条目失败，跳过: %s", item)
        servers.sort(key=lambda s: (not s.last_used_at, s.name))
        return servers

    def upsert(self, server: ServerInfo) -> None:
        data = self._load_raw()
        servers: List[dict] = data.get("servers", [])
        plain = server.to_dict(include_secret=False)
        if server.password:
            plain["_password"] = crypto.encrypt(server.password)
        if server.passphrase:
            plain["_passphrase"] = crypto.encrypt(server.passphrase)
        for i, item in enumerate(servers):
            if item.get("id") == server.id:
                servers[i] = plain
                break
        else:
            servers.append(plain)
        data["servers"] = servers
        self._save_raw(data)

    def remove(self, server_id: str) -> None:
        data = self._load_raw()
        data["servers"] = [s for s in data.get("servers", [])
                           if s.get("id") != server_id]
        self._save_raw(data)

    def get(self, server_id: str) -> Optional[ServerInfo]:
        for s in self.list_servers():
            if s.id == server_id:
                return s
        return None

    def touch(self, server_id: str, device: Optional[DeviceInfo] = None) -> None:
        """更新最近使用时间（可附带探测到的设备信息）。"""
        from datetime import datetime

        for s in self.list_servers():
            if s.id == server_id:
                s.last_used_at = datetime.now().isoformat(timespec="seconds")
                if device is not None:
                    s.device = device
                self.upsert(s)
                return
