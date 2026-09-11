# -*- coding: utf-8 -*-
"""基于 paramiko 的本地模拟 SSH 服务器（含 SFTP 子系统）。

用途：
- 无真实服务器环境下的端到端自验证（pytest / `cli.py self-test`）
- GUI 演示：`python -m ucm_deployer.mock.mock_server` 后在界面添加 127.0.0.1 即可
"""
from __future__ import annotations

import os
import socket
import stat
import threading
import time
from pathlib import Path
from typing import List, Optional

import paramiko
from paramiko.sftp import SFTP_OK
from paramiko.sftp_handle import SFTPHandle
from paramiko.sftp_server import SFTPServer, SFTPServerInterface, SFTPAttributes

from ..utils.log import get_logger
from .mock_linux import MockLinux

logger = get_logger(__name__)


# ============================================================ SFTP
class MockSFTPInterface(SFTPServerInterface):
    """把远端路径映射到沙箱目录的 SFTP 实现。"""

    def __init__(self, server):
        super().__init__(server)
        self._linux: MockLinux = server.linux
        self._root = Path(self._linux.root)

    def _real(self, path: str) -> str:
        return self._linux._real(path)

    def _attr(self, real_path: str) -> SFTPAttributes:
        st = os.stat(real_path)
        attr = SFTPAttributes()
        attr.st_size = st.st_size
        attr.st_uid = st.st_uid
        attr.st_gid = st.st_gid
        attr.st_mode = st.st_mode
        attr.st_atime = st.st_atime
        attr.st_mtime = st.st_mtime
        return attr

    def session_started(self) -> None:
        pass

    def list_folder(self, path):
        real = self._real(path)
        try:
            out = []
            for name in os.listdir(real):
                child = os.path.join(real, name)
                attr = self._attr(child)
                attr.filename = name
                out.append(attr)
            return out
        except OSError as e:
            return SFTPServer.convert_errno(e.errno)

    def stat(self, path):
        try:
            return self._attr(self._real(path))
        except OSError as e:
            return SFTPServer.convert_errno(e.errno)

    def lstat(self, path):
        return self.stat(path)

    def open(self, path, flags, attr):
        real = self._real(path)
        try:
            pflags = flags | getattr(os, "O_BINARY", 0)
            mode = getattr(attr, "st_mode", None) or 0o666
            fd = os.open(real, pflags, mode)
        except OSError as e:
            return SFTPServer.convert_errno(e.errno)
        # 注意: paramiko 传入的 flags 已是 os.* 标志(经 _convert_pflags)
        accmode = flags & (os.O_RDONLY | os.O_WRONLY | os.O_RDWR)
        if accmode == os.O_RDONLY:
            strflag = "rb"
        elif accmode == os.O_WRONLY:
            strflag = "ab" if (flags & os.O_APPEND) else "wb"
        else:
            strflag = "a+b" if (flags & os.O_APPEND) else "r+b"
        try:
            f = os.fdopen(fd, strflag)
        except OSError as e:
            return SFTPServer.convert_errno(e.errno)
        handle = SFTPHandle(flags)
        handle.readfile = f
        handle.writefile = f
        handle.filename = real
        return handle

    def remove(self, path):
        try:
            os.remove(self._real(path))
            return SFTP_OK
        except OSError as e:
            return SFTPServer.convert_errno(e.errno)

    def rename(self, oldpath, newpath):
        try:
            os.replace(self._real(oldpath), self._real(newpath))
            return SFTP_OK
        except OSError as e:
            return SFTPServer.convert_errno(e.errno)

    def mkdir(self, path, attr):
        try:
            os.mkdir(self._real(path))
            return SFTP_OK
        except OSError as e:
            return SFTPServer.convert_errno(e.errno)

    def rmdir(self, path):
        try:
            os.rmdir(self._real(path))
            return SFTP_OK
        except OSError as e:
            return SFTPServer.convert_errno(e.errno)

    def chattr(self, path, attr):
        return SFTP_OK

    def canonicalize(self, path):
        return path if path.startswith("/") else "/" + path


# ============================================================ SSH
class MockSSHServerInterface(paramiko.ServerInterface):
    def __init__(self, owner: "MockSSHServer"):
        self.owner = owner

    # ---- 供 SFTP 接口访问
    @property
    def linux(self) -> MockLinux:
        return self.owner.linux

    def get_allowed_auths(self, username):
        return "password"

    def check_auth_password(self, username, password):
        if (username == self.owner.username and password == self.owner.password):
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_channel_request(self, kind, chanid):
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self, channel, command):
        threading.Thread(target=self._exec_worker,
                         args=(channel, command), daemon=True).start()
        return True

    def _exec_worker(self, channel, command) -> None:
        code, out, err = 0, "", ""
        try:
            text = command.decode("utf-8", errors="replace") \
                if isinstance(command, bytes) else str(command)
            code, out, err = self.owner.linux.execute(text)
        except Exception as exc:
            code, err = 1, f"mock-exec-error: {exc}"
        try:
            if out:
                channel.sendall(out.encode("utf-8"))
            if err:
                channel.sendall_stderr(err.encode("utf-8"))
            channel.send_exit_status(code)
        except Exception:
            logger.debug("发送 exec 结果失败（通道可能已关闭）", exc_info=True)
        # 注意：不主动 close/发送 EOF —— 若 EOF/CLOSE 抢在 exec 请求应答之前
        # 到达客户端，paramiko 客户端的 _wait_for_event 会抛 "Channel closed"。
        # 由客户端在读取完退出码后关闭通道即可。


class MockSSHServer:
    """在 127.0.0.1 上启动一个模拟 Ascend/NVIDIA + Docker 的 SSH 服务器。"""

    def __init__(self, port: int = 0, device: str = "ascend", cards: int = 8,
                 username: str = "root", password: str = "root",
                 root_dir: Optional[str] = None,
                 preload_ucm_image: bool = False,
                 npu_model: str = "910B3"):
        import tempfile

        if root_dir is None:
            root_dir = tempfile.mkdtemp(prefix="ucm-mock-root-")
        self.linux = MockLinux(root_dir, device=device, cards=cards,
                               preload_ucm_image=preload_ucm_image,
                               npu_model=npu_model)
        self.username = username
        self.password = password
        self._port = int(port)
        self._host_key = paramiko.RSAKey.generate(2048)
        self._sock: Optional[socket.socket] = None
        self._transports: List[paramiko.Transport] = []
        self._accept_thread: Optional[threading.Thread] = None
        self._stopping = threading.Event()

    # ------------------------------------------------------------ 生命周期
    def start(self, host: str = "127.0.0.1", timeout: float = 10.0) -> int:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, self._port))
        sock.listen(8)
        sock.settimeout(0.5)
        self._sock = sock
        self._port = sock.getsockname()[1]
        self._stopping.clear()
        self._accept_thread = threading.Thread(target=self._accept_loop,
                                               daemon=True)
        self._accept_thread.start()
        logger.info("模拟 SSH 服务器已启动: %s:%s (%s x%d)",
                    host, self._port, self.linux.device, self.linux.cards)
        return self._port

    def stop(self) -> None:
        self._stopping.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        for t in self._transports:
            try:
                t.close()
            except Exception:
                pass
        self._transports.clear()

    @property
    def port(self) -> int:
        return self._port

    # ------------------------------------------------------------ 内部
    def _accept_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                conn, _addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                transport = paramiko.Transport(conn)
                transport.add_server_key(self._host_key)
                transport.set_subsystem_handler(
                    "sftp", SFTPServer, MockSFTPInterface)
                transport.start_server(server=MockSSHServerInterface(self))
                self._transports.append(transport)
            except Exception:
                logger.exception("接受 SSH 连接失败")
                try:
                    conn.close()
                except OSError:
                    pass
        logger.info("模拟 SSH 服务器已停止")


def main() -> None:
    """命令行入口：python -m ucm_deployer.mock.mock_server [--port 2222]"""
    import argparse

    from ..utils.log import setup_logging

    setup_logging()
    parser = argparse.ArgumentParser(description="UCM Deployer 模拟服务器（演示/自验证用）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2222)
    parser.add_argument("--device", choices=["ascend", "nvidia"], default="ascend")
    parser.add_argument("--cards", type=int, default=8)
    parser.add_argument("--npu-model", default="910B3")
    parser.add_argument("--username", default="root")
    parser.add_argument("--password", default="root")
    parser.add_argument("--preload-ucm-image", action="store_true",
                        help="预置一个已含 UCM 的镜像(可跳过镜像构建步骤)")
    args = parser.parse_args()

    server = MockSSHServer(port=args.port, device=args.device, cards=args.cards,
                           username=args.username, password=args.password,
                           preload_ucm_image=args.preload_ucm_image,
                           npu_model=args.npu_model)
    port = server.start(host=args.host)
    print("=" * 60)
    print("UCM Deployer 模拟服务器已启动")
    print(f"  地址     : {args.host}:{port}")
    print(f"  登录     : {args.username} / {args.password}")
    print(f"  设备     : {args.device} x{args.cards}"
          + (f" ({args.npu_model})" if args.device == "ascend" else ""))
    print(f"  文件沙箱 : {server.linux.root}")
    print()
    print("在 UCM Deployer 中添加服务器即可体验完整流程；Ctrl+C 退出。")
    print("=" * 60)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n正在停止...")
        server.stop()


if __name__ == "__main__":
    main()
