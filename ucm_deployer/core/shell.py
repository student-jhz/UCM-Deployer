# -*- coding: utf-8 -*-
"""远端 shell 命令的引号与续行处理。"""
from __future__ import annotations

import re

_SAFE = re.compile(r"^[A-Za-z0-9_./:=,@%+~-]+$")


def shq(s: str) -> str:
    """安全字符串原样返回；否则单引号包裹（内嵌单引号转义为 '\\''）。"""
    s = str(s)
    if _SAFE.match(s) and not s.startswith("-"):
        return s
    return "'" + s.replace("'", "'\\''") + "'"


_LINE_CONT = re.compile(r"\\\r?\n")


def flatten_command(cmd: str) -> str:
    """把多行续行命令压成单行（多余空白折叠）。"""
    one = _LINE_CONT.sub(" ", cmd)
    one = re.sub(r"\s+", " ", one)
    return one.strip()
