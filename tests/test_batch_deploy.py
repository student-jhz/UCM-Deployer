# -*- coding: utf-8 -*-
"""批量部署核心模块测试（模板序列化、镜像分发、容器/预检流水线）。"""
import json

import pytest

from ucm_deployer.core.batch_deploy import (
    TEMPLATE_KIND,
    TEMPLATE_VERSION,
    BatchContainerConfig,
    BatchDeployError,
    BatchPrecheckConfig,
    BatchStages,
    DeployTemplate,
    TemplateError,
    create_container_from_template,
    export_image_tar,
    load_image_tar,
    pull_image_on_target,
    run_precheck_on_target,
    stage_progress_ranges,
    upsert_servers_from_template,
)
from ucm_deployer.core.docker_manager import DockerManager
from ucm_deployer.core.models import ServerInfo
from ucm_deployer.core.ssh_client import SSHClient


def make_ssh(fake_ssh, host="127.0.0.1"):
    ssh = SSHClient(ServerInfo.create(name="s", host=host))
    ssh.connect()
    return ssh


def make_registry(tmp_path):
    from ucm_deployer.core import crypto
    from ucm_deployer.core.server_registry import ServerRegistry
    from ucm_deployer.utils import paths

    paths.default_config_dir = lambda: tmp_path
    crypto._key_path = lambda: tmp_path / ".secret.key"
    return ServerRegistry(tmp_path)


def make_template() -> DeployTemplate:
    from ucm_deployer.core.models import VolumeMount

    return DeployTemplate(
        image_mode="tar",
        image_ref="ucm-vllm:0.3.6",
        source_host="10.0.0.10",
        source_port=22,
        skip_existing=True,
        recreate_container=True,
        servers=[
            ServerInfo.create(name="node-1", host="10.0.0.11",
                              password="pw1", remark="r1"),
            ServerInfo.create(name="node-2", host="10.0.0.12"),
        ],
        container=BatchContainerConfig(
            name="ucm-vllm", shm_size="512g", network="host",
            ports=["8000:8000"], kv_cache_dirs=["/mnt/kvcache"],
            model_mount=VolumeMount("/data/models", "/models", True),
            extra_mounts=[VolumeMount("/data/logs", "/logs", False)],
            extra_args="--ulimit nofile=65535:65535"),
        precheck=BatchPrecheckConfig(
            bandwidth_check=True, mount_path="/mnt/kvcache",
            custom_config_enabled=True,
            custom_config_json='{"bandwidth": {"workers": 8}}'),
        stages=BatchStages(distribute_image=True, create_container=True,
                           run_precheck=False),
    )


# ============================================================ 模板序列化
def test_template_roundtrip_without_secrets():
    tpl = make_template()
    data = tpl.to_dict(include_secrets=False)
    assert data["kind"] == TEMPLATE_KIND
    assert data["template_version"] == TEMPLATE_VERSION
    # 密码不导出
    for item in data["servers"]:
        assert "password" not in item
        assert "passphrase" not in item

    restored = DeployTemplate.from_dict(data)
    assert restored.image_ref == "ucm-vllm:0.3.6"
    assert restored.image_mode == "tar"
    assert restored.skip_existing is True
    assert restored.recreate_container is True
    assert [s.host for s in restored.servers] == ["10.0.0.11", "10.0.0.12"]
    assert restored.servers[0].password == ""     # 密码未导出
    assert restored.container.name == "ucm-vllm"
    assert restored.container.kv_cache_dirs == ["/mnt/kvcache"]
    assert restored.container.model_mount.host_path == "/data/models"
    assert restored.container.model_mount.readonly is True
    assert restored.container.extra_mounts[0].container_path == "/logs"
    assert restored.container.extra_args.startswith("--ulimit")
    assert restored.precheck.bandwidth_check is True
    assert restored.precheck.custom_config_enabled is True
    assert json.loads(restored.precheck.custom_config_json) == {
        "bandwidth": {"workers": 8}}
    assert restored.stages.run_precheck is False


def test_template_roundtrip_with_secrets():
    tpl = make_template()
    data = tpl.to_dict(include_secrets=True)
    assert data["servers"][0]["password"] == "pw1"
    restored = DeployTemplate.from_dict(data)
    assert restored.servers[0].password == "pw1"


def test_template_save_and_load(tmp_path):
    path = str(tmp_path / "tpl.json")
    tpl = make_template()
    tpl.save(path, include_secrets=True)
    restored = DeployTemplate.load(path)
    assert restored.image_ref == tpl.image_ref
    assert restored.servers[0].password == "pw1"
    # 导出的 JSON 便于手工编辑：kind/版本字段存在
    raw = json.loads(open(path, encoding="utf-8").read())
    assert raw["kind"] == TEMPLATE_KIND


def test_template_precheck_config_import_accepts_object_and_string():
    base = make_template().to_dict()
    # 对象形式
    base["precheck"]["custom_config_json"] = {"a": 1}
    cfg = DeployTemplate.from_dict(base).precheck
    assert json.loads(cfg.custom_config_json) == {"a": 1}
    # 字符串形式
    base["precheck"]["custom_config_json"] = '{"a": 2}'
    cfg = DeployTemplate.from_dict(base).precheck
    assert json.loads(cfg.custom_config_json) == {"a": 2}


@pytest.mark.parametrize("mutate,message", [
    (lambda d: d.update(kind="other-tool"), "kind"),
    (lambda d: d.update(template_version=99), "版本"),
    (lambda d: d["image"].update(mode="p2p"), "分发方式"),
    (lambda d: d["servers"].append({"name": "x"}), "host"),
    (lambda d: d["servers"].append({"host": "h", "port": "abc"}), "端口"),
    (lambda d: d["precheck"].update(custom_config_enabled=True,
                                    custom_config_json="{bad"), "JSON"),
    (lambda d: d["container"].update(name="非法 名字"), "模板配置无效"),
])
def test_template_validation_errors(mutate, message):
    data = make_template().to_dict()
    mutate(data)
    with pytest.raises(TemplateError) as exc:
        DeployTemplate.from_dict(data)
    assert message in str(exc.value)


def test_template_load_rejects_invalid_json_and_missing_file(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(TemplateError):
        DeployTemplate.load(str(bad))
    with pytest.raises(TemplateError):
        DeployTemplate.load(str(tmp_path / "missing.json"))


def test_upsert_servers_from_template(tmp_path):
    reg = make_registry(tmp_path)
    existing = ServerInfo.create(name="old-name", host="10.0.0.11",
                                 password="old-pw", remark="old")
    reg.upsert(existing)

    incoming = ServerInfo.create(name="node-1", host="10.0.0.11",
                                 remark="new-remark")           # 无密码
    incoming2 = ServerInfo.create(name="node-2", host="10.0.0.12",
                                  password="pw2")
    imported, messages = upsert_servers_from_template(
        reg, [incoming, incoming2])

    assert len(messages) == 2
    servers = {s.host: s for s in reg.list_servers()}
    # 命中 host+port+user：更新信息、保留本地密码（模板密码为空）
    assert servers["10.0.0.11"].name == "node-1"
    assert servers["10.0.0.11"].remark == "new-remark"
    assert servers["10.0.0.11"].password == "old-pw"
    assert servers["10.0.0.11"].id == existing.id
    # 未命中：新增
    assert servers["10.0.0.12"].password == "pw2"
    assert len(imported) == 2


def test_stage_progress_ranges():
    # 全阶段：三段铺满 0-100
    ranges = stage_progress_ranges(BatchStages())
    assert ranges["image"] == (0, 45)
    assert ranges["container"] == (45, 70)
    assert ranges["precheck"] == (70, 100)

    # 仅预检：独占全程
    ranges = stage_progress_ranges(
        BatchStages(distribute_image=False, create_container=False))
    assert ranges == {"precheck": (0, 100)}

    # 分发+预检
    ranges = stage_progress_ranges(BatchStages(create_container=False))
    assert ranges["image"] == (0, 60)
    assert ranges["precheck"] == (60, 100)


# ============================================================ 镜像分发
def test_export_image_tar_saves_and_gzips(fake_ssh):
    ssh = make_ssh(fake_ssh)
    dm = DockerManager(ssh)
    ssh._client.add(r"docker image inspect", stdout="")
    ssh._client.add(r"bash -c", stdout="")
    ssh._client.add(r"test -s", stdout="")

    export_image_tar(ssh, dm, "ucm-vllm:0.3.6", "/tmp/pkg.tar.gz")
    cmds = [c.executed_command for c in ssh._client.channels]
    save_cmd = next(c for c in cmds if "docker save" in c)
    assert "bash -c" in save_cmd
    assert "set -o pipefail" in save_cmd
    assert "gzip -1 > /tmp/pkg.tar.gz" in save_cmd


def test_export_image_tar_missing_image(fake_ssh):
    ssh = make_ssh(fake_ssh)
    dm = DockerManager(ssh)
    ssh._client.add(r"docker image inspect", exit_code=1)
    with pytest.raises(BatchDeployError):
        export_image_tar(ssh, dm, "ucm-vllm:0.3.6", "/tmp/pkg.tar.gz")


def test_docker_manager_save_image_empty_output(fake_ssh):
    ssh = make_ssh(fake_ssh)
    dm = DockerManager(ssh)
    ssh._client.add(r"bash -c", stdout="")
    ssh._client.add(r"test -s", exit_code=1)     # 导出结果为空文件
    with pytest.raises(Exception) as exc:
        dm.save_image("ucm-vllm:0.3.6", "/tmp/pkg.tar.gz")
    assert "为空" in str(exc.value)


def test_load_image_tar_upload_load_and_cleanup(fake_ssh, tmp_path):
    ssh = make_ssh(fake_ssh)
    dm = DockerManager(ssh)
    local = tmp_path / "pkg.tar.gz"
    local.write_bytes(b"fake-tar-data")
    ssh._client.add(r"^docker load", stdout="Loaded image: ucm-vllm:0.3.6\n")
    ssh._client.add(r"docker image inspect", stdout="")
    ssh._client.add(r"rm -f", stdout="")

    logs = []
    refs = load_image_tar(ssh, dm, str(local), "ucm-vllm:0.3.6", log=logs.append)
    assert refs == ["ucm-vllm:0.3.6"]
    cmds = [c.executed_command for c in ssh._client.channels]
    assert any(c.startswith("docker load -i /tmp/pkg.tar.gz") for c in cmds)
    assert any(c.startswith("rm -f /tmp/pkg.tar.gz") for c in cmds)
    # 本机文件已上传到远端（FakeSFTP 内存）
    assert ssh._client.files.get("/tmp/pkg.tar.gz") == b"fake-tar-data"
    assert any("上传镜像包" in l for l in logs)


def test_load_image_tar_missing_local_file(fake_ssh, tmp_path):
    ssh = make_ssh(fake_ssh)
    dm = DockerManager(ssh)
    with pytest.raises(BatchDeployError):
        load_image_tar(ssh, dm, str(tmp_path / "no.tar.gz"), "img:1")


def test_load_image_tar_verifies_ref_after_load(fake_ssh, tmp_path):
    """docker load 成功但未见预期镜像（如 ref 不匹配）时报错。"""
    ssh = make_ssh(fake_ssh)
    dm = DockerManager(ssh)
    local = tmp_path / "pkg.tar.gz"
    local.write_bytes(b"data")
    ssh._client.add(r"^docker load", stdout="Loaded image: other:tag\n")
    ssh._client.add(r"docker image inspect", exit_code=1)  # 目标 ref 不存在
    with pytest.raises(BatchDeployError) as exc:
        load_image_tar(ssh, dm, str(local), "ucm-vllm:0.3.6")
    assert "未找到镜像" in str(exc.value)


def test_pull_image_on_target(fake_ssh):
    ssh = make_ssh(fake_ssh)
    dm = DockerManager(ssh)
    ssh._client.add(r"^docker pull", stdout="Status: Downloaded\n")
    ssh._client.add(r"docker image inspect", stdout="")
    logs = []
    ref = pull_image_on_target(dm, "quay.io/x/img:v1", log=logs.append)
    assert ref == "quay.io/x/img:v1"
    assert any("docker pull" in l for l in logs)


# ============================================================ 容器创建
_NPU_SMI = """+--------------------------------+
| npu-smi 24.1.rc2               |
+===+============+===============+
| NPU     Name   | Health        |
|=================+===============|
| 0       910B3  | OK            |
| 1       910B3  | OK            |
| 2       910B3  | OK            |
| 3       910B3  | OK            |
+--------------------------------+"""
_PRECHECK_OUT = (
    "[INFO] serving_stack  vLLM 0.10.0\n"
    "[PASS] kernel  5.10.0\n"
    "[PASS] memory_shm  512 GiB\n"
    "[PASS] bandwidth  12.0 GB/s\n"
    "Result: PASSED  |  3 pass, 0 warn, 0 fail\n"
)


def test_create_container_from_template_ascend(fake_ssh):
    ssh = make_ssh(fake_ssh)
    dm = DockerManager(ssh)
    # 镜像 UCM 校验
    ssh._client.add(r"docker run --rm", stdout="uc-manager==1.2.3\n")
    # 设备探测
    ssh._client.add(r"^uname -m", stdout="aarch64\n")
    ssh._client.add(r"^npu-smi info", stdout=_NPU_SMI)
    # Ascend 节点/挂载探测
    ssh._client.add(r"for d in /dev/davinci", stdout="/dev/davinci0\n/dev/davinci1\n")
    ssh._client.add(r"for p in /usr/local", stdout="/usr/local/Ascend/driver\n")
    # docker run
    ssh._client.add(r"^docker run -itd", stdout="container-id\n")

    tpl = make_template().container
    name, cmd = create_container_from_template(ssh, dm, tpl, "ucm-vllm:0.3.6")
    assert name == "ucm-vllm"
    assert "--name ucm-vllm" in cmd
    assert "--net=host" in cmd
    assert "--shm-size 512g" in cmd
    assert "--device /dev/davinci0" in cmd
    assert "-v /usr/local/Ascend/driver:/usr/local/Ascend/driver" in cmd
    assert "-v /mnt/kvcache:/mnt/kvcache" in cmd
    assert "-v /data/models:/models:ro" in cmd
    assert "-v /data/logs:/logs" in cmd
    assert "--ulimit nofile" in cmd
    assert "ucm-vllm:0.3.6" in cmd


def test_create_container_from_template_recreate_removes_old(fake_ssh):
    ssh = make_ssh(fake_ssh)
    dm = DockerManager(ssh)
    ssh._client.add(r"docker run --rm", stdout="uc-manager==1.2.3\n")
    ssh._client.add(r"^uname -m", stdout="aarch64\n")
    ssh._client.add(r"^npu-smi info", stdout=_NPU_SMI)
    ssh._client.add(r"for d in /dev/davinci", stdout="/dev/davinci0\n")
    ssh._client.add(r"for p in /usr/local", stdout="/usr/local/Ascend/driver\n")
    ssh._client.add(r"^docker run -itd", stdout="container-id\n")
    ssh._client.add(r"^docker rm -f", stdout="", exit_code=1)  # 不存在旧容器

    tpl = make_template().container
    create_container_from_template(ssh, dm, tpl, "ucm-vllm:0.3.6",
                                   recreate=True)
    cmds = [c.executed_command for c in ssh._client.channels]
    assert any(c == "docker rm -f ucm-vllm" for c in cmds)


def test_create_container_from_template_nvidia(fake_ssh):
    ssh = make_ssh(fake_ssh, host="127.0.0.2")
    dm = DockerManager(ssh)
    ssh._client.add(r"docker run --rm", stdout="uc-manager==1.2.3\n")
    ssh._client.add(r"^uname -m", stdout="x86_64\n")
    ssh._client.add(r"nvidia-smi --query-gpu", stdout="0, A800\n1, A800\n")
    ssh._client.add(r"^docker run -itd", stdout="container-id\n")

    tpl = make_template().container
    name, cmd = create_container_from_template(ssh, dm, tpl, "ucm-vllm:0.3.6")
    assert "--runtime nvidia" in cmd
    assert "--gpus all" in cmd
    assert "-p 8000:8000" in cmd
    assert "--net=host" not in cmd


def test_create_container_rejects_image_without_ucm(fake_ssh):
    ssh = make_ssh(fake_ssh)
    dm = DockerManager(ssh)
    ssh._client.add(r"docker run --rm", stdout="pip: no packages\n")
    with pytest.raises(BatchDeployError) as exc:
        create_container_from_template(
            ssh, dm, make_template().container, "ucm-vllm:0.3.6")
    assert "未安装 UCM" in str(exc.value)


def test_create_container_rejects_unknown_device(fake_ssh):
    ssh = make_ssh(fake_ssh)
    dm = DockerManager(ssh)
    ssh._client.add(r"docker run --rm", stdout="uc-manager==1.2.3\n")
    ssh._client.add(r"^uname -m", stdout="x86_64\n")
    ssh._client.add(r"^npu-smi info", exit_code=1)
    ssh._client.add(r"grep -cE", exit_code=1)
    ssh._client.add(r"nvidia-smi --query-gpu", exit_code=1)
    ssh._client.add(r"lspci", exit_code=1)
    with pytest.raises(BatchDeployError) as exc:
        create_container_from_template(
            ssh, dm, make_template().container, "ucm-vllm:0.3.6")
    assert "未探测到加速设备" in str(exc.value)


# ============================================================ 预检
def test_run_precheck_on_target_with_custom_config(fake_ssh):
    ssh = make_ssh(fake_ssh)
    # 保存配置 / 校验配置存在 / 校验挂载目录 / 临时目录创建清理
    ssh._client.add(r"mkdir -p", stdout="")
    ssh._client.add(r"test -f", stdout="")
    ssh._client.add(r"test -d", stdout="")
    ssh._client.add(r"rm -rf", stdout="")
    # 预检执行
    ssh._client.add(r"ucm-toolkit run precheck", stdout=_PRECHECK_OUT)

    cfg = make_template().precheck
    logs = []
    result = run_precheck_on_target(ssh, cfg, "ucm-vllm", log=logs.append)
    assert result.verdict == "passed"
    cmds = [c.executed_command for c in ssh._client.channels]
    precheck_cmd = next(c for c in cmds if "ucm-toolkit run precheck" in c)
    assert "--config /root/ucm-deploy/precheck.json" in precheck_cmd
    assert "--mount-path" in precheck_cmd
    save_cmd = next(c for c in cmds if "printf" in c)
    assert "/root/ucm-deploy/precheck.json" in save_cmd
    assert any("自定义预检配置" in l for l in logs)


def test_run_precheck_on_target_skip_bandwidth(fake_ssh):
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"ucm-toolkit run precheck", stdout=_PRECHECK_OUT)
    cfg = make_template().precheck
    cfg.bandwidth_check = False
    cfg.custom_config_enabled = False
    result = run_precheck_on_target(ssh, cfg, "ucm-vllm")
    cmds = [c.executed_command for c in ssh._client.channels]
    precheck_cmd = next(c for c in cmds if "ucm-toolkit run precheck" in c)
    assert "--skip-bandwidth" in precheck_cmd
    assert "--config" not in precheck_cmd
    assert result.verdict == "passed"


def test_run_precheck_on_target_invalid_custom_config(fake_ssh):
    ssh = make_ssh(fake_ssh)
    cfg = make_template().precheck
    cfg.custom_config_json = "{bad json"
    with pytest.raises(BatchDeployError):
        run_precheck_on_target(ssh, cfg, "ucm-vllm")


def test_run_precheck_on_target_passes_failure_verdict(fake_ssh):
    """预检 FAIL 不抛异常（结果保留在 verdict 中，由界面汇总展示）。"""
    ssh = make_ssh(fake_ssh)
    output = _PRECHECK_OUT.replace(
        "[PASS] memory_shm  512 GiB", "[FAIL] memory_shm  64 GiB")
    # toolkit 存在 FAIL 项时以非零退出码结束
    ssh._client.add(r"ucm-toolkit run precheck", stdout=output, exit_code=1)
    cfg = make_template().precheck
    cfg.bandwidth_check = False
    cfg.custom_config_enabled = False
    result = run_precheck_on_target(ssh, cfg, "ucm-vllm")
    assert result.verdict == "failed"
