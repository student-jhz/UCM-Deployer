# -*- coding: utf-8 -*-
"""UCM Deployer CLI 入口。

用法：
  python cli.py mock-server [--port 2222] [--device ascend] [--cards 8]
  python cli.py self-test [--device ascend] [--cards 8]
  python cli.py check   --host H [--port 22] [--username root] [--password PW]
  python cli.py build   --host H --base-image REF --ucm-whl PATH
                        [--wrapt-whl PATH] [--offline] [--tag TAG]
"""
from __future__ import annotations

import argparse
import getpass
import sys

from ucm_deployer import __version__
from ucm_deployer.utils.log import get_logger, setup_logging

logger = get_logger(__name__)


def _add_server_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", required=True, help="服务器 IP")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--username", default="root")
    parser.add_argument("--password", help="登录密码（不填则交互输入）")
    parser.add_argument("--key", help="私钥文件路径")


def _make_server_info(args):
    from ucm_deployer.core.models import ServerInfo

    password = args.password
    if not password and not args.key:
        password = getpass.getpass(f"{args.host} 的 SSH 密码: ")
    return ServerInfo.create(name=args.host, host=args.host, port=args.port,
                             username=args.username, password=password or "",
                             auth_type="key" if args.key else "password",
                             key_path=args.key or "")


def cmd_mock_server(args) -> int:
    from ucm_deployer.mock import mock_server

    sys.argv = ["mock-server"] + _remaining_args(args)
    mock_server.main()
    return 0


def _remaining_args(args) -> list:
    return [a for a in sys.argv[2:]]


def cmd_self_test(args) -> int:
    from ucm_deployer.mock.selftest import run_selftest

    ok, report = run_selftest(device=args.device, cards=args.cards,
                              progress=lambda m: print(f"  -> {m}"))
    print()
    for line in report:
        print(line)
    return 0 if ok else 1


def cmd_check(args) -> int:
    from ucm_deployer.core.device_detector import DeviceDetector
    from ucm_deployer.core.docker_manager import DockerManager
    from ucm_deployer.core.ssh_client import SSHClient

    info = _make_server_info(args)
    with SSHClient(info) as ssh:
        device = DeviceDetector(ssh).detect()
        print(f"设备: {device.display} ({device.device_type.value})")
        dm = DockerManager(ssh)
        print(f"Docker: {'可用' if dm.check_docker() else '不可用'}"
              + (f" ({dm.docker_version()})" if dm.check_docker() else ""))
        if dm.check_docker():
            print(f"镜像数: {len(dm.list_images())}")
    return 0


def cmd_build(args) -> int:
    from ucm_deployer.core.docker_manager import DockerManager
    from ucm_deployer.core.image_builder import (ImageBuildConfig, ImageBuilder,
                                                 suggest_tag)
    from ucm_deployer.core.ssh_client import SSHClient

    info = _make_server_info(args)
    tag = args.tag or suggest_tag(args.base_image)
    cfg = ImageBuildConfig(base_image=args.base_image, image_tag=tag,
                           ucm_whl_local=args.ucm_whl,
                           wrapt_whl_local=args.wrapt_whl or "",
                           offline=args.offline,
                           platform="ascend" if args.platform == "ascend" else "cuda")
    with SSHClient(info) as ssh:
        result = ImageBuilder(ssh, DockerManager(ssh)).build(
            cfg,
            progress=lambda p, m: print(f"\r[{p:3d}%] {m}", end="", flush=True),
            log=lambda line: print(f"\n  | {line}", end=""))
        print()
        if result.ok:
            print(f"构建成功: {result.image} (UCM {result.ucm.version})")
            return 0
        print(f"构建失败: {result.error}")
        return 1


def main(argv=None) -> int:
    setup_logging()
    parser = argparse.ArgumentParser(
        prog="ucm-deployer", description=f"UCM Deployer v{__version__} 命令行工具")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("mock-server", help="启动模拟 SSH 服务器（GUI 演示用）")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=2222)
    p.add_argument("--device", choices=["ascend", "nvidia"], default="ascend")
    p.add_argument("--cards", type=int, default=8)
    p.add_argument("--npu-model", default="910B3")
    p.add_argument("--username", default="root")
    p.add_argument("--password", default="root")
    p.add_argument("--preload-ucm-image", action="store_true")

    p = sub.add_parser("self-test", help="端到端自检（自动启动模拟服务器）")
    p.add_argument("--device", choices=["ascend", "nvidia"], default="ascend")
    p.add_argument("--cards", type=int, default=8)

    p = sub.add_parser("check", help="检查服务器设备与 docker")
    _add_server_args(p)

    p = sub.add_parser("build", help="在服务器上构建带 UCM 的镜像")
    _add_server_args(p)
    p.add_argument("--base-image", required=True, help="基础镜像 (repo:tag)")
    p.add_argument("--ucm-whl", required=True, help="本地 UCM whl 路径")
    p.add_argument("--wrapt-whl", default="", help="本地 wrapt whl 路径（离线）")
    p.add_argument("--offline", action="store_true", help="服务器无外网")
    p.add_argument("--tag", default="", help="新镜像名（默认自动生成）")
    p.add_argument("--platform", choices=["ascend", "cuda"], default="ascend")

    args = parser.parse_args(argv)
    if args.command == "mock-server":
        return cmd_mock_server(args)
    if args.command == "self-test":
        return cmd_self_test(args)
    if args.command == "check":
        return cmd_check(args)
    if args.command == "build":
        return cmd_build(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
