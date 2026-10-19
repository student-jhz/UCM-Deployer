# -*- coding: utf-8 -*-
"""FakeSSH：paramiko.SSHClient 的脚本化替身（单元测试用，无真实网络）。

用法：
    fake_ssh.add(r"^docker info", stdout="ok\\n")     # 按正则返回预设输出
    ssh = SSHClient(server); ssh.connect()            # 经 fixture 替换后走 Fake
    ssh._client.channels[-1].executed_command         # 检查实际执行的命令
"""
from __future__ import annotations

import re
import time
from typing import Dict, List, Optional, Tuple


class FakeChannel:
    def __init__(self, session: "FakeSSHSession"):
        self._session = session
        self.executed_command = ""
        self._combine = False
        self._out = b""
        self._err = b""
        self._exit_code = 0
        self._closed = False

    def set_combine_stderr(self, flag: bool) -> None:
        # paramiko 协议在 exec_command 之前调用；实际合并在 exec 填充时进行
        self._combine = flag

    def exec_command(self, cmd: str) -> None:
        self.executed_command = cmd
        matched = self._session.match(cmd)
        if matched is None:
            self._out, self._err = b"", b"bash: command not found\n"
            self._exit_code = 127
        else:
            stdout, stderr, code = matched
            self._out = stdout.encode("utf-8")
            self._err = stderr.encode("utf-8")
            self._exit_code = code
        if self._combine:
            self._out += self._err
            self._err = b""

    # ---------------------------------------------------------- stream API
    def recv_ready(self) -> bool:
        return bool(self._out)

    def recv_stderr_ready(self) -> bool:
        return bool(self._err)

    def recv(self, n: int) -> bytes:
        data, self._out = self._out[:n], self._out[n:]
        return data

    def recv_stderr(self, n: int) -> bytes:
        data, self._err = self._err[:n], self._err[n:]
        return data

    def exit_status_ready(self) -> bool:
        return not self._out and not self._err

    def recv_exit_status(self) -> int:
        return self._exit_code

    def close(self) -> None:
        self._closed = True


class FakeTransport:
    def __init__(self, client: "FakeSSHClient"):
        self._client = client

    def set_keepalive(self, seconds: int) -> None:
        pass

    def is_active(self) -> bool:
        return self._client.connected

    def open_session(self, timeout: float = None) -> FakeChannel:
        session = self._client.current_session
        chan = FakeChannel(session)
        session.channels.append(chan)
        return chan

    def join(self, timeout: float = None) -> None:  # pragma: no cover
        pass


class FakeSFTP:
    """内存文件系统 + put/get 记录。"""

    def __init__(self, client: "FakeSSHClient"):
        self._client = client
        self.puts: List[Tuple[str, str]] = []
        self.gets: List[Tuple[str, str]] = []

    def put(self, local: str, remote: str, callback=None) -> None:
        import os

        self.puts.append((local, remote))
        with open(local, "rb") as fh:
            self._client.files[remote] = fh.read()
        if callback is not None:
            size = os.path.getsize(local)
            callback(size, size)

    def get(self, remote: str, local: str, callback=None) -> None:
        self.gets.append((remote, local))
        data = self._client.files.get(remote, b"")
        with open(local, "wb") as fh:
            fh.write(data)
        if callback is not None:
            callback(len(data), len(data))

    def stat(self, remote: str):
        class _St:
            st_size = len(self._client.files.get(remote, b""))
        return _St()

    def open(self, remote: str, mode: str = "rb"):
        import io

        data = self._client.files.get(remote, b"")

        class _File(io.BytesIO):
            def close(self) -> None:
                if "w" in mode or "a" in mode or "+" in mode:
                    self._client.files[remote] = self.getvalue()
                super().close()

        f = _File(data if "r" in mode else b"")
        f._client = self._client
        return f

    def close(self) -> None:
        pass


class FakeSSHSession:
    """一次 SSH 连接内的脚本化命令匹配。"""

    def __init__(self, client: "FakeSSHClient"):
        self.client = client
        self.channels: List[FakeChannel] = []

    def match(self, cmd: str) -> Optional[Tuple[str, str, int]]:
        # 后注册的脚本优先（同一命令多次 add 时，最新一次生效）
        for pattern, stdout, stderr, code in reversed(self.client.scripts):
            if re.search(pattern, cmd):
                return stdout, stderr, code
        return None


class FakeSSHClient:
    """paramiko.SSHClient 替身。脚本通过 add() 注册（全局，按注册顺序匹配）。"""

    connect_exception: Optional[Exception] = None
    instances: List["FakeSSHClient"] = []

    def __init__(self):
        self.connected = False
        self.closed = False
        self.files: Dict[str, bytes] = {}
        self.scripts: List[Tuple[str, str, str, int]] = []
        self.sftp_sessions: List[FakeSFTP] = []
        self.current_session = FakeSSHSession(self)
        FakeSSHClient.instances.append(self)

    @property
    def channels(self) -> List[FakeChannel]:
        return self.current_session.channels

    # ---------------------------------------------------------- 脚本注册
    def add(self, pattern: str, stdout: str = "", stderr: str = "",
            exit_code: int = 0) -> None:
        self.scripts.append((pattern, stdout, stderr, exit_code))

    # ---------------------------------------------------------- SSHClient API
    def set_missing_host_key_policy(self, policy) -> None:
        pass

    def connect(self, **kwargs) -> None:
        if FakeSSHClient.connect_exception is not None:
            exc = FakeSSHClient.connect_exception
            FakeSSHClient.connect_exception = None
            raise exc
        self.connected = True
        self.current_session = FakeSSHSession(self)

    def close(self) -> None:
        self.connected = False
        self.closed = True

    def get_transport(self) -> Optional[FakeTransport]:
        if not self.connected:
            return None
        return FakeTransport(self)

    def open_sftp(self) -> FakeSFTP:
        sftp = FakeSFTP(self)
        self.sftp_sessions.append(sftp)
        return sftp


class FakeParamikoModule:
    """paramiko 模块替身（含 AutoAddPolicy 等符号）。"""

    SSHClient = FakeSSHClient

    class AutoAddPolicy:
        pass

    class RSAKey:  # pragma: no cover
        @staticmethod
        def generate(bits: int):
            raise NotImplementedError
