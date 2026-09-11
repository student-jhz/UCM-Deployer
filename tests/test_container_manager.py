# -*- coding: utf-8 -*-
import pytest

from ucm_deployer.core.container_manager import (
    ContainerCreateConfig,
    ContainerManager,
    check_shared_fs,
)
from ucm_deployer.core.models import DeviceType, ServerInfo, VolumeMount
from ucm_deployer.core.shell import flatten_command
from ucm_deployer.core.ssh_client import SSHClient


def make_ssh(fake_ssh, name="s1"):
    ssh = SSHClient(ServerInfo.create(name=name, host="127.0.0.1"))
    ssh.connect()
    return ssh


def make_cm(fake_ssh):
    ssh = make_ssh(fake_ssh)
    return ContainerManager(ssh), ssh


def test_ascend_run_command(fake_ssh):
    cm, _ = make_cm(fake_ssh)
    cfg = ContainerCreateConfig(
        image="ucm-vllm:v0.1", name="ucm-node1",
        device_type=DeviceType.ASCEND, device_count=8,
        kv_cache_dirs=["/mnt/nfs_share"],
        model_mount=VolumeMount("/data/models/DeepSeek", "/models", readonly=True),
        extra_mounts=[VolumeMount("/home/user/logs", "/logs")],
        extra_args="--restart=unless-stopped",
    )
    cmd = cm.generate_run_command(cfg)
    assert cmd.startswith("docker run -itd")
    assert "--name ucm-node1" in cmd
    assert "--net=host" in cmd
    assert "--shm-size 512g" in cmd
    for i in range(8):
        assert f"--device /dev/davinci{i}" in cmd
    assert "--device /dev/davinci8" not in cmd
    assert "--device /dev/davinci_manager" in cmd
    assert "--device /dev/devmm_svm" in cmd
    assert "--device /dev/hisi_hdc" in cmd
    assert "-v /usr/local/dcmi:/usr/local/dcmi" in cmd
    assert "-v /usr/local/bin/npu-smi:/usr/local/bin/npu-smi" in cmd
    assert "-v /mnt/nfs_share:/mnt/nfs_share" in cmd
    assert "-v /data/models/DeepSeek:/models:ro" in cmd
    assert "-v /home/user/logs:/logs" in cmd
    assert "--restart=unless-stopped" in cmd
    assert cmd.rstrip().endswith("bash")
    # 压缩成单行后仍是合法命令
    flat = flatten_command(cmd)
    assert "\\" not in flat
    assert flat.startswith("docker run -itd --name ucm-node1")


def test_nvidia_run_command(fake_ssh):
    cm, _ = make_cm(fake_ssh)
    cfg = ContainerCreateConfig(
        image="vllm/vllm-openai:latest", name="ucm-gpu1",
        device_type=DeviceType.NVIDIA, device_count=8,
        kv_cache_dirs=["/mnt/cache"],
        model_mount=VolumeMount("/data/models", "/models", True),
    )
    cmd = cm.generate_run_command(cfg)
    assert "--gpus all" in cmd
    assert "--ipc=host" in cmd
    assert "davinci" not in cmd
    assert "npu-smi" not in cmd


def test_run_command_name_quoting(fake_ssh):
    cm, _ = make_cm(fake_ssh)
    cfg = ContainerCreateConfig(image="img:1", name="ok.name-1",
                                device_type=DeviceType.NVIDIA)
    cmd = cm.generate_run_command(cfg)
    assert "--name ok.name-1" in cmd


def test_run_command_invalid_name(fake_ssh):
    cm, _ = make_cm(fake_ssh)
    cfg = ContainerCreateConfig(image="img:1", name="bad name",
                                device_type=DeviceType.NVIDIA)
    with pytest.raises(Exception):
        cm.generate_run_command(cfg)


def test_run_command_relative_kv_dir_rejected(fake_ssh):
    cm, _ = make_cm(fake_ssh)
    cfg = ContainerCreateConfig(image="img:1", name="c1",
                                device_type=DeviceType.NVIDIA,
                                kv_cache_dirs=["relative/path"])
    with pytest.raises(Exception):
        cm.generate_run_command(cfg)


def test_create_executes_flattened(fake_ssh):
    cm, ssh = make_cm(fake_ssh)
    ssh._client.add(r"^docker run ", stdout="container-id-777\n")
    cfg = ContainerCreateConfig(image="img:1", name="c1",
                                device_type=DeviceType.NVIDIA, device_count=2)
    cmd = cm.generate_run_command(cfg)
    res = cm.create(cmd)
    assert res.ok
    executed = ssh._client.channels[-1].executed_command
    assert "\n" not in executed
    assert "\\" not in executed
    assert executed.startswith("docker run -itd --name c1")


def test_detect_ascend_device_count(fake_ssh):
    cm, ssh = make_cm(fake_ssh)
    ssh._client.add(r"ls -1 /dev/ .*grep -cE.*davinci", stdout="16\n")
    assert cm.detect_ascend_device_count() == 16


def test_list_host_dirs(fake_ssh):
    cm, ssh = make_cm(fake_ssh)
    ssh._client.add(r"find / -maxdepth 1", stdout="/bin\n/data\n/home\n/models\n/opt\n/root\n")
    dirs = cm.list_host_dirs("/")
    assert dirs == ["/bin", "/data", "/home", "/models", "/opt", "/root"]


def _add_df(ssh, path, source, fstype, ok=True):
    ssh._client.add(rf"df --output=source,fstype {re.escape(path)}",
                    stdout=f"Filesystem Type\n{source} {fstype}\n" if ok else "",
                    exit_code=0 if ok else 1)


import re  # noqa: E402


def test_shared_fs_single_server_local(fake_ssh):
    ssh1 = make_ssh(fake_ssh, "s1")
    _add_df(ssh1, "/mnt/cache1", "/dev/sda1", "ext4")
    _add_df(ssh1, "/mnt/cache2", "/dev/sda1", "ext4")
    report = check_shared_fs({"s1": ssh1}, ["/mnt/cache1", "/mnt/cache2"])
    assert report.ok
    assert report.warnings  # 本地磁盘给出提示


def test_shared_fs_single_server_mismatch(fake_ssh):
    ssh1 = make_ssh(fake_ssh, "s1")
    _add_df(ssh1, "/mnt/a", "/dev/sda1", "ext4")
    _add_df(ssh1, "/mnt/b", "/dev/sdb1", "xfs")
    report = check_shared_fs({"s1": ssh1}, ["/mnt/a", "/mnt/b"])
    assert not report.ok
    assert "不在同一文件系统" in report.message


def test_shared_fs_multi_server_nfs_ok(fake_ssh):
    ssh1 = make_ssh(fake_ssh, "s1")
    ssh2 = make_ssh(fake_ssh, "s2")
    for s in (ssh1, ssh2):
        _add_df(s, "/mnt/nfs", "nfsserver:/export", "nfs4")
    report = check_shared_fs({"s1": ssh1, "s2": ssh2}, ["/mnt/nfs"])
    assert report.ok
    assert "共享文件系统" in report.message


def test_shared_fs_multi_server_local_fails(fake_ssh):
    ssh1 = make_ssh(fake_ssh, "s1")
    ssh2 = make_ssh(fake_ssh, "s2")
    for s in (ssh1, ssh2):
        _add_df(s, "/mnt/cache", "/dev/sda1", "ext4")
    report = check_shared_fs({"s1": ssh1, "s2": ssh2}, ["/mnt/cache"])
    assert not report.ok
    assert "共享文件系统" in report.message


def test_shared_fs_dir_missing(fake_ssh):
    ssh1 = make_ssh(fake_ssh, "s1")
    _add_df(ssh1, "/nope", "", "", ok=False)
    report = check_shared_fs({"s1": ssh1}, ["/nope"])
    assert not report.ok


def test_shared_fs_cross_server_mismatch(fake_ssh):
    ssh1 = make_ssh(fake_ssh, "s1")
    ssh2 = make_ssh(fake_ssh, "s2")
    _add_df(ssh1, "/mnt/x", "serverA:/e1", "nfs4")
    _add_df(ssh2, "/mnt/x", "serverB:/e2", "nfs4")
    report = check_shared_fs({"s1": ssh1, "s2": ssh2}, ["/mnt/x"])
    assert not report.ok
    assert "不是同一存储源" in report.message
