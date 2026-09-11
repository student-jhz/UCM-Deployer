# -*- coding: utf-8 -*-
"""公共路径工具。"""
from __future__ import annotations

from pathlib import Path


def default_config_dir() -> Path:
    """程序配置目录（服务器凭据等），默认 ~/.ucm_deployer"""
    return Path.home() / ".ucm_deployer"
