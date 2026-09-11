# -*- coding: utf-8 -*-
import pytest
import paramiko

from .fake_ssh import FakeSSHClient


@pytest.fixture
def fake_ssh(monkeypatch):
    """把 paramiko.SSHClient 替换为 FakeSSHClient。"""
    monkeypatch.setattr(paramiko, "SSHClient", FakeSSHClient)
    FakeSSHClient.connect_exception = None
    FakeSSHClient.instances = []
    yield FakeSSHClient
    FakeSSHClient.connect_exception = None
