# -*- coding: utf-8 -*-
"""端到端测试：真实 paramiko 客户端 <-> 本地模拟 SSH 服务器。

这是「无真实服务器环境」下最重要的自验证：完整走通
SSH/SFTP -> 设备探测 -> docker load/build -> docker run -> docker exec/cp
-> 部署脚本 -> 拉起 -> 健康检查 -> 日志 -> 停止。
"""
import os

import pytest

from ucm_deployer.core.command_generator import CommandGenerator
from ucm_deployer.core.container_manager import (ContainerCreateConfig,
                                                 ContainerManager,
                                                 check_shared_fs)
from ucm_deployer.core.device_detector import DeviceDetector
from ucm_deployer.core.docker_manager import DockerManager
from ucm_deployer.core.image_builder import (ImageBuildConfig, ImageBuilder,
                                             suggest_tag)
from ucm_deployer.core.models import (DeviceType, ServerInfo, VolumeMount)
from ucm_deployer.core.ssh_client import SSHClient
from ucm_deployer.core.topology import (DeployMode, DeployPlan, NodePlan,
                                        NodeRole, validate_plan)
from ucm_deployer.mock import mock_server as ms
from ucm_deployer.mock.selftest import run_full_flow, run_selftest
from ucm_deployer.service import deploy_service


@pytest.fixture
def ascend_server(tmp_path):
    server = ms.MockSSHServer(device="ascend", cards=8,
                              root_dir=str(tmp_path / "root"))
    server.start()
    yield server
    server.stop()


@pytest.fixture
def nvidia_server(tmp_path):
    server = ms.MockSSHServer(device="nvidia", cards=4,
                              root_dir=str(tmp_path / "root-gpu"))
    server.start()
    yield server
    server.stop()


def make_ssh(server) -> SSHClient:
    info = ServerInfo.create(name="mock", host="127.0.0.1", port=server.port,
                             username="root", password="root")
    return SSHClient(info)


# ------------------------------------------------------------ 基础通道
def test_mock_ssh_exec_and_sftp_roundtrip(ascend_server):
    with make_ssh(ascend_server) as ssh:
        res = ssh.exec("npu-smi info")
        assert res.ok and "910B3" in res.stdout
        ssh.write_file("/tmp/hello.txt", "你好 UCM\nroundtrip")
        assert ssh.read_file("/tmp/hello.txt") == "你好 UCM\nroundtrip"


def test_mock_upload_binary(ascend_server, tmp_path):
    local = tmp_path / "pkg.whl"
    local.write_bytes(bytes(range(256)) * 64)
    with make_ssh(ascend_server) as ssh:
        progress = []
        ssh.upload_file(str(local), "/tmp/pkg.whl",
                        progress_cb=lambda s, t: progress.append((s, t)))
        assert progress and progress[-1][0] == progress[-1][1]
        assert os.path.isfile(ascend_server.linux._real("/tmp/pkg.whl"))
        assert os.path.getsize(ascend_server.linux._real("/tmp/pkg.whl")) == local.stat().st_size


# ------------------------------------------------------------ 步骤1: 设备探测
def test_mock_detect_ascend(ascend_server):
    with make_ssh(ascend_server) as ssh:
        info = DeviceDetector(ssh).detect()
    assert info.device_type == DeviceType.ASCEND
    assert info.model == "Ascend 910B3"
    assert info.count == 8


def test_mock_detect_nvidia(nvidia_server):
    with make_ssh(nvidia_server) as ssh:
        info = DeviceDetector(ssh).detect()
    assert info.device_type == DeviceType.NVIDIA
    assert info.count == 4


# ------------------------------------------------------------ 步骤2: 镜像
def test_mock_docker_images_and_load(ascend_server, tmp_path):
    with make_ssh(ascend_server) as ssh:
        dm = DockerManager(ssh)
        assert dm.check_docker()
        assert any(i.ref == "quay.io/ascend/vllm-ascend:v0.23.0-a3"
                   for i in dm.list_images())
        tar = tmp_path / "my-engine__v1.0.tar.gz"
        tar.write_bytes(b"fake-tar")
        from ucm_deployer.core.image_builder import upload_image_tar
        refs = upload_image_tar(ssh, dm, str(tar))
        assert refs == ["my-engine:v1.0"]
        assert dm.image_exists("my-engine:v1.0")


def test_mock_build_image_with_ucm(ascend_server, tmp_path):
    whl = tmp_path / "uc_manager-0.2.1-py3-none-any.whl"
    whl.write_bytes(b"PK fake")
    wrapt = tmp_path / "wrapt-1.16.0-cp39-cp39-manylinux2014_x86_64.whl"
    wrapt.write_bytes(b"PK wrapt")
    with make_ssh(ascend_server) as ssh:
        dm = DockerManager(ssh)
        base = "quay.io/ascend/vllm-ascend:v0.23.0-a3"
        cfg = ImageBuildConfig(base_image=base, image_tag=suggest_tag(base),
                               ucm_whl_local=str(whl), wrapt_whl_local=str(wrapt),
                               offline=True, platform="ascend")
        logs = []
        result = ImageBuilder(ssh, dm).build(cfg, log=logs.append)
        assert result.ok
        assert result.image == "vllm-ascend-ucm:v0.23.0-a3"
        assert result.ucm.installed and result.ucm.version == "0.2.1"
        assert any("Successfully tagged" in l for l in logs)
        # 二次校验镜像 UCM
        ucm = dm.image_ucm_info(result.image)
        assert ucm.installed and ucm.version == "0.2.1"


def test_mock_build_missing_base_fails(ascend_server, tmp_path):
    whl = tmp_path / "uc_manager-0.2.1-py3-none-any.whl"
    whl.write_bytes(b"PK")
    with make_ssh(ascend_server) as ssh:
        cfg = ImageBuildConfig(base_image="no-such/image:9.9",
                               image_tag="x:1", ucm_whl_local=str(whl),
                               offline=True)
        with pytest.raises(Exception):
            ImageBuilder(ssh).build(cfg)


# ------------------------------------------------------------ 镜像分发
def test_mock_distribute_image(tmp_path):
    """构建一次、分发到第二台：export -> import -> 目标服务器有镜像且保留 UCM。"""
    from ucm_deployer.core.image_builder import export_image_to_local, import_image

    a = ms.MockSSHServer(device="ascend", cards=8, root_dir=str(tmp_path / "root-a"))
    b = ms.MockSSHServer(device="ascend", cards=8, root_dir=str(tmp_path / "root-b"))
    a.start()
    b.start()
    try:
        whl = tmp_path / "uc_manager-0.2.1-py3-none-any.whl"
        whl.write_bytes(b"PK")
        with make_ssh(a) as ssh_a, make_ssh(b) as ssh_b:
            dm_a, dm_b = DockerManager(ssh_a), DockerManager(ssh_b)
            ImageBuilder(ssh_a, dm_a).build(ImageBuildConfig(
                base_image="quay.io/ascend/vllm-ascend:v0.23.0-a3",
                image_tag="ucm-img:d1", ucm_whl_local=str(whl), offline=True))
            assert not dm_b.image_exists("ucm-img:d1")

            # 导出不存在的镜像 -> 明确报错原因
            with pytest.raises(Exception, match="不存在"):
                export_image_to_local(ssh_a, dm_a, "no-such:img")

            local_tar, size = export_image_to_local(ssh_a, dm_a, "ucm-img:d1")
            assert size > 0 and os.path.isfile(local_tar)
            try:
                status = import_image(ssh_b, dm_b, local_tar, "ucm-img:d1")
                assert "已加载" in status
                assert dm_b.image_exists("ucm-img:d1")
                ucm = dm_b.image_ucm_info("ucm-img:d1")
                assert ucm.installed and ucm.version == "0.2.1"
                # 重复导入 -> 跳过
                assert "跳过" in import_image(ssh_b, dm_b, local_tar, "ucm-img:d1")
            finally:
                os.unlink(local_tar)
    finally:
        a.stop()
        b.stop()


# ------------------------------------------------------------ 步骤3: 容器
def test_mock_create_container_and_check_ucm(ascend_server, tmp_path):
    whl = tmp_path / "uc_manager-0.2.1-py3-none-any.whl"
    whl.write_bytes(b"PK")
    with make_ssh(ascend_server) as ssh:
        dm = DockerManager(ssh)
        base = "quay.io/ascend/vllm-ascend:v0.23.0-a3"
        cfg = ImageBuildConfig(base_image=base, image_tag="ucm-img:t1",
                               ucm_whl_local=str(whl), offline=True)
        ImageBuilder(ssh, dm).build(cfg)

        cm = ContainerManager(ssh, dm)
        ccfg = ContainerCreateConfig(
            image="ucm-img:t1", name="ucm-ctr", device_type=DeviceType.ASCEND,
            device_count=8, kv_cache_dirs=["/mnt/nfs_share"],
            model_mount=VolumeMount("/models/Qwen3-32B", "/models", True))
        cmd = cm.generate_run_command(ccfg)
        res = cm.create(cmd)
        assert res.ok
        containers = dm.list_containers()
        assert any(c.names == "ucm-ctr" and c.state == "running" for c in containers)

        ucm = dm.container_ucm_info("ucm-ctr")
        assert ucm.installed

        # 宿主机目录浏览
        dirs = cm.list_host_dirs("/")
        assert "/models" in dirs and "/mnt" in dirs

        # 共享文件系统校验（单机 /mnt -> nfs）
        report = check_shared_fs({"mock": ssh}, ["/mnt/nfs_share"])
        assert report.ok and "网络文件系统" in report.message


def test_mock_container_stopped_ucm_raises(ascend_server):
    with make_ssh(ascend_server) as ssh:
        dm = DockerManager(ssh)
        ssh.exec("docker run -itd --name c1 quay.io/ascend/vllm-ascend:v0.23.0-a3 bash")
        ssh.exec("docker stop c1")
        with pytest.raises(Exception, match="未运行"):
            dm.container_ucm_info("c1")


# ------------------------------------------------------------ 步骤4/5: 部署与拉起
def test_mock_deploy_launch_stop(ascend_server, tmp_path):
    whl = tmp_path / "uc_manager-0.2.1-py3-none-any.whl"
    whl.write_bytes(b"PK")
    with make_ssh(ascend_server) as ssh:
        dm = DockerManager(ssh)
        ImageBuilder(ssh, dm).build(ImageBuildConfig(
            base_image="quay.io/ascend/vllm-ascend:v0.23.0-a3",
            image_tag="ucm-img:t1", ucm_whl_local=str(whl), offline=True))
        ssh.exec("docker run -itd --name ucm-ctr ucm-img:t1 bash")

        info = ascend_server.linux
        plan = DeployPlan(
            mode=DeployMode.COLOCATED, engine="vllm", device_type=DeviceType.ASCEND,
            model_path="/models/Qwen3-32B", served_model_name="qwen3",
            kv_cache_dir="/mnt/nfs_share", extra_args="--max-model-len 17000",
            nodes=[NodePlan(server_id="mock", server_name="mock", host="127.0.0.1",
                            role=NodeRole.MIXED, dp=1, tp=8,
                            container="ucm-ctr", cards=8)])
        assert not [i for i in validate_plan(plan) if i.is_error]

        ds = CommandGenerator(plan).generate()
        deploy_service.deploy_files(ssh, dm, "ucm-ctr", ds.scripts + ds.config_files)

        # 脚本真实落位
        assert os.path.isfile(info._real("/root/ucm-deploy/serve.sh"))
        assert os.path.isfile(info._real("/root/ucm-deploy/ucm_config.yaml"))

        for s in deploy_service.ordered_scripts(ds):
            deploy_service.launch_script(ssh, dm, "ucm-ctr", s)

        ok, msg = deploy_service.health_check(ssh, "http://127.0.0.1:9000/health")
        assert ok, msg

        log = deploy_service.tail_log(ssh, dm, "ucm-ctr", "/root/ucm-deploy/logs/serve.log")
        assert "Application startup complete" in log
        assert deploy_service.script_running(ssh, dm, "ucm-ctr", ds.scripts[0])

        for s in ds.scripts:
            deploy_service.stop_script(ssh, dm, "ucm-ctr", s)
        ok2, _ = deploy_service.health_check(ssh, "http://127.0.0.1:9000/health")
        assert not ok2


def test_mock_pd_two_servers(tmp_path):
    """PD 分离：两台模拟服务器（1P+1D）端到端。"""
    p_server = ms.MockSSHServer(device="ascend", cards=8,
                                root_dir=str(tmp_path / "root-p"))
    d_server = ms.MockSSHServer(device="ascend", cards=8,
                                root_dir=str(tmp_path / "root-d"))
    p_server.start()
    d_server.start()
    try:
        conns = {}
        for label, srv in (("p1", p_server), ("d1", d_server)):
            info = ServerInfo.create(name=label, host="127.0.0.1", port=srv.port,
                                     username="root", password="root")
            conns[label] = SSHClient(info)
            conns[label].connect()

        # 两台都构建镜像 + 起容器
        whl = tmp_path / "uc_manager-0.2.1-py3-none-any.whl"
        whl.write_bytes(b"PK")
        for label, ssh in conns.items():
            dm = DockerManager(ssh)
            ImageBuilder(ssh, dm).build(ImageBuildConfig(
                base_image="quay.io/ascend/vllm-ascend:v0.23.0-a3",
                image_tag="ucm-img:t1", ucm_whl_local=str(whl), offline=True))
            ssh.exec("docker run -itd --name ucm-ctr ucm-img:t1 bash")

        plan = DeployPlan(
            mode=DeployMode.PD, engine="vllm", device_type=DeviceType.ASCEND,
            model_path="/models/Qwen3-32B", kv_cache_dir="/mnt/nfs_share",
            nodes=[
                NodePlan(server_id="p1", server_name="p1", host="127.0.0.1",
                         role=NodeRole.P, dp=1, tp=8, container="ucm-ctr", cards=8),
                NodePlan(server_id="d1", server_name="d1", host="127.0.0.1",
                         role=NodeRole.D, dp=1, tp=8, container="ucm-ctr", cards=8),
            ])
        assert not [i for i in validate_plan(plan) if i.is_error]
        ds = CommandGenerator(plan).generate()

        by_server = deploy_service.scripts_by_server(ds)
        cfg_by_server = deploy_service.config_files_by_server(ds)
        for sid, ssh in conns.items():
            dm = DockerManager(ssh)
            deploy_service.deploy_files(ssh, dm, "ucm-ctr",
                                        by_server.get(sid, []) + cfg_by_server.get(sid, []))
            for s in by_server.get(sid, []):
                deploy_service.launch_script(ssh, dm, "ucm-ctr", s)

        # 两台服务器上的健康检查全部通过
        for hc in ds.health_checks:
            ssh = conns[hc.server_id]
            ok, msg = deploy_service.health_check(ssh, hc.url)
            assert ok, f"{hc.label} @ {hc.server_id}: {msg}"
    finally:
        p_server.stop()
        d_server.stop()
        for ssh in conns.values():
            ssh.close()


# ------------------------------------------------------------ 自检入口
def test_run_selftest_ascend():
    ok, report = run_selftest(device="ascend", cards=8)
    assert ok, "\n".join(report)


def test_run_selftest_nvidia():
    ok, report = run_selftest(device="nvidia", cards=4)
    assert ok, "\n".join(report)


def test_run_full_flow_against_external_server():
    """占位：对真实服务器跑全流程的示例（CI 中跳过）。"""
    host = os.environ.get("UCM_TEST_REAL_HOST")
    if not host:
        pytest.skip("未设置 UCM_TEST_REAL_HOST，跳过真实服务器测试")
    report = []
    ok = run_full_flow(host, int(os.environ.get("UCM_TEST_REAL_PORT", "22")),
                       os.environ.get("UCM_TEST_REAL_USER", "root"),
                       os.environ.get("UCM_TEST_REAL_PASSWORD", ""), report,
                       device=os.environ.get("UCM_TEST_REAL_DEVICE", "ascend"))
    assert ok, "\n".join(report)
