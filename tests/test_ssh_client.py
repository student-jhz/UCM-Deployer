# -*- coding: utf-8 -*-
import pytest

from ucm_deployer.core.models import ServerInfo
from ucm_deployer.core.ssh_client import SSHClient, SSHError


def make_ssh(fake_ssh) -> SSHClient:
    ssh = SSHClient(ServerInfo.create(name="s", host="127.0.0.1"))
    ssh.connect()
    return ssh


def test_exec_returns_output(fake_ssh):
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"^docker info", stdout="Server Version: 27\n")
    res = ssh.exec("docker info")
    assert res.ok
    assert "27" in res.stdout


def test_exec_nonzero_exit(fake_ssh):
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"^bad", stderr="boom\n", exit_code=3)
    res = ssh.exec("bad command")
    assert not res.ok
    assert res.exit_code == 3
    assert "boom" in res.output


def test_exec_unmatched_command(fake_ssh):
    ssh = make_ssh(fake_ssh)
    res = ssh.exec("never-registered")
    assert res.exit_code == 127


def test_exec_combine_stderr(fake_ssh):
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"^cmd", stdout="out\n", stderr="err\n")
    res = ssh.exec("cmd", combine_stderr=True)
    assert "out" in res.stdout and "err" in res.stdout


def test_connect_failure_raises_ssherror(fake_ssh):
    fake_ssh.connect_exception = OSError("unreachable")
    ssh = SSHClient(ServerInfo.create(name="s", host="1.2.3.4", port=1))
    with pytest.raises(SSHError, match="连接"):
        ssh.connect()


def test_upload_and_read_file(fake_ssh, tmp_path):
    ssh = make_ssh(fake_ssh)
    local = tmp_path / "pkg.whl"
    local.write_bytes(b"PK wheel bytes")

    ssh.upload_file(str(local), "/tmp/pkg.whl")
    sftp = ssh._client.sftp_sessions[-1]
    assert sftp.puts == [(str(local), "/tmp/pkg.whl")]
    assert ssh._client.files["/tmp/pkg.whl"] == b"PK wheel bytes"

    assert ssh.file_size("/tmp/pkg.whl") == len(b"PK wheel bytes")


def test_write_and_read_text_file(fake_ssh):
    ssh = make_ssh(fake_ssh)
    ssh.write_file("/tmp/x.txt", "你好 UCM\n")
    assert ssh._client.files["/tmp/x.txt"] == "你好 UCM\n".encode("utf-8")
    assert ssh.read_file("/tmp/x.txt") == "你好 UCM\n"


def test_download_file(fake_ssh, tmp_path):
    ssh = make_ssh(fake_ssh)
    ssh._client.files["/remote/data.bin"] = b"\x00\x01\x02"
    local = tmp_path / "out.bin"
    ssh.download_file("/remote/data.bin", str(local))
    assert local.read_bytes() == b"\x00\x01\x02"


def test_mkdirs(fake_ssh):
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"^mkdir -p ")
    ssh.mkdirs("/tmp/build-dir")
    assert "mkdir -p /tmp/build-dir" in ssh._client.channels[-1].executed_command


def test_context_manager_closes(fake_ssh):
    with make_ssh(fake_ssh) as ssh:
        assert ssh.is_connected
        client = ssh._client
    assert client.closed
    assert not ssh.is_connected
    assert ssh._client is None
