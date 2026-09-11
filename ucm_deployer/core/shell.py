# -*- coding: utf-8 -*-
"""shell 工具：安全的远程命令引号处理。"""
from __future__ import annotations

import re

_SAFE = re.compile(r"^[A-Za-z0-9_@%+=:,./-]+$")


def shq(text: str) -> str:
    """单引号包裹，用于在远端 bash 中安全引用一个词。

    与 shlex.quote 行为一致（POSIX shell）。
    """
    text = str(text)
    if not text:
        return "''"
    if _SAFE.match(text):
        return text
    return "'" + text.replace("'", "'\"'\"'") + "'"
