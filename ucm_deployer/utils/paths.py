# -*- coding: utf-8 -*-
"""本地配置目录。"""
from __future__ import annotations

from pathlib import Path


def default_config_dir() -> Path:
    d = Path.home() / ".ucm_deployer"
    d.mkdir(parents=True, exist_ok=True)
    return d
