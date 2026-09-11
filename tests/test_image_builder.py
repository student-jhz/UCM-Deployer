# -*- coding: utf-8 -*-
import os

import pytest

from ucm_deployer.core.image_builder import (
    BuildCancelled,
    ImageBuildConfig,
    ImageBuilder,
    generate_dockerfile,
    suggest_tag,
    upload_image_tar,
)
from ucm_deployer.core.models import ServerInfo
from ucm_deployer.core.ssh_client import SSHClient


def make_ssh(fake_ssh):
    ssh = SSHClient(ServerInfo.create(name="s", host="127.0.0.1"))
    ssh.connect()
    return ssh


def make_whl(tmp_path, name="uc_manager-0.2.1-py3-none-any.whl"):
    p = tmp_path / name
    p.write_bytes(b"PK fake wheel")
    return str(p)


def test_suggest_tag():
    assert suggest_tag("quay.io/ascend/vllm-ascend:v0.23.0-a3") == "vllm-ascend-ucm:v0.23.0-a3"
    assert suggest_tag("vllm/vllm-openai") == "vllm-openai-ucm:latest"
    assert suggest_tag("") == "ucm-engine:latest"


def test_dockerfile_online():
    cfg = ImageBuildConfig(base_image="vllm/vllm-openai:latest",
                           image_tag="x-ucm:latest",
                           platform="nvidia")
    df = generate_dockerfile(cfg, "uc_manager-0.2.1-py3-none-any.whl")
    assert "FROM vllm/vllm-openai:latest" in df
    assert "ENV PLATFORM=nvidia" in df
    assert "wrapt" not in df  # 在线模式不单独装 wrapt
    assert "pip install --no-cache-dir /tmp/ucm-pkgs/uc_manager-0.2.1-py3-none-any.whl" in df


def test_dockerfile_offline_with_wrapt():
    cfg = ImageBuildConfig(base_image="quay.io/ascend/vllm-ascend:v0.23.0-a3",
                           image_tag="a-ucm:1", offline=True, platform="ascend")
    df = generate_dockerfile(cfg, "ucm.whl", "wrapt-1.16.0-cp39.whl")
    assert "ENV PLATFORM=ascend" in df
    assert "--no-index --find-links=/tmp/ucm-pkgs" in df
    assert "pip install --no-cache-dir --no-index --find-links=/tmp/ucm-pkgs /tmp/ucm-pkgs/wrapt-1.16.0-cp39.whl" in df


def test_dockerfile_with_toolkit():
    cfg = ImageBuildConfig(base_image="b:1", image_tag="t:1",
                           toolkit_dir="/tmp/x")
    df = generate_dockerfile(cfg, "ucm.whl", "", "ucm-toolkit-src.tar.gz")
    assert "COPY ucm-toolkit-src.tar.gz /tmp/ucm-pkgs/" in df
    assert "tar xzf /tmp/ucm-pkgs/ucm-toolkit-src.tar.gz" in df
    assert "pip install --no-cache-dir /tmp/ucm-toolkit-src" in df


def test_config_validate_missing_whl(tmp_path):
    cfg = ImageBuildConfig(base_image="b:1", image_tag="t:1", ucm_whl_local=str(tmp_path / "nope.whl"))
    with pytest.raises(Exception):
        cfg.validate()


def test_config_validate_bad_tag(tmp_path):
    whl = make_whl(tmp_path)
    cfg = ImageBuildConfig(base_image="b:1", image_tag="bad tag with spaces", ucm_whl_local=whl)
    with pytest.raises(Exception):
        cfg.validate()


def test_build_full_flow(fake_ssh, tmp_path):
    ssh = make_ssh(fake_ssh)
    whl = make_whl(tmp_path)
    wrapt = make_whl(tmp_path, "wrapt-1.16.0-cp39-cp39-linux_x86_64.whl")

    ssh._client.add(r"^mkdir -p .* && test -d", stdout="")
    ssh._client.add(r"^docker build -t ", stdout="Step 1/4\nSuccessfully built abc123\n")
    ssh._client.add(r"docker run --rm --entrypoint /bin/sh .+ -c .+pip show",
                    stdout="Name: uc-manager\nVersion: 0.2.1\nLocation: /usr/lib\n")
    ssh._client.add(r"^rm -rf ", stdout="")

    logs = []
    progress_events = []
    cfg = ImageBuildConfig(base_image="quay.io/ascend/vllm-ascend:v0.23.0-a3",
                           image_tag="vllm-ascend-ucm:v0.23.0-a3",
                           ucm_whl_local=whl, wrapt_whl_local=wrapt,
                           offline=True, platform="ascend")
    result = ImageBuilder(ssh).build(cfg, progress=lambda p, m: progress_events.append((p, m)),
                                     log=logs.append)
    assert result.ok
    assert result.image == "vllm-ascend-ucm:v0.23.0-a3"
    assert result.ucm.installed and result.ucm.version == "0.2.1"

    # 上传了两个 whl
    puts = [p for s in ssh._client.sftp_sessions for p in s.puts]
    assert any("uc_manager-0.2.1" in p[1] for p in puts)
    assert any("wrapt-1.16.0" in p[1] for p in puts)

    # Dockerfile 内容写到了远端
    dockerfile = ssh._client.files[
        [k for k in ssh._client.files if k.endswith("Dockerfile")][0]].decode()
    assert "FROM quay.io/ascend/vllm-ascend:v0.23.0-a3" in dockerfile

    # 进度单调不减且达到 100
    pcts = [p for p, _ in progress_events]
    assert pcts == sorted(pcts)
    assert pcts[-1] == 100

    # 日志包含 docker build 输出
    assert any("Successfully built" in l for l in logs)


def test_build_cancel(fake_ssh, tmp_path):
    ssh = make_ssh(fake_ssh)
    whl = make_whl(tmp_path)
    ssh._client.add(r"^mkdir -p .* && test -d", stdout="")
    ssh._client.add(r"^rm -rf ", stdout="")
    cfg = ImageBuildConfig(base_image="b:1", image_tag="t:1", ucm_whl_local=whl)
    with pytest.raises(BuildCancelled):
        ImageBuilder(ssh).build(cfg, cancelled=lambda: True)


def test_upload_image_tar(fake_ssh, tmp_path):
    ssh = make_ssh(fake_ssh)
    tar = tmp_path / "vllm-image.tar.gz"
    tar.write_bytes(b"tar-bytes")
    ssh._client.add(r"^docker load", stdout="Loaded image: vllm/vllm-openai:latest\n")
    refs = upload_image_tar(ssh, __import__("ucm_deployer.core.docker_manager", fromlist=["DockerManager"]).DockerManager(ssh), str(tar))
    assert refs == ["vllm/vllm-openai:latest"]
    puts = [p for s in ssh._client.sftp_sessions for p in s.puts]
    assert puts[-1][1] == "/tmp/vllm-image.tar.gz"
