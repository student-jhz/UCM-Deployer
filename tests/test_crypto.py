# -*- coding: utf-8 -*-
import ucm_deployer.core.crypto as crypto
from ucm_deployer.core.crypto import decrypt, encrypt


def test_encrypt_decrypt_roundtrip(tmp_path, monkeypatch):
    from ucm_deployer.utils import paths
    monkeypatch.setattr(paths, "default_config_dir", lambda: tmp_path)
    monkeypatch.setattr(crypto, "_key_path", lambda: tmp_path / ".secret.key")

    token = encrypt("密码123")
    assert token != "密码123"
    assert decrypt(token) == "密码123"


def test_key_reused(tmp_path, monkeypatch):
    from ucm_deployer.utils import paths
    monkeypatch.setattr(paths, "default_config_dir", lambda: tmp_path)
    monkeypatch.setattr(crypto, "_key_path", lambda: tmp_path / ".secret.key")

    t1 = encrypt("a")
    t2 = encrypt("a")
    assert decrypt(t1) == decrypt(t2) == "a"
    # 密钥文件已生成且不再变化
    assert (tmp_path / ".secret.key").is_file()


def test_empty_plaintext(tmp_path, monkeypatch):
    from ucm_deployer.utils import paths
    monkeypatch.setattr(paths, "default_config_dir", lambda: tmp_path)
    monkeypatch.setattr(crypto, "_key_path", lambda: tmp_path / ".secret.key")
    assert encrypt("") == ""
    assert decrypt("") == ""


def test_decrypt_invalid_token_returns_empty(tmp_path, monkeypatch):
    from ucm_deployer.utils import paths
    monkeypatch.setattr(paths, "default_config_dir", lambda: tmp_path)
    monkeypatch.setattr(crypto, "_key_path", lambda: tmp_path / ".secret.key")
    assert decrypt("not-a-valid-token") == ""
