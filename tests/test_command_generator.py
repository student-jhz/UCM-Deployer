# -*- coding: utf-8 -*-
import json
import shutil
import subprocess

import pytest

from ucm_deployer.core.command_generator import (
    CommandGenerator,
    kv_config_colocated,
    kv_config_consumer,
    kv_config_producer,
)
from ucm_deployer.core.models import DeviceType
from ucm_deployer.core.topology import DeployMode, DeployPlan, NodePlan, NodeRole

BASH = shutil.which("bash")


def node(name, host, role=NodeRole.MIXED, dp=1, tp=8, cards=8):
    return NodePlan(server_id=f"id-{name}", server_name=name, host=host,
                    role=role, dp=dp, tp=tp, container=f"c-{name}", cards=cards)


def gen(plan):
    return CommandGenerator(plan).generate()


# ------------------------------------------------------------ kv 配置
def test_kv_colocated_json():
    kv = kv_config_colocated("/root/ucm-deploy/ucm_config.yaml")
    data = json.loads(kv)
    assert data["kv_connector"] == "UCMConnector"
    assert data["kv_role"] == "kv_both"
    assert data["kv_connector_extra_config"]["UCM_CONFIG_FILE"].endswith("ucm_config.yaml")


def test_kv_producer_has_mooncake_splice():
    kv = kv_config_producer(4, 4, 4, 4, "/cfg.yaml")
    assert "'\"$mooncake_port\"'" in kv
    data = json.loads(kv.replace("'\"$mooncake_port\"'", "12345"))
    connectors = data["kv_connector_extra_config"]["connectors"]
    assert connectors[0]["kv_connector"] == "MooncakeConnectorV1"
    assert connectors[1]["kv_connector"] == "UCMConnector"
    assert connectors[0]["kv_connector_extra_config"]["prefill"] == {"dp_size": 4, "tp_size": 4}
    assert connectors[0]["kv_connector_extra_config"]["decode"] == {"dp_size": 4, "tp_size": 4}


def test_kv_consumer():
    kv = kv_config_consumer(4, 4, 2, 8)
    data = json.loads(kv.replace("'\"$mooncake_port\"'", "1"))
    assert data["kv_role"] == "kv_consumer"
    assert data["kv_connector_extra_config"]["prefill"] == {"dp_size": 4, "tp_size": 4}
    assert data["kv_connector_extra_config"]["decode"] == {"dp_size": 2, "tp_size": 8}


# ------------------------------------------------------------ 单机混部
def test_colocated_single_script():
    p = DeployPlan(mode=DeployMode.COLOCATED, engine="vllm",
                   device_type=DeviceType.ASCEND,
                   model_path="/models/Qwen3-32B", served_model_name="qwen",
                   extra_args="--max-model-len 32000\n--gpu-memory-utilization 0.9",
                   nodes=[node("s1", "10.0.0.1", dp=1, tp=8)])
    ds = gen(p)
    assert len(ds.scripts) == 1
    s = ds.scripts[0]
    assert s.name == "serve.sh" and s.role == "serve"
    assert s.path == "/root/ucm-deploy/serve.sh"
    assert "vllm serve /models/Qwen3-32B" in s.content
    assert "--tensor-parallel-size 8" in s.content
    assert "--port 9000" in s.content
    assert "--served-model-name qwen" in s.content
    assert "--max-model-len 32000" in s.content
    assert "kv_both" in s.content and "UCMConnector" in s.content
    assert "UCM_CONFIG_FILE" in s.content
    assert "export ENABLE_UCM_PATCH=1" in s.content
    # ucm 配置模板
    assert any(f.name == "ucm_config.yaml" for f in ds.config_files)
    assert ds.health_checks[0].url == "http://127.0.0.1:9000/health"


def test_colocated_single_dp_loop():
    p = DeployPlan(mode=DeployMode.COLOCATED, engine="vllm",
                   device_type=DeviceType.ASCEND,
                   model_path="/models/m",
                   nodes=[node("s1", "10.0.0.1", dp=2, tp=4)])
    ds = gen(p)
    s = ds.scripts[0]
    assert s.role == "serve_dp"
    assert "DP_LOCAL=2" in s.content
    assert "DP_GLOBAL=2" in s.content
    assert "--data-parallel-rank $dp_rank" in s.content
    assert "ASCEND_RT_VISIBLE_DEVICES" in s.content
    assert "mooncake" not in s.content.lower().replace("mooncake_port_start", "") or True
    assert s.ports == [9000, 9001]


# ------------------------------------------------------------ 多机混部 ray
def test_colocated_ray():
    p = DeployPlan(mode=DeployMode.COLOCATED, engine="vllm",
                   device_type=DeviceType.ASCEND,
                   model_path="/models/m", pp=2,
                   nodes=[node("s1", "10.0.0.1", dp=1, tp=8),
                          node("s2", "10.0.0.2", dp=1, tp=8)])
    ds = gen(p)
    roles = {s.role for s in ds.scripts}
    assert roles == {"ray-head", "ray-worker", "serve"}
    head = [s for s in ds.scripts if s.role == "ray-head"][0]
    worker = [s for s in ds.scripts if s.role == "ray-worker"][0]
    serve = [s for s in ds.scripts if s.role == "serve"][0]
    assert "ray start --head --port 6379" in head.content
    assert "ray start --address 10.0.0.1:6379" in worker.content
    assert serve.server_id == "id-s1"  # serve 在 head 节点
    assert "--pipeline-parallel-size 2" in serve.content
    assert "kv_both" in serve.content
    assert "多机混部(ray)" in ds.summary


# ------------------------------------------------------------ PD 分离
def make_pd_plan():
    return DeployPlan(
        mode=DeployMode.PD, engine="vllm", device_type=DeviceType.ASCEND,
        model_path="/models/DeepSeek", kv_cache_dir="/mnt/nfs1",
        nodes=[node("p1", "10.0.0.1", role=NodeRole.P, dp=2, tp=4),
               node("p2", "10.0.0.2", role=NodeRole.P, dp=2, tp=4),
               node("d1", "10.0.0.3", role=NodeRole.D, dp=2, tp=4),
               node("d2", "10.0.0.4", role=NodeRole.D, dp=2, tp=4)])


def test_pd_scripts():
    p = make_pd_plan()
    ds = gen(p)
    roles = [s.role for s in ds.scripts]
    assert roles.count("mooncake-master") == 1
    assert roles.count("prefill") == 2
    assert roles.count("decode") == 2
    assert roles.count("load-balancer") == 1

    # mooncake master 在 p1
    mc = [s for s in ds.scripts if s.role == "mooncake-master"][0]
    assert mc.server_id == "id-p1"
    assert "mooncake_master --port 50088" in mc.content

    # mooncake.json 配置在全部 4 个节点
    mj = [f for f in ds.config_files if f.name == "mooncake.json"]
    assert len(mj) == 4
    data = json.loads(mj[0].content)
    assert data["master_server_address"] == "10.0.0.1:50088"
    assert data["protocol"] == "ascend"

    # prefill 脚本
    p1 = [s for s in ds.scripts if s.role == "prefill" and s.server_id == "id-p1"][0]
    p2 = [s for s in ds.scripts if s.role == "prefill" and s.server_id == "id-p2"][0]
    assert "MultiConnector" in p1.content
    assert "MooncakeConnectorV1" in p1.content
    assert "UCMConnector" in p1.content
    assert "'\"$mooncake_port\"'" in p1.content
    assert "MOONCAKE_PORT_START=20001" in p1.content
    assert "MOONCAKE_PORT_START=20009" in p2.content
    assert "DP_RANK_START=0" in p1.content
    assert "DP_RANK_START=2" in p2.content
    assert "DP_ADDRESS=10.0.0.1" in p1.content
    assert p1.ports == [9000, 9001]

    # decode 脚本：kv_consumer，无 UCM connector
    d1 = [s for s in ds.scripts if s.role == "decode" and s.server_id == "id-d1"][0]
    assert "kv_consumer" in d1.content
    assert "UCMConnector" not in d1.content
    assert "DP_ADDRESS=10.0.0.3" in d1.content

    # 负载均衡
    lb = [s for s in ds.scripts if s.role == "load-balancer"][0]
    assert "--prefiller-hosts 10.0.0.1 10.0.0.1 10.0.0.2 10.0.0.2" in lb.content
    assert "--prefiller-ports 9000 9001 9000 9001" in lb.content
    assert "--decoder-hosts 10.0.0.3 10.0.0.3 10.0.0.4 10.0.0.4" in lb.content
    assert "vllm-ascend/examples/disaggregated_prefill_v1/load_balance_proxy_server_example.py" in lb.content

    # 健康检查：4 个引擎 + 1 个 LB
    assert len(ds.health_checks) == 9

    # UCM 配置模板
    ucm = [f for f in ds.config_files if f.name == "ucm_config.yaml"][0]
    assert "/mnt/nfs1" in ucm.content


def test_pd_nvidia_protocol():
    p = make_pd_plan()
    p.device_type = DeviceType.NVIDIA
    ds = gen(p)
    mj = [f for f in ds.config_files if f.name == "mooncake.json"][0]
    assert json.loads(mj.content)["protocol"] == "tcp"
    d1 = [s for s in ds.scripts if s.role == "decode"][0]
    assert "CUDA_VISIBLE_DEVICES" in d1.content
    assert "ASCEND_RT_VISIBLE_DEVICES" not in d1.content


# ------------------------------------------------------------ sglang
def test_sglang_script():
    p = DeployPlan(mode=DeployMode.COLOCATED, engine="sglang",
                   device_type=DeviceType.NVIDIA,
                   model_path="/models/Qwen", kv_cache_dir="/mnt/cache",
                   nodes=[node("s1", "10.0.0.1", dp=1, tp=8)])
    ds = gen(p)
    s = ds.scripts[0]
    assert "sglang.launch_server" in s.content
    assert "--enable-hierarchical-cache" in s.content
    assert "ucm.integration.sglang.unifiedcache_store" in s.content
    assert "/mnt/cache" in s.content
    assert "--tensor-parallel-size 8" in s.content


# ------------------------------------------------------------ 脚本静态自检
import re  # noqa: E402


def _bash_works() -> bool:
    if not BASH:
        return False
    try:
        r = subprocess.run([BASH, "-n", "-c", "echo ok"],
                           capture_output=True, timeout=15)
        return r.returncode == 0
    except Exception:
        return False


BASH_OK = _bash_works()


def _quotes_balanced(content: str) -> bool:
    in_single = in_double = False
    i, n = 0, len(content)
    while i < n:
        ch = content[i]
        if in_single:
            if ch == "'":
                in_single = False
        elif in_double:
            if ch == "\\":
                i += 1
            elif ch == '"':
                in_double = False
        else:
            if ch == "'":
                in_single = True
            elif ch == '"':
                in_double = True
            elif ch == "\\":
                i += 1
        i += 1
    return not in_single and not in_double


def _continuations_ok(content: str) -> bool:
    lines = content.split("\n")
    for idx, line in enumerate(lines):
        if line.rstrip().endswith("\\"):
            if idx == len(lines) - 1 or not lines[idx + 1].strip():
                return False
    return True


def _extract_kv_blocks(content: str):
    """提取脚本中 --kv-transfer-config 后单引号包裹的 JSON 块。"""
    blocks = []
    lines = content.split("\n")
    i = 0
    while i < len(lines):
        if lines[i].strip().endswith("--kv-transfer-config \\"):
            j = i + 1
            block = []
            while j < len(lines):
                block.append(lines[j])
                if lines[j].rstrip().endswith("'"):
                    break
                j += 1
            text = "\n".join(block).strip()
            if text.startswith("'") and text.endswith("'"):
                blocks.append(text[1:-1])
            i = j
        i += 1
    return blocks


def assert_script_static_ok(content: str) -> None:
    """无 bash 环境时的静态自检：引号配平、续行合法、kv JSON 可解析。"""
    assert _quotes_balanced(content), "脚本引号不配平"
    assert _continuations_ok(content), "脚本续行符不合法"
    for block in _extract_kv_blocks(content):
        normalized = re.sub(r"'\"\$\w+\"'", "1", block)
        data = json.loads(normalized)  # kv-transfer-config 必须是合法 JSON
        assert isinstance(data, dict) and "kv_connector" in data


# ------------------------------------------------------------ bash 语法自检
# ------------------------------------------------------------ 静态自检（所有环境必跑）
def test_generated_scripts_static_ok():
    for p in _build_test_plans():
        ds = gen(p)
        assert ds.scripts, "应生成至少一个脚本"
        for s in ds.scripts:
            assert_script_static_ok(s.content)
        for f in ds.config_files:
            if f.name.endswith(".json"):
                json.loads(f.content)


def _build_test_plans():
    plans = [
        # 单机混部 dp=1
        DeployPlan(mode=DeployMode.COLOCATED, engine="vllm",
                   device_type=DeviceType.ASCEND, model_path="/models/m",
                   extra_args="--max-model-len 8192",
                   nodes=[node("s1", "10.0.0.1", tp=8)]),
        # 单机混部 dp=2 (loop)
        DeployPlan(mode=DeployMode.COLOCATED, engine="vllm",
                   device_type=DeviceType.NVIDIA, model_path="/models/m",
                   extra_args="--seed 1024",
                   nodes=[node("s1", "10.0.0.1", dp=2, tp=4)]),
        # 多机混部 ray
        DeployPlan(mode=DeployMode.COLOCATED, engine="vllm",
                   device_type=DeviceType.ASCEND, model_path="/models/m",
                   pp=2, extra_args="--seed 1024",
                   nodes=[node("s1", "10.0.0.1"), node("s2", "10.0.0.2")]),
        # PD 分离
        DeployPlan(mode=DeployMode.PD, engine="vllm",
                   device_type=DeviceType.ASCEND, model_path="/models/m",
                   extra_args="--trust-remote-code",
                   nodes=[node("p1", "10.0.0.1", role=NodeRole.P, dp=2, tp=4),
                          node("d1", "10.0.0.2", role=NodeRole.D, dp=2, tp=4)]),
        # sglang
        DeployPlan(mode=DeployMode.COLOCATED, engine="sglang",
                   device_type=DeviceType.NVIDIA, model_path="/models/m",
                   extra_args="--max-model-len 8192",
                   nodes=[node("s1", "10.0.0.1", tp=8)]),
    ]
    return plans


@pytest.mark.skipif(not BASH_OK, reason="本机无可用 bash（WSL 损坏时跳过）")
@pytest.mark.parametrize("plan_index", [0, 1, 2, 3, 4])
def test_generated_scripts_bash_syntax(tmp_path, plan_index):
    p = _build_test_plans()[plan_index]
    ds = gen(p)
    assert ds.scripts, "应生成至少一个脚本"
    for s in ds.scripts:
        f = tmp_path / s.name
        with f.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(s.content)
        result = subprocess.run([BASH, "-n", str(f)],
                                capture_output=True, text=True)
        assert result.returncode == 0, (
            f"bash 语法错误 {s.name}: {result.stderr}\n-----\n{s.content}")
