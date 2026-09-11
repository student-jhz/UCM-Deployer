# -*- coding: utf-8 -*-
import pytest

from ucm_deployer.core.docker_manager import DockerManager, DockerError
from ucm_deployer.core.models import ServerInfo
from ucm_deployer.core.ssh_client import SSHClient

IMAGES_OUT = """quay.io/ascend/vllm-ascend\tv0.23.0-a3\t7d3ac58e2f1c\t18.2GB\t2026-08-01 10:00:00 +0800 CST
vllm/vllm-openai\tlatest\taa2b3c4d5e6f\t14.1GB\t2026-07-20 20:00:00 +0800 CST
ucm-vllm\tv0.1\t9f8e7d6c5b4a\t18.3GB\t2026-08-12 09:00:00 +0800 CST
<none>\t<none>\tdeadbeef0000\t10.0GB\t2026-06-01 08:00:00 +0800 CST
"""

CONTAINERS_OUT = """1a2b3c4d5e6f\tucm-vllm-node1\tucm-vllm:v0.1\tUp 3 hours\trunning
abcdef123456\told-ctr\tvllm/vllm-openai:latest\tExited (0) 2 days ago\texited
"""


def make_dm(fake_ssh):
    ssh = SSHClient(ServerInfo.create(name="s", host="127.0.0.1"))
    ssh.connect()
    return DockerManager(ssh), ssh


def test_check_docker(fake_ssh):
    dm, ssh = make_dm(fake_ssh)
    ssh._client.add(r"^docker info", stdout="")
    assert dm.check_docker()
    ssh._client.add(r"^docker info", exit_code=1, stderr="Cannot connect to the Docker daemon")
    assert not dm.check_docker()


def test_list_images_parse(fake_ssh):
    dm, ssh = make_dm(fake_ssh)
    ssh._client.add(r"docker images --format", stdout=IMAGES_OUT)
    images = dm.list_images()
    assert len(images) == 4
    assert images[0].repository == "quay.io/ascend/vllm-ascend"
    assert images[0].ref == "quay.io/ascend/vllm-ascend:v0.23.0-a3"
    assert images[0].size == "18.2GB"
    assert images[3].repository == "<none>"


def test_image_exists(fake_ssh):
    dm, ssh = make_dm(fake_ssh)
    ssh._client.add(r"docker images --format", stdout=IMAGES_OUT)
    assert dm.image_exists("ucm-vllm:v0.1")
    assert dm.image_exists("9f8e7d6c5b4a")
    assert not dm.image_exists("no-such-image:tag")


def test_load_image(fake_ssh):
    dm, ssh = make_dm(fake_ssh)
    lines = []
    ssh._client.add(r"^docker load", stdout="Loaded image: ucm-vllm:v0.2\n")
    refs = dm.load_image("/tmp/img.tar.gz", on_line=lines.append)
    assert refs == ["ucm-vllm:v0.2"]
    assert lines == ["Loaded image: ucm-vllm:v0.2"]


def test_load_image_fail(fake_ssh):
    dm, ssh = make_dm(fake_ssh)
    ssh._client.add(r"^docker load", exit_code=1, stderr="Error processing tar file")
    with pytest.raises(DockerError):
        dm.load_image("/tmp/bad.tar")


def test_image_ucm_installed(fake_ssh):
    dm, ssh = make_dm(fake_ssh)
    pip_show = ("Name: uc-manager\nVersion: 0.2.1\n"
                "Location: /usr/local/lib/python3.10/site-packages\n")
    ssh._client.add(r"docker run --rm --entrypoint /bin/sh .+ -c .+pip show", stdout=pip_show)
    info = dm.image_ucm_info("ucm-vllm:v0.1")
    assert info.installed and info.version == "0.2.1"


def test_image_ucm_not_installed(fake_ssh):
    dm, ssh = make_dm(fake_ssh)
    ssh._client.add(r"docker run --rm --entrypoint /bin/sh .+ -c .+pip show",
                    exit_code=1, stdout="WARNING: Package(s) not found: uc-manager, ucm\n")
    info = dm.image_ucm_info("ucm-vllm:v0.1")
    assert not info.installed


def test_image_ucm_image_missing(fake_ssh):
    dm, ssh = make_dm(fake_ssh)
    ssh._client.add(r"docker run --rm --entrypoint /bin/sh .+ -c .+pip show",
                    exit_code=125, stderr="docker: Error response from daemon: Unable to find image")
    with pytest.raises(DockerError):
        dm.image_ucm_info("ghost:tag")


def test_list_containers(fake_ssh):
    dm, ssh = make_dm(fake_ssh)
    ssh._client.add(r"docker ps -a --format", stdout=CONTAINERS_OUT)
    cs = dm.list_containers()
    assert len(cs) == 2
    assert cs[0].names == "ucm-vllm-node1"
    assert cs[0].state == "running"
    assert cs[1].state == "exited"
    assert dm.get_container("ucm-vllm-node1").container_id == "1a2b3c4d5e6f"
    assert dm.get_container("1a2b3c").names == "ucm-vllm-node1"
    assert dm.get_container("nope") is None


def test_container_ucm_info(fake_ssh):
    dm, ssh = make_dm(fake_ssh)
    ssh._client.add(r"docker ps -a --format", stdout=CONTAINERS_OUT)
    pip_show = "Name: uc-manager\nVersion: 0.2.1\nLocation: /usr/lib\n"
    ssh._client.add(r"docker exec ucm-vllm-node1 /bin/sh -c .+pip show", stdout=pip_show)
    info = dm.container_ucm_info("ucm-vllm-node1")
    assert info.installed


def test_container_ucm_stopped_raises(fake_ssh):
    dm, ssh = make_dm(fake_ssh)
    ssh._client.add(r"docker ps -a --format", stdout=CONTAINERS_OUT)
    with pytest.raises(DockerError, match="未运行"):
        dm.container_ucm_info("old-ctr")


def test_run_rejects_non_docker_run(fake_ssh):
    dm, _ = make_dm(fake_ssh)
    with pytest.raises(DockerError):
        dm.run("rm -rf /")


def test_run_ok(fake_ssh):
    dm, ssh = make_dm(fake_ssh)
    lines = []
    ssh._client.add(r"^docker run ", stdout="container-id-123\n")
    res = dm.run("docker run -itd --name x some:image bash", on_line=lines.append)
    assert res.ok
    assert lines == ["container-id-123"]


def test_exec_in_container(fake_ssh):
    dm, ssh = make_dm(fake_ssh)
    ssh._client.add(r"docker exec -d ", stdout="")
    res = dm.exec_in_container("c1", "nohup bash /root/run.sh > /root/run.log 2>&1",
                               detach=True)
    assert res.ok
    chan_cmd = ssh._client.channels[-1].executed_command
    assert chan_cmd.startswith("docker exec -d c1 /bin/bash -c ")


def test_copy_to_container_quotes(fake_ssh):
    dm, ssh = make_dm(fake_ssh)
    ssh._client.add(r"^docker cp ", stdout="")
    dm.copy_to_container("/tmp/a b/run.sh", "my ctr", "/root/run.sh")
    cmd = ssh._client.channels[-1].executed_command
    assert "'/tmp/a b/run.sh'" in cmd
    assert "'my ctr:/root/run.sh'" in cmd


def test_build_fail_raises(fake_ssh):
    dm, ssh = make_dm(fake_ssh)
    ssh._client.add(r"^docker build", exit_code=1, stderr="The command '/bin/sh -c pip install' returned a non-zero code")
    with pytest.raises(DockerError):
        dm.build("/tmp/ctx", "t:1")
