# -*- coding: utf-8 -*-
import pytest
import paramiko

from ucm_deployer.core.models import ServerInfo
from ucm_deployer.core.ssh_client import SSHClient, SSHError


def make_server():
    return ServerInfo.create(name="s1", host="127.0.0.1", port=22,
                             username="root", password="pw")


def test_auto_connect_and_exec(fake_ssh):
    ssh = SSHClient(make_server())
    res = ssh.exec("echo hello")  # 未脚本化 -> 127
    assert not res.ok
    assert res.exit_code == 127
    # 连接参数校验
    assert ssh._client.connect_kwargs["username"] == "root"
    assert ssh._client.connect_kwargs["password"] == "pw"


def test_exec_scripted(fake_ssh):
    ssh = SSHClient(make_server())
    ssh.connect()
    ssh._client.add(r"^npu-smi info$", stdout="| 0  910B3  | OK |\n")
    res = ssh.exec("npu-smi info")
    assert res.ok and "910B3" in res.stdout


def test_exec_separates_stderr(fake_ssh):
    ssh = SSHClient(make_server())
    ssh.connect()
    ssh._client.add(r"^fail$", exit_code=2, stderr="boom\n")
    res = ssh.exec("fail")
    assert res.exit_code == 2 and res.stderr == "boom\n" and res.stdout == ""


def test_exec_timeout(fake_ssh):
    ssh = SSHClient(make_server())
    ssh.connect()
    ssh._client.add(r"^sleep$", hang=True)
    with pytest.raises(SSHError):
        ssh.exec("sleep", timeout=0.3)


def test_exec_stream_lines(fake_ssh):
    ssh = SSHClient(make_server())
    ssh.connect()
    ssh._client.add(r"^docker build", stdout="Step 1/3\nStep 2/3\nStep 3/3\n")
    lines = []
    res = ssh.exec_stream("docker build -t x .", on_line=lines.append)
    assert res.ok
    assert lines == ["Step 1/3", "Step 2/3", "Step 3/3"]
    assert "Step 3/3" in res.stdout


def test_connect_failure_raises_ssherror(fake_ssh):
    fake_ssh.connect_exception = paramiko.AuthenticationException("bad password")
    ssh = SSHClient(make_server())
    with pytest.raises(SSHError):
        ssh.connect()


def test_upload_file(fake_ssh, tmp_path):
    local = tmp_path / "ucm-0.2.0-py3-none-any.whl"
    local.write_bytes(b"fake-wheel")
    ssh = SSHClient(make_server())
    progress = []
    ssh.upload_file(str(local), "/tmp/ucm.whl",
                    progress_cb=lambda sent, total: progress.append((sent, total)))
    assert ssh._client.sftp_sessions[-1].puts == [(str(local), "/tmp/ucm.whl")]
    assert progress and progress[-1][0] == progress[-1][1] == len(b"fake-wheel")


def test_write_and_read_remote_file(fake_ssh):
    ssh = SSHClient(make_server())
    ssh.write_file("/tmp/x.txt", "hello\n中文")
    assert ssh.read_file("/tmp/x.txt") == "hello\n中文"


def test_mkdirs(fake_ssh):
    ssh = SSHClient(make_server())
    ssh.connect()
    ssh._client.add(r"^mkdir -p .* && test -d ", stdout="")
    ssh.mkdirs("/tmp/a/b")
    assert ssh._client.channels[-1].executed_command == "mkdir -p /tmp/a/b && test -d /tmp/a/b"


def test_context_manager(fake_ssh):
    ssh = SSHClient(make_server())
    with ssh:
        assert ssh.is_connected
    assert not ssh.is_connected
