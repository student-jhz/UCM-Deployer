# -*- coding: utf-8 -*-
import pytest
import paramiko

from .fake_ssh import FakeSSHClient


@pytest.fixture
def fake_ssh(monkeypatch):
    """把 paramiko.SSHClient 替换为 FakeSSHClient。"""
    monkeypatch.setattr(paramiko, "SSHClient", FakeSSHClient)
    FakeSSHClient.connect_exception = None
    FakeSSHClient.instances = []
    FakeSSHClient.default_scripts = []
    yield FakeSSHClient
    FakeSSHClient.connect_exception = None
    FakeSSHClient.default_scripts = []


@pytest.hookimpl(hookwrapper=True)
def pytest_runtestloop(session):
    """两个 Windows/PySide6 已知问题的规避（本环境实测稳定复现）：

    1. 主线程自动 GC 清理与工作线程交互的 Qt 对象 wrapper 时偶发
       access violation——测试期间冻结存量对象并禁用自动 GC；
    2. 测试全部跑完后的进程收尾阶段（连 os._exit 都会触发的
       DLL_PROCESS_DETACH）与残留 Qt 状态交互崩溃——测试循环一结束
       立即 TerminateProcess（不做任何清理，不执行 DLL 卸载钩子）。

    这会截断 pytest 的统计行输出（dots 已实时打印）；退出码与 pytest
    一致（失败=1），测试结果以退出码为准。
    """
    import ctypes
    import gc
    import os

    if os.environ.get("UCM_TEST_NO_EARLY_EXIT"):
        # 调试模式：不做提前退出，保留 pytest 完整报告（可能触发收尾崩溃）
        yield
        return

    gc.collect()
    gc.freeze()
    gc.disable()
    outcome = yield
    import time

    k32 = ctypes.windll.kernel32
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    # 伪句柄为 64 位 -1，必须显式声明 argtypes（默认 int 转换会溢出抛异常）
    k32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    code = 1 if session.testsfailed else 0
    k32.TerminateProcess(k32.GetCurrentProcess(), code)
    # TerminateProcess 是异步的：等待终止生效，绝不能落到正常退出路径
    # （os._exit 也会触发 DLL_PROCESS_DETACH，正是崩溃源）
    time.sleep(5)
