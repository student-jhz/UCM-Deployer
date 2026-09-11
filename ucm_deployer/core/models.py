# -*- coding: utf-8 -*-
"""核心数据模型。

所有模块共享的纯数据结构（dataclass），不依赖 SSH/GUI 等任何上层模块，
便于序列化持久化与单元测试。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class DeviceType(str, Enum):
    ASCEND = "ascend"
    NVIDIA = "nvidia"
    UNKNOWN = "unknown"


@dataclass
class DeviceInfo:
    """服务器上的加速器硬件信息。"""

    device_type: DeviceType = DeviceType.UNKNOWN
    model: str = ""          # 如 'Ascend 910B3' / 'NVIDIA A800-SXM4-80GB'
    count: int = 0           # 卡数
    detail: str = ""         # 原始探测输出（调试用）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_type": self.device_type.value if isinstance(self.device_type, DeviceType) else str(self.device_type),
            "model": self.model,
            "count": int(self.count or 0),
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "DeviceInfo":
        if not data:
            return cls()
        try:
            dtype = DeviceType(data.get("device_type", "unknown"))
        except ValueError:
            dtype = DeviceType.UNKNOWN
        return cls(
            device_type=dtype,
            model=str(data.get("model", "") or ""),
            count=int(data.get("count", 0) or 0),
            detail=str(data.get("detail", "") or ""),
        )

    @property
    def display(self) -> str:
        if self.device_type == DeviceType.UNKNOWN or not self.model:
            return "未检测"
        return f"{self.model} x{self.count}"


@dataclass
class ServerInfo:
    """一台待部署服务器的 SSH 登录信息。"""

    id: str
    name: str
    host: str
    port: int = 22
    username: str = "root"
    password: str = ""            # 仅内存中明文；落盘加密
    auth_type: str = "password"   # password | key
    key_path: str = ""
    passphrase: str = ""
    remark: str = ""
    device: Optional[DeviceInfo] = None
    created_at: str = ""
    last_used_at: str = ""

    @classmethod
    def create(
        cls,
        name: str,
        host: str,
        port: int = 22,
        username: str = "root",
        password: str = "",
        auth_type: str = "password",
        key_path: str = "",
        passphrase: str = "",
        remark: str = "",
    ) -> "ServerInfo":
        now = datetime.now().isoformat(timespec="seconds")
        return cls(
            id=uuid.uuid4().hex,
            name=name or host,
            host=host,
            port=port,
            username=username,
            password=password,
            auth_type=auth_type,
            key_path=key_path,
            passphrase=passphrase,
            remark=remark,
            created_at=now,
            last_used_at=now,
        )

    def to_dict(self, include_secret: bool = False) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "auth_type": self.auth_type,
            "key_path": self.key_path,
            "remark": self.remark,
            "device": self.device.to_dict() if self.device else None,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
        }
        if include_secret:
            d["password"] = self.password
            d["passphrase"] = self.passphrase
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ServerInfo":
        return cls(
            id=d.get("id") or uuid.uuid4().hex,
            name=d.get("name", ""),
            host=d.get("host", ""),
            port=int(d.get("port", 22) or 22),
            username=d.get("username", "root"),
            password=d.get("password", ""),
            auth_type=d.get("auth_type", "password"),
            key_path=d.get("key_path", ""),
            passphrase=d.get("passphrase", ""),
            remark=d.get("remark", ""),
            device=DeviceInfo.from_dict(d.get("device")),
            created_at=d.get("created_at", ""),
            last_used_at=d.get("last_used_at", ""),
        )

    @property
    def endpoint(self) -> str:
        return f"{self.host}:{self.port}"

    @property
    def display_device(self) -> str:
        return self.device.display if self.device else "未检测"


@dataclass
class CommandResult:
    """远程命令执行结果。"""

    command: str
    exit_code: int = -1
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    @property
    def output(self) -> str:
        """stdout + stderr 合并（用于日志展示）。"""
        parts = []
        if self.stdout:
            parts.append(self.stdout.rstrip("\n"))
        if self.stderr:
            parts.append(self.stderr.rstrip("\n"))
        return "\n".join(parts)


@dataclass
class DockerImage:
    repository: str
    tag: str
    image_id: str = ""
    size: str = ""
    created: str = ""

    @property
    def ref(self) -> str:
        if self.tag and self.tag != "<none>":
            return f"{self.repository}:{self.tag}"
        return self.image_id

    def to_dict(self) -> Dict[str, Any]:
        return {"repository": self.repository, "tag": self.tag, "image_id": self.image_id,
                "size": self.size, "created": self.created}


@dataclass
class DockerContainer:
    container_id: str
    names: str
    image: str
    status: str = ""
    state: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"container_id": self.container_id, "names": self.names, "image": self.image,
                "status": self.status, "state": self.state}


@dataclass
class UCMInfo:
    """UCM 包安装状态（pip show ucm 的解析结果）。"""

    installed: bool = False
    version: str = ""
    location: str = ""

    @classmethod
    def from_pip_show(cls, text: str) -> "UCMInfo":
        version = ""
        location = ""
        for line in (text or "").splitlines():
            key, _, value = line.partition(":")
            key = key.strip().lower()
            if key == "version":
                version = value.strip()
            elif key == "location":
                location = value.strip()
        installed = bool(version) and ("uc-manager" in text or "ucm" in text.lower())
        return cls(installed=installed, version=version, location=location)

    def __str__(self) -> str:
        if self.installed:
            loc = f" @ {self.location}" if self.location else ""
            return f"已安装 {self.version}{loc}"
        return "未安装"


@dataclass
class VolumeMount:
    """一个 -v 卷映射。"""

    host_path: str
    container_path: str
    readonly: bool = False

    def to_docker_arg(self) -> str:
        from ..core.shell import shq

        return f"-v {shq(self.host_path)}:{shq(self.container_path)}" + (":ro" if self.readonly else "")
