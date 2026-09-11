# -*- coding: utf-8 -*-
from ucm_deployer.core.models import (
    CommandResult,
    DeviceInfo,
    DeviceType,
    DockerImage,
    ServerInfo,
    UCMInfo,
    VolumeMount,
)


def test_server_info_roundtrip():
    s = ServerInfo.create(name="node1", host="10.0.0.1", port=2222,
                          username="root", password="pw", remark="测试机")
    s.device = DeviceInfo(DeviceType.ASCEND, "Ascend 910B3", 8)
    d = s.to_dict(include_secret=True)
    assert "password" in d
    s2 = ServerInfo.from_dict(d)
    assert s2.id == s.id
    assert s2.host == s.host and s2.port == 2222
    assert s2.device and s2.device.count == 8
    assert s2.device.device_type == DeviceType.ASCEND


def test_server_info_endpoint():
    s = ServerInfo.create(name="n", host="1.2.3.4", port=22)
    assert s.endpoint == "1.2.3.4:22"


def test_device_info_bad_enum():
    d = DeviceInfo.from_dict({"device_type": "tpu", "model": "x", "count": 4})
    assert d.device_type == DeviceType.UNKNOWN
    assert d.count == 4


def test_command_result():
    r = CommandResult("cmd", 0, "out", "")
    assert r.ok and r.output == "out"
    r2 = CommandResult("cmd", 1, "out", "err")
    assert not r2.ok and r2.output == "out\nerr"


def test_docker_image_ref():
    img = DockerImage("quay.io/ascend/vllm-ascend", "v0.23.0-a3", "abc123")
    assert img.ref == "quay.io/ascend/vllm-ascend:v0.23.0-a3"
    img2 = DockerImage("repo", "<none>", "abc123")
    assert img2.ref == "abc123"


def test_ucm_info_from_pip_show():
    text = "Name: uc-manager\nVersion: 0.2.1\nLocation: /usr/local/lib/python3.10/site-packages\n"
    info = UCMInfo.from_pip_show(text)
    assert info.installed
    assert info.version == "0.2.1"
    assert info.location.endswith("site-packages")

    empty = UCMInfo.from_pip_show("WARNING: Package(s) not found: ucm")
    assert not empty.installed


def test_volume_mount_arg():
    m = VolumeMount("/mnt/models", "/models", readonly=True)
    arg = m.to_docker_arg()
    assert arg == "-v /mnt/models:/models:ro"
    m2 = VolumeMount("/data/a b", "/data", False)
    assert "'/data/a b'" in m2.to_docker_arg()
