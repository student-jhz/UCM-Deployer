# -*- coding: utf-8 -*-
import pytest

from ucm_deployer.core.image_builder import (
    BuildCancelled,
    ImageBuildConfig,
    ImageBuilder,
    ImageRuntimeInfo,
    fetch_image_runtime_info,
    generate_dockerfile,
    image_platform,
    suggest_tag,
    upload_image_tar,
    validate_build_match,
    whl_arch,
    whl_version,
)
from ucm_deployer.core.models import DeviceInfo, DeviceType, ServerInfo
from ucm_deployer.core.ssh_client import SSHClient


def make_ssh(fake_ssh):
    ssh = SSHClient(ServerInfo.create(name="s", host="127.0.0.1"))
    ssh.connect()
    return ssh


def make_whl(tmp_path, name="uc_manager-0.2.1-py3-none-any.whl"):
    p = tmp_path / name
    p.write_bytes(b"PK fake wheel")
    return str(p)


# ============================================================ 纯函数
def test_suggest_tag():
    # 基础镜像名:ucm-<UCM版本号>
    assert suggest_tag("quay.io/ascend/vllm-ascend:v0.23.0-a3",
                       "0.8.0.dev20261015") == \
        "quay.io/ascend/vllm-ascend:ucm-0.8.0.dev20261015"
    assert suggest_tag("vllm/vllm-openai:latest", "0.2.1") == \
        "vllm/vllm-openai:ucm-0.2.1"
    # 无 UCM 版本时
    assert suggest_tag("vllm-ascend:v0.23.0-a3") == "vllm-ascend:ucm"
    # 无 tag 的基础镜像
    assert suggest_tag("vllm/vllm-openai", "1.0") == "vllm/vllm-openai:ucm-1.0"
    assert suggest_tag("") == "ucm-engine:ucm"


def test_whl_version():
    assert whl_version("uc_manager-0.2.1-py3-none-any.whl") == "0.2.1"
    assert whl_version("uc_manager-0.8.0.dev20261015-py3-none-any.whl") == \
        "0.8.0.dev20261015"
    assert whl_version("wrapt-1.17.2-cp39-cp39-manylinux2014_x86_64.whl") == "1.17.2"
    assert whl_version("not-a-whl.txt") == ""


def test_image_platform():
    assert image_platform("quay.io/ascend/vllm-ascend:v0.23.0") == "ascend"
    assert image_platform("xxx/cann-vllm:1") == "ascend"
    assert image_platform("vllm/vllm-openai:cuda12") == "nvidia"
    assert image_platform("nvidia/cuda:12.4") == "nvidia"
    assert image_platform("my-custom-engine:1") == ""


def test_whl_arch():
    assert whl_arch("wrapt-1.17.2-cp39-cp39-manylinux2014_x86_64.whl") == "x86_64"
    assert whl_arch("wrapt-1.17.2-cp39-cp39-manylinux2014_aarch64.whl") == "aarch64"
    assert whl_arch("wrapt-1.17.2-cp39-cp39-linux_arm64.whl") == "aarch64"
    assert whl_arch("uc_manager-0.2.1-py3-none-any.whl") == ""
    assert whl_arch("not-a-whl.txt") == ""


# ============================================================ 匹配校验
def test_match_ok():
    dev = DeviceInfo(DeviceType.ASCEND, "Ascend 910B3", 8, arch="aarch64")
    r = validate_build_match(
        dev, "aarch64", "quay.io/ascend/vllm-ascend:v0.23.0-a2",
        "/tmp/uc_manager-0.2.1-py3-none-any.whl",
        "/tmp/wrapt-1.17.2-cp39-cp39-manylinux2014_aarch64.whl")
    assert r.ok
    assert "校验通过" in r.message


def test_match_platform_mismatch():
    dev = DeviceInfo(DeviceType.NVIDIA, "NVIDIA A800", 8)
    r = validate_build_match(
        dev, "x86_64", "quay.io/ascend/vllm-ascend:v0.23.0-a3",
        "/tmp/uc_manager-0.2.1-py3-none-any.whl", "")
    assert not r.ok
    assert any("不匹配" in e for e in r.errors)


def test_match_whl_arch_mismatch():
    dev = DeviceInfo(DeviceType.ASCEND, "Ascend 910B3", 8)
    r = validate_build_match(
        dev, "aarch64", "quay.io/ascend/vllm-ascend:v0.23.0-a3",
        "/tmp/uc_manager-0.2.1-py3-none-any.whl",
        "/tmp/wrapt-1.17.2-cp39-cp39-manylinux2014_x86_64.whl")
    assert not r.ok
    assert any("架构不匹配" in e for e in r.errors)


def test_match_no_device():
    r = validate_build_match(DeviceInfo(), "x86_64", "vllm/vllm-openai:latest",
                             "/tmp/uc.whl", "")
    assert not r.ok
    assert any("未探测到" in e for e in r.errors)


def test_match_unknown_image_warns():
    dev = DeviceInfo(DeviceType.ASCEND, "Ascend 910B3", 8)
    r = validate_build_match(dev, "aarch64", "my-custom-engine:1",
                             "/tmp/uc_manager-0.2.1-py3-none-any.whl", "")
    assert r.ok
    assert any("无法从镜像名" in w for w in r.warnings)


def test_match_no_arch_skips_whl_check():
    dev = DeviceInfo(DeviceType.NVIDIA, "NVIDIA A800", 8)
    r = validate_build_match(dev, "", "vllm/vllm-openai:latest",
                             "/tmp/uc_manager-0.2.1-py3-none-any.whl",
                             "/tmp/wrapt-1.0-py3-none-any.whl")
    assert r.ok  # 无架构信息时跳过 whl 校验（仅警告）
    assert any("跳过" in w for w in r.warnings)


def test_match_a5_runtime_with_exact_wheels():
    device = DeviceInfo(DeviceType.ASCEND, "Ascend950PR", 8, arch="aarch64")
    runtime = ImageRuntimeInfo(
        python_version="3.12", python_abi="cp312", arch="aarch64",
        glibc_version="2.34", cann_version="9.2", variant="a5",
        probe_ok=True)
    result = validate_build_match(
        device, "aarch64", "quay.io/ascend/vllm-ascend:v0.30.0rc2-a5",
        "/tmp/uc_manager_cann920_a5-0.9.0rc3-cp312-cp312-manylinux_2_34_aarch64.whl",
        "/tmp/wrapt-1.17.2-cp312-cp312-manylinux_2_17_aarch64.manylinux2014_aarch64.whl",
        image_runtime=runtime,
        toolkit_whl="/tmp/ucm_toolkit-0.9.0rc3-py3-none-any.whl")
    assert result.ok, result.errors
    assert "Python 3.12" in result.message
    assert "CANN 9.2" in result.message
    assert "A5" in result.message


def test_match_rejects_python_variant_and_glibc_mismatch():
    device = DeviceInfo(DeviceType.ASCEND, "Ascend950PR", 8, arch="aarch64")
    runtime = ImageRuntimeInfo(
        python_version="3.12", python_abi="cp312", arch="aarch64",
        glibc_version="2.28", cann_version="9.2", variant="a5",
        probe_ok=True)
    result = validate_build_match(
        device, "aarch64", "quay.io/ascend/vllm-ascend:v0.30.0rc2-a5",
        "/tmp/uc_manager_cann900_a2-0.9.0rc3-cp311-cp311-manylinux_2_34_aarch64.whl",
        "/tmp/wrapt-1.17.2-cp312-cp312-manylinux_2_17_aarch64.whl",
        image_runtime=runtime)
    assert not result.ok
    assert any("Python 标记不匹配" in error for error in result.errors)
    assert any("runtime 不匹配" in error for error in result.errors)
    assert any("设备变体不匹配" in error for error in result.errors)
    assert any("glibc" in error for error in result.errors)


def test_match_rejects_cuda_runtime_mismatch():
    device = DeviceInfo(DeviceType.NVIDIA, "NVIDIA A800", 8, arch="x86_64")
    runtime = ImageRuntimeInfo(
        python_version="3.12", python_abi="cp312", arch="x86_64",
        glibc_version="2.35", cuda_version="13.0", probe_ok=True)
    result = validate_build_match(
        device, "x86_64", "vllm/vllm-openai:v0.31.0-cu130",
        "/tmp/uc_manager_cuda_cu129-0.9.0rc3-cp312-cp312-manylinux_2_28_x86_64.whl",
        "/tmp/wrapt-1.17.2-cp312-cp312-manylinux_2_17_x86_64.whl",
        image_runtime=runtime)
    assert not result.ok
    assert any("runtime 不匹配" in error for error in result.errors)


def test_image_runtime_info_parses_cuda_tags():
    info = ImageRuntimeInfo.from_image_ref("vllm/vllm-openai:v0.31.0-cu129")
    assert info.cuda_version == "12.9"
    assert info.cuda_code == "cu129"

    info = ImageRuntimeInfo.from_image_ref("vllm/vllm-openai:v0.31.0-cu130")
    assert info.cuda_version == "13.0"
    assert info.cuda_code == "cu130"


def test_image_runtime_info_parses_probe_output():
    output = """__PYTHON__
python=3.12
arch=aarch64
__GLIBC__
ldd (Ubuntu GLIBC 2.34-0ubuntu3.7) 2.34
__CUDA__
cuda=12.9
__CANN__
Version=9.2.0
"""
    info = ImageRuntimeInfo.from_probe(
        output, "quay.io/ascend/vllm-ascend:v0.30.0rc2-a5-openeuler")
    assert info.python_version == "3.12"
    assert info.python_abi == "cp312"
    assert info.arch == "aarch64"
    assert info.glibc_version == "2.34"
    assert info.cuda_version == "12.9"
    assert info.cann_version == "9.2.0"
    assert info.cann_code == "cann920"
    assert info.variant == "a5"
    assert info.probe_ok
    assert info.ucm_wheel_glibc == "2.34"


def test_fetch_image_runtime_info_runs_docker_probe(fake_ssh):
    output = """__PYTHON__
python=3.12
arch=x86_64
__GLIBC__
ldd (GNU libc) 2.35
__CUDA__
cuda=13.0
__CANN__
"""
    fake_ssh.add_default(r"^docker run --rm --entrypoint /bin/sh", stdout=output)
    ssh = make_ssh(fake_ssh)
    info = fetch_image_runtime_info(
        ssh, "registry.example.com/vllm-openai:v0.31.0-cu130")
    assert info.python_version == "3.12"
    assert info.python_abi == "cp312"
    assert info.arch == "x86_64"
    assert info.glibc_version == "2.35"
    assert info.cuda_version == "13.0"


# ============================================================ Dockerfile
def test_dockerfile_full():
    cfg = ImageBuildConfig(base_image="quay.io/ascend/vllm-ascend:v0.23.0-a3",
                           image_tag="a-ucm:1", platform="ascend")
    df = generate_dockerfile(cfg, "ucm.whl", "wrapt-1.16.0-cp39.whl",
                             "ucm_toolkit-0.1.0-py3-none-any.whl")
    assert "FROM quay.io/ascend/vllm-ascend:v0.23.0-a3" in df
    assert "ENV PLATFORM=ascend" in df
    assert "COPY ucm.whl /tmp/ucm-pkgs/" in df
    assert "COPY wrapt-1.16.0-cp39.whl /tmp/ucm-pkgs/" in df
    assert "COPY ucm_toolkit-0.1.0-py3-none-any.whl /tmp/ucm-pkgs/" in df
    # wrapt 先装（离线），UCM 后装（离线 --no-index）
    wrapt_line = df.index("RUN pip install --no-cache-dir --no-index"
                          " --find-links=/tmp/ucm-pkgs /tmp/ucm-pkgs/wrapt-1.16.0-cp39.whl")
    ucm_line = df.index("RUN pip install --no-cache-dir --no-index"
                         " --find-links=/tmp/ucm-pkgs /tmp/ucm-pkgs/ucm.whl")
    assert wrapt_line < ucm_line
    # toolkit 同样离线 pip install whl
    toolkit_line = df.index(
        "RUN pip install --no-cache-dir --no-index"
        " --find-links=/tmp/ucm-pkgs /tmp/ucm-pkgs/ucm_toolkit-0.1.0-py3-none-any.whl")
    assert toolkit_line > ucm_line


def test_dockerfile_without_toolkit():
    cfg = ImageBuildConfig(base_image="b:1", image_tag="t:1")
    df = generate_dockerfile(cfg, "ucm.whl", "wrapt-1.16.0-cp39.whl")
    assert "toolkit" not in df


# ============================================================ 配置校验
def test_config_validate_missing_wrapt(tmp_path):
    whl = make_whl(tmp_path)
    cfg = ImageBuildConfig(base_image="b:1", image_tag="t:1",
                           ucm_whl_local=whl, wrapt_whl_local=str(tmp_path / "nope.whl"))
    with pytest.raises(Exception, match="wrapt"):
        cfg.validate()


def test_config_validate_missing_ucm(tmp_path):
    cfg = ImageBuildConfig(base_image="b:1", image_tag="t:1",
                           ucm_whl_local=str(tmp_path / "nope.whl"),
                           wrapt_whl_local=make_whl(tmp_path, "wrapt-1.whl"))
    with pytest.raises(Exception, match="UCM"):
        cfg.validate()


def test_config_validate_bad_tag(tmp_path):
    cfg = ImageBuildConfig(base_image="b:1", image_tag="bad tag",
                           ucm_whl_local=make_whl(tmp_path),
                           wrapt_whl_local=make_whl(tmp_path, "wrapt-1.whl"))
    with pytest.raises(Exception, match="镜像名"):
        cfg.validate()


def test_config_validate_bad_toolkit_whl(tmp_path):
    cfg = ImageBuildConfig(base_image="b:1", image_tag="t:1",
                           ucm_whl_local=make_whl(tmp_path),
                           wrapt_whl_local=make_whl(tmp_path, "wrapt-1.whl"),
                           toolkit_whl_local=str(tmp_path / "nope-toolkit.whl"))
    with pytest.raises(Exception, match="toolkit"):
        cfg.validate()


def test_config_validate_toolkit_ok(tmp_path):
    toolkit = make_whl(tmp_path, "ucm_toolkit-0.1.0-py3-none-any.whl")
    cfg = ImageBuildConfig(base_image="b:1", image_tag="t:1",
                           ucm_whl_local=make_whl(tmp_path),
                           wrapt_whl_local=make_whl(tmp_path, "wrapt-1.whl"),
                           toolkit_whl_local=toolkit)
    cfg.validate()


# ============================================================ 构建
def test_build_full_flow(fake_ssh, tmp_path):
    ssh = make_ssh(fake_ssh)
    whl = make_whl(tmp_path)
    wrapt = make_whl(tmp_path, "wrapt-1.17.2-cp39-cp39-manylinux2014_aarch64.whl")
    toolkit = make_whl(tmp_path, "ucm_toolkit-0.1.0-py3-none-any.whl")

    ssh._client.add(r"^mkdir -p .* && test -d", stdout="")
    ssh._client.add(r"^docker build -t ", stdout="Step 1/4\nSuccessfully built abc123\n")
    ssh._client.add(r"docker run --rm --entrypoint /bin/sh .+ -c .+pip list",
                    stdout="uc-manager==0.2.1\n")
    ssh._client.add(r"^rm -rf ", stdout="")

    logs = []
    progress_events = []
    cfg = ImageBuildConfig(base_image="quay.io/ascend/vllm-ascend:v0.23.0-a3",
                           image_tag="vllm-ascend-ucm:v0.23.0-a3",
                           ucm_whl_local=whl, wrapt_whl_local=wrapt,
                           toolkit_whl_local=toolkit,
                           platform="ascend")
    result = ImageBuilder(ssh).build(
        cfg, progress=lambda p, m: progress_events.append((p, m)),
        log=logs.append)
    assert result.ok
    assert result.image == "vllm-ascend-ucm:v0.23.0-a3"
    assert result.ucm.installed and result.ucm.version == "0.2.1"

    # 上传了三个 whl
    puts = [p for s in ssh._client.sftp_sessions for p in s.puts]
    assert any("uc_manager-0.2.1" in p[1] for p in puts)
    assert any("wrapt-1.17.2" in p[1] for p in puts)
    assert any("ucm_toolkit-0.1.0" in p[1] for p in puts)

    # Dockerfile 写到了远端（wrapt 先装、toolkit 为 whl 安装）
    dockerfile = ssh._client.files[
        [k for k in ssh._client.files if k.endswith("Dockerfile")][0]].decode()
    assert "FROM quay.io/ascend/vllm-ascend:v0.23.0-a3" in dockerfile
    wrapt_run = dockerfile.index("RUN pip install --no-cache-dir --no-index"
                                 " --find-links=/tmp/ucm-pkgs /tmp/ucm-pkgs/wrapt-1.17.2")
    ucm_run = dockerfile.index("RUN pip install --no-cache-dir --no-index"
                               " --find-links=/tmp/ucm-pkgs /tmp/ucm-pkgs/uc_manager-0.2.1")
    toolkit_run = dockerfile.index(
        "RUN pip install --no-cache-dir --no-index"
        " --find-links=/tmp/ucm-pkgs /tmp/ucm-pkgs/ucm_toolkit-0.1.0")
    assert wrapt_run < ucm_run < toolkit_run

    # 进度单调不减且达到 100
    pcts = [p for p, _ in progress_events]
    assert pcts == sorted(pcts)
    assert pcts[-1] == 100


def test_build_missing_base_fails(fake_ssh, tmp_path):
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"^mkdir -p .* && test -d", stdout="")
    ssh._client.add(r"^docker build -t ", stderr="pull access denied", exit_code=1)
    ssh._client.add(r"^rm -rf ", stdout="")
    cfg = ImageBuildConfig(base_image="no-such/image:9.9", image_tag="x:1",
                           ucm_whl_local=make_whl(tmp_path),
                           wrapt_whl_local=make_whl(tmp_path, "wrapt-1.whl"))
    with pytest.raises(Exception):
        ImageBuilder(ssh).build(cfg)


def test_build_cancel(fake_ssh, tmp_path):
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"^mkdir -p .* && test -d", stdout="")
    ssh._client.add(r"^rm -rf ", stdout="")
    cfg = ImageBuildConfig(base_image="b:1", image_tag="t:1",
                           ucm_whl_local=make_whl(tmp_path),
                           wrapt_whl_local=make_whl(tmp_path, "wrapt-1.whl"))
    with pytest.raises(BuildCancelled):
        ImageBuilder(ssh).build(cfg, cancelled=lambda: True)


def test_upload_image_tar(fake_ssh, tmp_path):
    from ucm_deployer.core.docker_manager import DockerManager

    ssh = make_ssh(fake_ssh)
    tar = tmp_path / "vllm-image.tar.gz"
    tar.write_bytes(b"tar-bytes")
    ssh._client.add(r"^docker load", stdout="Loaded image: vllm/vllm-openai:latest\n")
    refs = upload_image_tar(ssh, DockerManager(ssh), str(tar))
    assert refs == ["vllm/vllm-openai:latest"]
    puts = [p for s in ssh._client.sftp_sessions for p in s.puts]
    assert puts[-1][1] == "/tmp/vllm-image.tar.gz"
