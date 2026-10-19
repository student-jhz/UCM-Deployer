# -*- coding: utf-8 -*-
from ucm_deployer.core.models import DeviceInfo, DeviceType, ServerInfo
from ucm_deployer.core.server_registry import ServerRegistry


def make_registry(tmp_path) -> ServerRegistry:
    from ucm_deployer.core import crypto
    from ucm_deployer.utils import paths

    paths.default_config_dir = lambda: tmp_path
    crypto._key_path = lambda: tmp_path / ".secret.key"
    return ServerRegistry(tmp_path)


def test_upsert_list_remove(tmp_path):
    reg = make_registry(tmp_path)
    s = ServerInfo.create(name="n1", host="10.0.0.1", password="pw")
    reg.upsert(s)

    servers = reg.list_servers()
    assert len(servers) == 1
    assert servers[0].host == "10.0.0.1"
    assert servers[0].password == "pw"      # 解密恢复
    assert reg.get(s.id).name == "n1"

    # 更新（同 id）
    s.name = "n1-renamed"
    reg.upsert(s)
    assert len(reg.list_servers()) == 1
    assert reg.list_servers()[0].name == "n1-renamed"

    reg.remove(s.id)
    assert reg.list_servers() == []


def test_password_encrypted_on_disk(tmp_path):
    reg = make_registry(tmp_path)
    reg.upsert(ServerInfo.create(name="n", host="h", password="secret-pw"))
    raw = (tmp_path / "servers.json").read_text(encoding="utf-8")
    assert "secret-pw" not in raw
    assert reg.list_servers()[0].password == "secret-pw"


def test_touch_updates_device(tmp_path):
    reg = make_registry(tmp_path)
    s = ServerInfo.create(name="n", host="h")
    reg.upsert(s)
    reg.touch(s.id, device=DeviceInfo(DeviceType.ASCEND, "Ascend 910B3", 8))
    got = reg.get(s.id)
    assert got.device is not None
    assert got.device.count == 8
    assert got.last_used_at >= s.last_used_at


def test_corrupted_file_recovers(tmp_path):
    reg = make_registry(tmp_path)
    (tmp_path / "servers.json").write_text("{ broken json", encoding="utf-8")
    assert reg.list_servers() == []
    reg.upsert(ServerInfo.create(name="n", host="h"))
    assert len(reg.list_servers()) == 1
