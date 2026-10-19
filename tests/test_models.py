# -*- coding: utf-8 -*-
from ucm_deployer.core.models import (DeviceInfo, DeviceType, DockerImage,
                                      ServerInfo, UCMInfo, VolumeMount)


def test_device_info_roundtrip():
    d = DeviceInfo(DeviceType.ASCEND, "Ascend 910B3", 8, arch="aarch64")
    d2 = DeviceInfo.from_dict(d.to_dict())
    assert d2.device_type == DeviceType.ASCEND
    assert d2.model == "Ascend 910B3"
    assert d2.count == 8
    assert d2.arch == "aarch64"
    assert d2.display == "Ascend 910B3 x8"


def test_device_info_unknown():
    d = DeviceInfo.from_dict({"device_type": "bogus"})
    assert d.device_type == DeviceType.UNKNOWN
    assert d.display == "未检测"
    assert DeviceInfo.from_dict(None).count == 0


def test_server_info_roundtrip_without_secret():
    s = ServerInfo.create(name="n1", host="10.0.0.1", password="pw")
    d = s.to_dict()
    assert "password" not in d
    s2 = ServerInfo.from_dict(d)
    assert s2.password == ""
    assert s2.endpoint == "10.0.0.1:22"
    assert s2.name == "n1"


def test_server_info_secret_included_on_demand():
    s = ServerInfo.create(name="n", host="h", password="pw", passphrase="pp")
    d = s.to_dict(include_secret=True)
    assert d["password"] == "pw"
    assert d["passphrase"] == "pp"


def test_docker_image_ref():
    assert DockerImage("repo/img", "v1").ref == "repo/img:v1"
    assert DockerImage("repo/img", "<none>", image_id="abc").ref == "abc"


def test_ucm_info_from_pip_show():
    ucm = UCMInfo.from_pip_show("Name: uc-manager\nVersion: 0.2.1\nLocation: /usr/lib")
    assert ucm.installed
    assert ucm.version == "0.2.1"
    assert "0.2.1" in str(ucm)

    empty = UCMInfo.from_pip_show("")
    assert not empty.installed
    assert str(empty) == "未安装"


def test_volume_mount_docker_arg():
    m = VolumeMount("/data/models", "/models", readonly=True)
    assert m.to_docker_arg() == "-v /data/models:/models:ro"
    m2 = VolumeMount("/a b", "/c")
    assert m2.to_docker_arg() == "-v '/a b':/c"
