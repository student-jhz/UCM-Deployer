# -*- coding: utf-8 -*-
"""GUI 测试入口：以独立子进程运行 tests/test_gui_all.py 全集。

为什么进程隔离：
  实验证实——同一进程内加载 PySide6 之后，paramiko 在多线程并发下的
  原生调用会间歇性访问违例/堆损坏并使整个 pytest 进程崩溃或挂起
  （Anaconda Python 环境）。因此：
  - 父进程（pytest tests）：只运行核心/端到端测试，绝不 import PySide6
    （test_gui_all.py 在父进程中整模块跳过）
  - 子进程（本文件的 test_gui_suite）：加载 Qt 并运行全部 GUI 测试
    （GUI 测试内的 SSH 已替换为无网络的 LocalScriptSSH 替身）
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_gui_suite():
    """子进程运行 GUI 测试全集（Qt 与 paramiko 原生并发需进程隔离）。

    子进程若发生原生层崩溃（退出码非 0/1，如 0xC0000005 访问违例），
    为已知间歇问题（见模块 docstring），自动重试至多 3 次；
    退出码 1 表示真实测试失败，立即报错不重试。
    """
    root = Path(__file__).resolve().parents[1]
    target = root / "tests" / "test_gui_all.py"
    env = os.environ.copy()
    env["UCM_GUI_TEST_CHILD"] = "1"
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env["PYTHONIOENCODING"] = "utf-8"

    last_tail = ""
    for attempt in range(1, 4):
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", str(target), "-q", "--tb=short"],
                cwd=str(root), env=env, timeout=180,
                capture_output=True, text=True, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            # 挂起同样是已知间歇问题（原生层死锁），按崩溃处理重试
            last_tail = f"[第 {attempt} 次挂起, 超过 180s]"
            continue
        if proc.returncode == 0:
            return
        out = (proc.stdout or "") + (proc.stderr or "")
        tail = "\n".join(out.splitlines()[-12:])
        if proc.returncode == 1:
            pytest.fail(f"GUI 子进程测试失败(退出码 {proc.returncode}):\n{tail}")
        last_tail = (f"[第 {attempt} 次崩溃, 退出码 {proc.returncode}]\n{tail}")
    pytest.fail(
        "GUI 子进程连续 3 次原生崩溃（Qt+paramiko 并发已知间歇问题, "
        "见本文件 docstring）:\n" + last_tail)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
