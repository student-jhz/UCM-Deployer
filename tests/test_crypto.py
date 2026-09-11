# -*- coding: utf-8 -*-
import pytest

from ucm_deployer.core.crypto import Crypto, CryptoError


def test_roundtrip(tmp_path):
    c = Crypto(tmp_path / "k")
    token = c.encrypt("s3cret-密码")
    assert token != "s3cret-密码"
    assert c.decrypt(token) == "s3cret-密码"


def test_same_key_file_decrypts(tmp_path):
    c1 = Crypto(tmp_path / "k")
    token = c1.encrypt("hello")
    c2 = Crypto(tmp_path / "k")
    assert c2.decrypt(token) == "hello"


def test_different_key_fails(tmp_path):
    c1 = Crypto(tmp_path / "k1")
    token = c1.encrypt("hello")
    c2 = Crypto(tmp_path / "k2")
    with pytest.raises(CryptoError):
        c2.decrypt(token)


def test_corrupt_token(tmp_path):
    c = Crypto(tmp_path / "k")
    with pytest.raises(CryptoError):
        c.decrypt("not-a-valid-token")
