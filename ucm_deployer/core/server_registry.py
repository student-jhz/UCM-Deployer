# -*- coding: utf-8 -*-
"""服务器登录信息的本地持久化注册表。

servers.json 保存服务器列表；密码字段使用 Fernet 加密（密钥在同目录 .secret.key）。
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Union

from ..utils.log import get_logger
from ..utils.paths import default_config_dir
from .crypto import Crypto
from .models import DeviceInfo, ServerInfo

logger = get_logger(__name__)


class ServerRegistry:
    VERSION = 1

    def __init__(self, config_dir: Optional[Union[str, Path]] = None):
        self._dir = Path(config_dir) if config_dir else default_config_dir()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._file = self._dir / "servers.json"
        self._crypto = Crypto(self._dir / ".secret.key")
        self._lock = threading.RLock()

    # ------------------------------------------------------------ 持久化
    def _load(self) -> List[ServerInfo]:
        if not self._file.exists():
            return []
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.error("读取 servers.json 失败: %s", exc)
            return []
        servers: List[ServerInfo] = []
        for item in data.get("servers", []):
            try:
                info = ServerInfo.from_dict(item)
            except Exception:
                logger.exception("解析服务器条目失败: %r", item)
                continue
            enc = item.get("password_enc", "")
            if enc:
                try:
                    info.password = self._crypto.decrypt(enc)
                except Exception:
                    info.password = ""
                    logger.warning("服务器 %s 的密码解密失败，已清空", info.name)
            servers.append(info)
        return servers

    def _save(self, servers: List[ServerInfo]) -> None:
        items = []
        for s in servers:
            d = s.to_dict()
            d.pop("password", None)
            if s.password:
                d["password_enc"] = self._crypto.encrypt(s.password)
            items.append(d)
        content = json.dumps({"version": self.VERSION, "servers": items},
                             ensure_ascii=False, indent=2)
        self._file.write_text(content, encoding="utf-8")

    # ------------------------------------------------------------ API
    def list_servers(self) -> List[ServerInfo]:
        with self._lock:
            return self._load()

    def upsert(self, info: ServerInfo) -> None:
        with self._lock:
            servers = self._load()
            for i, s in enumerate(servers):
                if s.id == info.id:
                    servers[i] = info
                    break
            else:
                servers.append(info)
            self._save(servers)
        logger.info("服务器已保存: %s (%s)", info.name, info.endpoint)

    def remove(self, server_id: str) -> bool:
        with self._lock:
            servers = self._load()
            remain = [s for s in servers if s.id != server_id]
            removed = len(remain) != len(servers)
            if removed:
                self._save(remain)
        return removed

    def get(self, server_id: str) -> Optional[ServerInfo]:
        for s in self.list_servers():
            if s.id == server_id:
                return s
        return None

    def touch(self, server_id: str, device: Optional[DeviceInfo] = None) -> None:
        """更新最后使用时间（可同时更新探测到的设备信息）。"""
        with self._lock:
            servers = self._load()
            for s in servers:
                if s.id == server_id:
                    s.last_used_at = datetime.now().isoformat(timespec="seconds")
                    if device is not None:
                        s.device = device
            self._save(servers)
