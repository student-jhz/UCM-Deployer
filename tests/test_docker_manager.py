# -*- coding: utf-8 -*-
import pytest

from ucm_deployer.core.docker_manager import DockerManager
from ucm_deployer.core.models import ServerInfo, UCMInfo
from ucm_deployer.core.ssh_client import SSHClient

_IMAGES_OUT = (
    "quay.io/ascend/vllm-ascend\tv0.23.0-a3\tid1\t18.2GB\t2026-08-01 10:00:00\n"
    "vllm/vllm-openai\tlatest\tid2\t9.9GB\t2026-07-01 09:00:00\n"
    "ucm-engine\tv0.1\tid3\t5GB\t2026-09-01 08:00:00\n"
)


def make_dm(fake_ssh) -> tuple:
    ssh = SSHClient(ServerInfo.create(name="s", host="127.0.0.1"))
    ssh.connect()
    return DockerManager(ssh), ssh


def test_check_docker(fake_ssh):
    dm, ssh = make_dm(fake_ssh)
    ssh._client.add(r"^docker info", stdout="")
    assert dm.check_docker()
    ssh._client.add(r"^docker info", exit_code=1)
    assert not dm.check_docker()


def test_list_images(fake_ssh):
    dm, ssh = make_dm(fake_ssh)
    ssh._client.add(r"images --format", stdout=_IMAGES_OUT)
    images = dm.list_images()
    assert len(images) == 3
    assert images[0].ref == "quay.io/ascend/vllm-ascend:v0.23.0-a3"
    assert images[0].size == "18.2GB"
    assert images[1].ref == "vllm/vllm-openai:latest"


def test_image_exists(fake_ssh):
    dm, ssh = make_dm(fake_ssh)
    ssh._client.add(r"docker image inspect img:1", stdout="")
    assert dm.image_exists("img:1")


def test_load_image_parses_refs(fake_ssh):
    dm, ssh = make_dm(fake_ssh)
    ssh._client.add(r"^docker load", stdout="Loaded image: my-engine:v1.0\n")
    refs = dm.load_image("/tmp/x.tar")
    assert refs == ["my-engine:v1.0"]


def test_build_streams_and_raises_on_error(fake_ssh):
    dm, ssh = make_dm(fake_ssh)
    lines = []
    ssh._client.add(r"^docker build", stdout="Step 1/3\nSuccessfully built abc\n")
    res = dm.build("/tmp/ctx", "new:1", on_line=lines.append)
    assert res.ok
    assert any("Successfully built" in l for l in lines)

    ssh._client.add(r"^docker build", stderr="ERROR: no FROM", exit_code=1)
    with pytest.raises(Exception, match="docker build 失败"):
        dm.build("/tmp/ctx", "new:2")


def test_image_ucm_info(fake_ssh):
    dm, ssh = make_dm(fake_ssh)
    ssh._client.add(r"^docker run --rm", stdout="Name: uc-manager\nVersion: 0.2.1\n")
    ucm = dm.image_ucm_info("img:1")
    assert isinstance(ucm, UCMInfo)
    assert ucm.installed and ucm.version == "0.2.1"


def test_container_ucm_info_requires_running(fake_ssh):
    dm, ssh = make_dm(fake_ssh)
    ssh._client.add(r"docker inspect -f", stdout="false\n")
    with pytest.raises(Exception, match="未运行"):
        dm.container_ucm_info("c1")


def test_container_ucm_info(fake_ssh):
    dm, ssh = make_dm(fake_ssh)
    ssh._client.add(r"docker inspect -f", stdout="true\n")
    ssh._client.add(r"^docker exec c1", stdout="Name: uc-manager\nVersion: 0.2.1\n")
    ucm = dm.container_ucm_info("c1")
    assert ucm.installed


def test_run(fake_ssh):
    dm, ssh = make_dm(fake_ssh)
    ssh._client.add(r"^docker run ", stdout="container-id-777\n")
    res = dm.run("docker run -itd --name x img:1 bash")
    assert res.ok
    assert "container-id-777" in res.stdout
