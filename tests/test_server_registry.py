# -*- coding: utf-8 -*-
from ucm_deployer.core.models import DeviceInfo, DeviceType, ServerInfo
from ucm_deployer.core.server_registry import ServerRegistry


def test_upsert_list_remove(tmp_path):
    reg = ServerRegistry(tmp_path)
    s = ServerInfo.create(name="node1", host="10.0.0.1", password="secret-pw")
    reg.upsert(s)

    servers = reg.list_servers()
    assert len(servers) == 1
    assert servers[0].name == "node1"
    assert servers[0].password == "secret-pw"  # 读取时已解密

    # 明文密码不允许落盘
    raw = (tmp_path / "servers.json").read_text(encoding="utf-8")
    assert "secret-pw" not in raw
    assert "password_enc" in raw

    assert reg.remove(s.id)
    assert reg.list_servers() == []


def test_upsert_updates_existing(tmp_path):
    reg = ServerRegistry(tmp_path)
    s = ServerInfo.create(name="node1", host="10.0.0.1", password="pw1")
    reg.upsert(s)
    s.password = "pw2"
    s.name = "node1-renamed"
    reg.upsert(s)
    servers = reg.list_servers()
    assert len(servers) == 1
    assert servers[0].name == "node1-renamed"
    assert servers[0].password == "pw2"


def test_touch_updates_device(tmp_path):
    reg = ServerRegistry(tmp_path)
    s = ServerInfo.create(name="node1", host="10.0.0.1")
    reg.upsert(s)
    dev = DeviceInfo(DeviceType.ASCEND, "Ascend 910B3", 8)
    old_used = s.last_used_at
    reg.touch(s.id, device=dev)
    got = reg.get(s.id)
    assert got.device is not None
    assert got.device.count == 8
    assert got.last_used_at >= old_used


def test_get_missing_returns_none(tmp_path):
    reg = ServerRegistry(tmp_path)
    assert reg.get("nope") is None


def test_corrupt_file_recovers(tmp_path):
    (tmp_path / "servers.json").write_text("{not json", encoding="utf-8")
    reg = ServerRegistry(tmp_path)
    assert reg.list_servers() == []
