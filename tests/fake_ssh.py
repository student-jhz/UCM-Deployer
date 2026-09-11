# -*- coding: utf-8 -*-
"""模拟 paramiko 的测试替身（FakeSSH 层）。

覆盖 SSHClient 用到的最小 paramiko 表面：
- SSHClient.connect / get_transport / open_sftp / close
- Transport.open_session / set_keepalive / is_active
- Channel.exec_command / recv / recv_stderr / exit_status...
- SFTPClient.put / open

通过脚本化响应（正则 -> 输出）模拟远端命令。
"""
from __future__ import annotations

import re
from typing import Dict, List, NamedTuple, Optional


class ScriptedResponse(NamedTuple):
    pattern: str      # 对完整命令做 re.search
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    hang: bool = False  # 模拟永不结束的命令（测超时）


class FakeChannel:
    def __init__(self, server: "FakeSSHClient"):
        self._server = server
        self._out = b""
        self._err = b""
        self._exit = 0
        self._hang = False
        self._combine = False
        self.executed_command: Optional[str] = None

    # ---- paramiko Channel API ----
    def set_combine_stderr(self, flag: bool) -> None:
        self._combine = flag

    def settimeout(self, timeout) -> None:
        pass

    def setblocking(self, flag: bool) -> None:
        pass

    def exec_command(self, command: str) -> None:
        self.executed_command = command
        resp = self._server.match(command)
        if resp is None:
            self._out, self._err, self._exit = b"", b"bash: command not found\n", 127
            return
        self._out = resp.stdout.encode("utf-8")
        self._err = resp.stderr.encode("utf-8")
        self._exit = resp.exit_code
        self._hang = resp.hang
        if self._combine:
            self._out = self._out + self._err
            self._err = b""

    def recv_ready(self) -> bool:
        return bool(self._out)

    def recv_stderr_ready(self) -> bool:
        return bool(self._err)

    def recv(self, n: int = 4096) -> bytes:
        data, self._out = self._out[:n], self._out[n:]
        return data

    def recv_stderr(self, n: int = 4096) -> bytes:
        data, self._err = self._err[:n], self._err[n:]
        return data

    def exit_status_ready(self) -> bool:
        if self._hang:
            return False
        return not self._out and not self._err

    def recv_exit_status(self) -> int:
        return self._exit

    def close(self) -> None:
        pass


class FakeTransport:
    def __init__(self, server: "FakeSSHClient"):
        self._server = server

    def set_keepalive(self, n: int) -> None:
        pass

    def is_active(self) -> bool:
        return self._server.connected

    def open_session(self, timeout=None) -> FakeChannel:
        chan = FakeChannel(self._server)
        self._server.channels.append(chan)
        return chan


class FakeSFTPFile:
    def __init__(self, store: Dict[str, bytes], path: str, mode: str):
        self._store = store
        self._path = path
        self._bin = "b" in mode
        self._read = "r" in mode
        if not self._read:
            self._store[path] = b""

    def write(self, data) -> int:
        if isinstance(data, str):
            data = data.encode("utf-8")
        self._store[self._path] += data
        return len(data)

    def read(self, n: int = -1) -> bytes:
        content = self._store.get(self._path, b"")
        if n < 0:
            return content
        return content[:n]

    def close(self) -> None:
        pass

    def __enter__(self) -> "FakeSFTPFile":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class FakeSFTP:
    def __init__(self, server: "FakeSSHClient"):
        self._server = server
        self.puts: List[tuple] = []

    def put(self, local_path: str, remote_path: str, callback=None) -> None:
        import os

        self.puts.append((local_path, remote_path))
        if callback is not None:
            size = os.path.getsize(local_path) if os.path.exists(local_path) else 4096
            for i in range(1, 11):
                callback(size * i // 10, size)

    def open(self, path: str, mode: str = "r") -> FakeSFTPFile:
        return FakeSFTPFile(self._server.files, path, mode)

    def close(self) -> None:
        pass


class FakeSSHClient:
    """paramiko.SSHClient 的替身。"""

    connect_exception: Optional[Exception] = None
    instances: List["FakeSSHClient"] = []

    def __init__(self):
        self.script: List[ScriptedResponse] = []
        self.connected = False
        self.channels: List[FakeChannel] = []
        self.sftp_sessions: List[FakeSFTP] = []
        self.connect_kwargs: Optional[Dict] = None
        self.files: Dict[str, bytes] = {}
        FakeSSHClient.instances.append(self)

    def set_missing_host_key_policy(self, policy) -> None:
        pass

    def connect(self, **kwargs) -> None:
        if FakeSSHClient.connect_exception is not None:
            raise FakeSSHClient.connect_exception
        self.connect_kwargs = kwargs
        self.connected = True

    def get_transport(self) -> FakeTransport:
        return FakeTransport(self)

    def open_sftp(self) -> FakeSFTP:
        sftp = FakeSFTP(self)
        self.sftp_sessions.append(sftp)
        return sftp

    def close(self) -> None:
        self.connected = False

    # ---- 脚本配置 ----
    def match(self, command: str) -> Optional[ScriptedResponse]:
        for resp in self.script:
            if re.search(resp.pattern, command):
                return resp
        return None

    def add(self, pattern: str, exit_code: int = 0, stdout: str = "",
            stderr: str = "", hang: bool = False) -> "FakeSSHClient":
        self.script.append(ScriptedResponse(pattern, exit_code, stdout, stderr, hang))
        return self
