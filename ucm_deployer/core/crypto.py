# -*- coding: utf-8 -*-
"""SSH 登录凭据本地加密（Fernet）。

密钥与密文均保存在 ~/.ucm_deployer/ 下；防止 servers.json 被单独拷贝后
直接泄露明文密码，属内网运维工具的折中方案。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from ..utils.paths import default_config_dir


class CryptoError(Exception):
    """加密/解密失败。"""


def _key_path() -> Path:
    return default_config_dir() / ".secret.key"


def ensure_key() -> bytes:
    """获取（不存在则生成）Fernet 密钥。"""
    path = _key_path()
    if path.is_file():
        data = path.read_bytes().strip()
        if data:
            return data
    key = Fernet.generate_key()
    path.write_bytes(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def encrypt(plaintext: str) -> str:
    """加密为可存 JSON 的密文（str）。"""
    if not plaintext:
        return ""
    try:
        return Fernet(ensure_key()).encrypt(plaintext.encode("utf-8")).decode("ascii")
    except Exception as exc:
        raise CryptoError(f"加密失败: {exc}") from exc


def decrypt(ciphertext: str) -> str:
    """解密密文；失败（密钥丢失/篡改）返回空串。"""
    if not ciphertext:
        return ""
    try:
        return Fernet(ensure_key()).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""
    except Exception as exc:
        raise CryptoError(f"解密失败: {exc}") from exc


def _selftest() -> bool:  # pragma: no cover - 手动验证用
    token = encrypt("你好 UCM")
    return decrypt(token) == "你好 UCM"


def load_key_for_test(tmp_dir: Path) -> Optional[bytes]:  # pragma: no cover
    """测试辅助：从指定目录读取密钥（不生成）。"""
    p = tmp_dir / ".secret.key"
    return p.read_bytes().strip() if p.is_file() else None
