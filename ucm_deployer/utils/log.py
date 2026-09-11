# -*- coding: utf-8 -*-
"""统一日志：控制台 + ~/.ucm_deployer/logs/ucm-deployer-YYYYMMDD.log"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from .paths import default_config_dir

_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def get_logger(name: str) -> logging.Logger:
    if not name.startswith("ucm_deployer"):
        name = f"ucm_deployer.{name}"
    return logging.getLogger(name)


def setup_logging(level: int = logging.INFO, config_dir: Optional[Union[str, Path]] = None) -> logging.Logger:
    """初始化根 logger（幂等），返回 'ucm_deployer' logger。"""
    root = logging.getLogger("ucm_deployer")
    if root.handlers:
        return root
    root.setLevel(level)

    log_dir = (Path(config_dir) if config_dir else default_config_dir()) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(log_dir / f"ucm-deployer-{datetime.now():%Y%m%d}.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter(_FMT))
    root.addHandler(fh)

    ch = logging.StreamHandler(sys.stderr)
    ch.setFormatter(logging.Formatter(_FMT))
    root.addHandler(ch)
    return root
