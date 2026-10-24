# -*- coding: utf-8 -*-
import pytest

from ucm_deployer.core.container_manager import (
    ContainerCreateConfig,
    ContainerManager,
    check_shared_fs,
    evaluate_shared_fs,
)
from ucm_deployer.core.models import DeviceType, ServerInfo, VolumeMount
from ucm_deployer.core.shell import flatten_command
from ucm_deployer.core.ssh_client import SSHClient


def make_cm(fake_ssh):
    ssh = SSHClient(ServerInfo.create(name="s", host="127.0.0.1"))
    ssh.connect()
    return ContainerManager(ssh), ssh


# ============================================================ Ascend 模板
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
    assert "--device /dev/davinci8" not in cmd       # 不超过实际卡数
    assert "--device /dev/davinci_manager" in cmd
    assert "--device /dev/devmm_svm" in cmd
    assert "--device /dev/hisi_hdc" in cmd
    assert "-v /usr/local/dcmi:/usr/local/dcmi" in cmd
    assert "-v /usr/local/Ascend/driver:/usr/local/Ascend/driver" in cmd
    assert "-v /etc/ascend_install.info:/etc/ascend_install.info" in cmd
    assert "-v /mnt/nfs_share:/mnt/nfs_share" in cmd
    assert "-v /data/models/DeepSeek:/models:ro" in cmd
    assert "-v /home/user/logs:/logs" in cmd
    assert "--restart=unless-stopped" in cmd
    assert "--entrypoint /bin/bash" in cmd
    assert cmd.rstrip().endswith("ucm-vllm:v0.1")
    # 压缩成单行后仍是合法命令
    flat = flatten_command(cmd)
    assert "\\" not in flat
    assert flat.startswith("docker run -itd --name ucm-node1")


def test_ascend_950_run_command_uses_actual_device_nodes(fake_ssh):
    cm, _ = make_cm(fake_ssh)
    cfg = ContainerCreateConfig(
        image="vllm-ascend-ucm:v0.30.0rc2-a5", name="ucm090rc1-vllm-a5-jhz",
        device_type=DeviceType.ASCEND, device_count=1,
        device_nodes=[
            "/dev/davinci0", "/dev/davinci1", "/dev/davinci2",
            "/dev/davinci3", "/dev/davinci4", "/dev/davinci5",
            "/dev/davinci6", "/dev/davinci7", "/dev/davinci_manager",
            "/dev/hisi_hdc",
        ],
    )
    cmd = cm.generate_run_command(cfg)
    assert "--device /dev/davinci7" in cmd
    assert "--device /dev/davinci_manager" in cmd
    assert "--device /dev/hisi_hdc" in cmd
    assert "/dev/devmm_svm" not in cmd


def test_list_ascend_devices_skips_missing_950_node(fake_ssh):
    cm, ssh = make_cm(fake_ssh)
    ssh._client.add(
        r"^for d in /dev/davinci\*",
        stdout="\n".join([
            "/dev/davinci0", "/dev/davinci1", "/dev/davinci2",
            "/dev/davinci3", "/dev/davinci4", "/dev/davinci5",
            "/dev/davinci6", "/dev/davinci7", "/dev/davinci_manager",
            "/dev/hisi_hdc",
        ]) + "\n",
    )
    nodes = cm.list_ascend_devices()
    assert nodes.count("/dev/davinci_manager") == 1
    assert "/dev/devmm_svm" not in nodes


def test_list_ascend_mounts_keeps_existing_a5_paths(fake_ssh):
    cm, ssh = make_cm(fake_ssh)
    existing = [
        "/usr/local/Ascend/driver",
        "/etc/ascend_install.info",
        "/etc/hccl_rootinfo.json",
        "/etc/hixlep",
        "/root/.cache",
        "/usr/local/sbin",
        "/usr/local/dcmi",
        "/usr/local/bin/npu-smi",
        "/usr/lib64",
    ]
    ssh._client.add(
        r"^for p in",
        stdout="\n".join(existing) + "\n",
    )
    mounts = cm.list_ascend_mounts()
    assert ("/usr/local/Ascend/driver", "/usr/local/Ascend/driver") in mounts
    assert ("/usr/local/sbin", "/usr/local/sbin") in mounts
    assert all(host in existing for host, _ in mounts)


def test_ascend_a5_run_command_adds_privileged_and_mounts(fake_ssh):
    cm, _ = make_cm(fake_ssh)
    cfg = ContainerCreateConfig(
        image="vllm-ascend:v0.26.0rc1-a5-openeuler",
        name="ucm-a5",
        device_type=DeviceType.ASCEND,
        device_count=8,
        device_nodes=[
            *[f"/dev/davinci{i}" for i in range(8)],
            "/dev/davinci_manager",
            "/dev/hisi_hdc",
            "/dev/ummu",
            "/dev/uburma",
        ],
        system_mounts=[
            ("/usr/local/Ascend/driver", "/usr/local/Ascend/driver"),
            ("/usr/local/sbin", "/usr/local/sbin"),
        ],
        privileged=True,
    )
    cmd = cm.generate_run_command(cfg)
    assert "--privileged=true" in cmd
    assert "--device /dev/ummu" in cmd
    assert "--device /dev/uburma" in cmd
    assert "-v /usr/local/Ascend/driver:/usr/local/Ascend/driver" in cmd


# ============================================================ NVIDIA 模板
def test_nvidia_run_command(fake_ssh):
    cm, _ = make_cm(fake_ssh)
    cfg = ContainerCreateConfig(
        image="vllm/vllm-openai:latest", name="ucm-gpu1",
        device_type=DeviceType.NVIDIA, device_count=8,
        kv_cache_dirs=["/mnt/cache"],
        model_mount=VolumeMount("/data/models", "/models", True),
    )
    cmd = cm.generate_run_command(cfg)
    assert "--runtime nvidia" in cmd
    assert "--gpus all" in cmd
    assert "-p 8000:8000" in cmd
    assert "--ipc=host" in cmd
    assert "--net=host" not in cmd    # NVIDIA 模板用端口映射而非 host 网络
    assert "davinci" not in cmd
    assert "npu-smi" not in cmd


def test_nvidia_custom_ports(fake_ssh):
    cm, _ = make_cm(fake_ssh)
    cfg = ContainerCreateConfig(
        image="vllm/vllm-openai:latest", name="gpu2",
        device_type=DeviceType.NVIDIA,
        ports=["9000:8000", "9001:8001"],
    )
    cmd = cm.generate_run_command(cfg)
    assert "-p 9000:8000" in cmd
    assert "-p 9001:8001" in cmd


# ============================================================ 校验与异常
def test_run_command_name_quoting(fake_ssh):
    cm, _ = make_cm(fake_ssh)
    cmd = cm.generate_run_command(ContainerCreateConfig(
        image="img:1", name="ok.name-1", device_type=DeviceType.NVIDIA))
    assert "--name ok.name-1" in cmd


def test_run_command_invalid_name(fake_ssh):
    cm, _ = make_cm(fake_ssh)
    with pytest.raises(Exception):
        cm.generate_run_command(ContainerCreateConfig(
            image="img:1", name="bad name", device_type=DeviceType.NVIDIA))


def test_run_command_relative_kv_dir_rejected(fake_ssh):
    cm, _ = make_cm(fake_ssh)
    with pytest.raises(Exception):
        cm.generate_run_command(ContainerCreateConfig(
            image="img:1", name="c1", device_type=DeviceType.NVIDIA,
            kv_cache_dirs=["relative/path"]))


def test_unknown_device_generic(fake_ssh):
    cm, _ = make_cm(fake_ssh)
    cmd = cm.generate_run_command(ContainerCreateConfig(
        image="img:1", name="c1"))
    assert "--net=host" in cmd and "--shm-size 512g" in cmd


def test_create_executes_flattened(fake_ssh):
    cm, ssh = make_cm(fake_ssh)
    ssh._client.add(r"^docker run ", stdout="container-id-777\n")
    cmd = cm.generate_run_command(ContainerCreateConfig(
        image="img:1", name="c1", device_type=DeviceType.NVIDIA, device_count=2))
    res = cm.create(cmd)
    assert res.ok
    executed = ssh._client.channels[-1].executed_command
    assert "\n" not in executed and "\\" not in executed
    assert executed.startswith("docker run -itd --name c1")
    assert "--entrypoint /bin/bash" in executed
    assert not executed.endswith("bash")


def test_create_rejects_missing_shell_entrypoint(fake_ssh):
    cm, ssh = make_cm(fake_ssh)
    ssh._client.add(r"^docker run ", stdout="container-id-777\n")
    with pytest.raises(Exception, match="--entrypoint /bin/bash"):
        cm.create("docker run -itd --name c1 img:1")
    assert not ssh._client.channels


def test_create_rejects_trailing_bash_argument(fake_ssh):
    cm, ssh = make_cm(fake_ssh)
    ssh._client.add(r"^docker run ", stdout="container-id-777\n")
    with pytest.raises(Exception, match="不得在镜像后追加 bash"):
        cm.create(
            "docker run -itd --entrypoint /bin/bash --name c1 img:1 bash")
    assert not ssh._client.channels


def test_list_host_dirs(fake_ssh):
    cm, ssh = make_cm(fake_ssh)
    ssh._client.add(r"find / -maxdepth 1", stdout="/bin\n/data\n/home\n/models\n/opt\n/root\n")
    assert cm.list_host_dirs("/") == ["/bin", "/data", "/home", "/models", "/opt", "/root"]


# ============================================================ 文件系统校验
def _add_df(ssh, path, source, fstype, ok=True):
    import re

    ssh._client.add(rf"df --output=source,fstype {re.escape(path)}",
                    stdout=f"Filesystem Type\n{source} {fstype}\n" if ok else "",
                    exit_code=0 if ok else 1)


def test_shared_fs_local_warns(fake_ssh):
    _, ssh = make_cm(fake_ssh)
    _add_df(ssh, "/mnt/cache1", "/dev/sda1", "ext4")
    _add_df(ssh, "/mnt/cache2", "/dev/sda1", "ext4")
    report = check_shared_fs(ssh, ["/mnt/cache1", "/mnt/cache2"])
    assert report.ok
    assert report.warnings  # 本地磁盘给出提示


def test_shared_fs_nfs_ok(fake_ssh):
    _, ssh = make_cm(fake_ssh)
    _add_df(ssh, "/mnt/nfs", "nfsserver:/export", "nfs4")
    report = check_shared_fs(ssh, ["/mnt/nfs"])
    assert report.ok
    assert "网络文件系统" in report.message


def test_shared_fs_mismatch_fails(fake_ssh):
    _, ssh = make_cm(fake_ssh)
    _add_df(ssh, "/mnt/a", "/dev/sda1", "ext4")
    _add_df(ssh, "/mnt/b", "/dev/sdb1", "xfs")
    report = check_shared_fs(ssh, ["/mnt/a", "/mnt/b"])
    assert not report.ok
    assert "不在同一文件系统" in report.message


def test_shared_fs_dir_missing(fake_ssh):
    _, ssh = make_cm(fake_ssh)
    _add_df(ssh, "/nope", "", "", ok=False)
    report = check_shared_fs(ssh, ["/nope"])
    assert not report.ok


def test_evaluate_empty():
    assert not evaluate_shared_fs([]).ok
