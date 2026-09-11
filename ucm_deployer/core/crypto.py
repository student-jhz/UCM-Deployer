# -*- coding: utf-8 -*-
"""基于 Fernet 的本地凭据加密。

密钥保存在配置目录的 .secret.key 中（首次自动生成）。
密码等敏感字段在 servers.json 中仅以密文出现。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Union

from cryptography.fernet import Fernet, InvalidToken


class CryptoError(Exception):
    """解密失败（密钥不匹配或数据损坏）。"""


class Crypto:
    def __init__(self, key_file: Union[str, Path]):
        self.key_file = Path(key_file)
        self._fernet = Fernet(self._load_or_create_key())

    def _load_or_create_key(self) -> bytes:
        if self.key_file.exists():
            key = self.key_file.read_bytes().strip()
            if key:
                return key
        key = Fernet.generate_key()
        self.key_file.parent.mkdir(parents=True, exist_ok=True)
        self.key_file.write_bytes(key)
        try:
            os.chmod(self.key_file, 0o600)  # Windows 下尽力而为
        except OSError:
            pass
        return key

    def encrypt(self, text: str) -> str:
        return self._fernet.encrypt(text.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError, AttributeError) as exc:
            raise CryptoError("无法解密凭据（密钥不匹配或数据损坏）") from exc
