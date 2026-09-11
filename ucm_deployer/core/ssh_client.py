# -*- coding: utf-8 -*-
"""paramiko 的轻量封装。

设计目标：
- 单个远端服务器的连接、命令执行（阻塞/流式）、SFTP 上传下载；
- 无真实服务器时可用 tests/fake_ssh.py 或 ucm_deployer.mock 完整模拟。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, List, Optional

import paramiko

from ..utils.log import get_logger
from .models import CommandResult, ServerInfo

logger = get_logger(__name__)


class SSHError(Exception):
    """SSH 连接 / 执行失败。"""


class SSHClient:
    """绑定一台服务器的 SSH 客户端（线程不安全，每线程独立实例）。"""

    def __init__(self, server: ServerInfo, connect_timeout: float = 15.0):
        self.server = server
        self.connect_timeout = connect_timeout
        self._client: Optional[paramiko.SSHClient] = None

    # ------------------------------------------------------------ 生命周期
    def connect(self) -> None:
        if self.is_connected:
            return
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs = {
            "hostname": self.server.host,
            "port": self.server.port,
            "username": self.server.username,
            "timeout": self.connect_timeout,
            "allow_agent": False,
            "look_for_keys": False,
        }
        if self.server.auth_type == "key":
            kwargs["key_filename"] = self.server.key_path
            if self.server.passphrase:
                kwargs["passphrase"] = self.server.passphrase
        else:
            kwargs["password"] = self.server.password
        try:
            client.connect(**kwargs)
        except Exception as exc:  # paramiko 抛出的异常类型较多，统一转换
            client.close()
            raise SSHError(f"连接 {self.server.endpoint} 失败: {exc}") from exc
        try:
            transport = client.get_transport()
            if transport is not None:
                transport.set_keepalive(30)
        except Exception:  # pragma: no cover - keepalive 失败不影响使用
            pass
        self._client = client
        logger.info("已连接 %s", self.server.endpoint)

    @property
    def is_connected(self) -> bool:
        if self._client is None:
            return False
        transport = self._client.get_transport()
        return bool(transport and transport.is_active())

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            finally:
                self._client = None

    def __enter__(self) -> "SSHClient":
        self.connect()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # ------------------------------------------------------------ 命令执行
    def _ensure(self) -> paramiko.SSHClient:
        if not self.is_connected:
            self.connect()
        assert self._client is not None
        return self._client

    def _open_channel(self):
        client = self._ensure()
        transport = client.get_transport()
        if transport is None:
            raise SSHError("SSH 传输层不可用，请重新连接")
        return transport.open_session(timeout=self.connect_timeout)

    def exec(self, cmd: str, timeout: Optional[float] = None,
             combine_stderr: bool = False) -> CommandResult:
        """执行远端命令并等待结束（stdout/stderr 完整返回）。"""
        chan = self._open_channel()
        try:
            if combine_stderr:
                chan.set_combine_stderr(True)
            chan.exec_command(cmd)
            deadline = time.monotonic() + timeout if timeout else None
            out = bytearray()
            err = bytearray()
            while True:
                progressed = False
                while chan.recv_ready():
                    out += chan.recv(65536)
                    progressed = True
                while chan.recv_stderr_ready():
                    err += chan.recv_stderr(65536)
                    progressed = True
                if (chan.exit_status_ready() and not chan.recv_ready()
                        and not chan.recv_stderr_ready()):
                    break
                if deadline is not None and time.monotonic() > deadline:
                    raise SSHError(f"命令执行超时({timeout}s): {cmd[:200]}")
                if not progressed:
                    time.sleep(0.01)
            code = chan.recv_exit_status()
        finally:
            try:
                chan.close()
            except Exception:
                pass
        result = CommandResult(
            command=cmd,
            exit_code=code,
            stdout=out.decode("utf-8", errors="replace"),
            stderr=err.decode("utf-8", errors="replace"),
        )
        logger.debug("exec [%s] -> exit=%d", cmd[:120], code)
        return result

    def exec_stream(self, cmd: str,
                    on_line: Optional[Callable[[str], None]] = None,
                    timeout: Optional[float] = None) -> CommandResult:
        """执行远端命令，按行流式回调（stderr 合并进 stdout 流）。"""
        chan = self._open_channel()
        chunks: List[str] = []
        try:
            chan.set_combine_stderr(True)
            chan.exec_command(cmd)
            buf = bytearray()
            deadline = time.monotonic() + timeout if timeout else None

            def _emit(data: bytes) -> None:
                text = data.decode("utf-8", errors="replace")
                chunks.append(text)
                if on_line is not None:
                    try:
                        on_line(text.rstrip("\r\n"))
                    except Exception:  # 回调异常不中断命令
                        logger.exception("on_line 回调异常")

            while True:
                progressed = False
                while chan.recv_ready():
                    buf += chan.recv(65536)
                    progressed = True
                    while b"\n" in buf:
                        raw = bytes(buf)
                        line, _, rest = raw.partition(b"\n")
                        buf = bytearray(rest)
                        _emit(line + b"\n")
                if chan.exit_status_ready() and not chan.recv_ready():
                    break
                if deadline is not None and time.monotonic() > deadline:
                    raise SSHError(f"命令执行超时({timeout}s): {cmd[:200]}")
                if not progressed:
                    time.sleep(0.02)
            if buf:
                _emit(bytes(buf))
            code = chan.recv_exit_status()
        finally:
            try:
                chan.close()
            except Exception:
                pass
        return CommandResult(command=cmd, exit_code=code, stdout="".join(chunks))

    # ------------------------------------------------------------ 文件
    def upload_file(self, local_path: str, remote_path: str,
                    progress_cb: Optional[Callable[[int, int], None]] = None) -> None:
        client = self._ensure()
        sftp = None
        try:
            sftp = client.open_sftp()
            if progress_cb is None:
                sftp.put(local_path, remote_path)
            else:
                sftp.put(local_path, remote_path, callback=progress_cb)
        except SSHError:
            raise
        except Exception as exc:
            raise SSHError(f"上传文件失败 {local_path} -> {remote_path}: {exc}") from exc
        finally:
            if sftp is not None:
                try:
                    sftp.close()
                except Exception:
                    pass

    def write_file(self, remote_path: str, content: str) -> None:
        """通过 SFTP 写远端文本文件（utf-8）。"""
        client = self._ensure()
        sftp = None
        try:
            sftp = client.open_sftp()
            with sftp.open(remote_path, "wb") as fh:
                fh.write(content.encode("utf-8"))
        except SSHError:
            raise
        except Exception as exc:
            raise SSHError(f"写远端文件失败 {remote_path}: {exc}") from exc
        finally:
            if sftp is not None:
                try:
                    sftp.close()
                except Exception:
                    pass

    def read_file(self, remote_path: str) -> str:
        client = self._ensure()
        sftp = None
        try:
            sftp = client.open_sftp()
            with sftp.open(remote_path, "rb") as fh:
                return fh.read().decode("utf-8", errors="replace")
        except SSHError:
            raise
        except Exception as exc:
            raise SSHError(f"读远端文件失败 {remote_path}: {exc}") from exc
        finally:
            if sftp is not None:
                try:
                    sftp.close()
                except Exception:
                    pass

    def mkdirs(self, path: str) -> None:
        from .shell import shq

        res = self.exec(f"mkdir -p {shq(path)} && test -d {shq(path)}")
        if not res.ok:
            raise SSHError(f"创建远端目录失败 {path}: {res.stderr.strip() or res.stdout.strip()}")
