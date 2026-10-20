# -*- coding: utf-8 -*-
"""核心数据模型（纯 dataclass，不依赖 SSH/GUI，便于序列化与单元测试）。"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
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
    arch: str = ""           # CPU 架构（uname -m），如 'aarch64' / 'x86_64'
    detail: str = ""         # 原始探测输出（调试用）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_type": self.device_type.value
            if isinstance(self.device_type, DeviceType) else str(self.device_type),
            "model": self.model,
            "count": int(self.count or 0),
            "arch": self.arch,
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
            arch=str(data.get("arch", "") or ""),
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
        return {"repository": self.repository, "tag": self.tag,
                "image_id": self.image_id, "size": self.size, "created": self.created}


@dataclass
class UCMInfo:
    """UCM 包安装状态（pip 输出解析结果）。

    UCM 包名存在平台变体（uc-manager、uc-manager-cann910-a2 等），
    统一按 uc[-_.]?manager 前缀宽泛匹配。
    """

    installed: bool = False
    version: str = ""
    location: str = ""
    package: str = ""       # 实际包名（如 uc-manager-cann910-a2）

    _NAME_RE = re.compile(r"(?i)^uc[-_.]?manager")

    @classmethod
    def from_pip_show(cls, text: str) -> "UCMInfo":
        version = ""
        location = ""
        package = ""
        for line in (text or "").splitlines():
            key, _, value = line.partition(":")
            key = key.strip().lower()
            if key == "name":
                package = value.strip()
            elif key == "version":
                version = value.strip()
            elif key == "location":
                location = value.strip()
        installed = bool(version) and bool(package) and bool(cls._NAME_RE.match(package))
        return cls(installed=installed, version=version,
                   location=location, package=package)

    @classmethod
    def from_freeze_output(cls, text: str) -> "UCMInfo":
        """解析 `pip list --format=freeze` 过滤输出（name==version 每行一个）。"""
        for line in (text or "").splitlines():
            name, sep, version = line.partition("==")
            if not sep:
                continue
            name = name.strip()
            if cls._NAME_RE.match(name):
                return cls(installed=True, version=version.strip(), package=name)
        return cls()

    def __str__(self) -> str:
        if self.installed:
            loc = f" @ {self.location}" if self.location else ""
            pkg = self.package or "uc-manager"
            return f"已安装 {pkg} {self.version}{loc}"
        return "未安装"


@dataclass
class VolumeMount:
    """一个 -v 卷映射。"""

    host_path: str
    container_path: str
    readonly: bool = False

    def to_docker_arg(self) -> str:
        from .shell import shq

        return f"-v {shq(self.host_path)}:{shq(self.container_path)}" + (":ro" if self.readonly else "")
