# -*- coding: utf-8 -*-
from ucm_deployer.core.models import DeviceType
from ucm_deployer.core.topology import (
    DeployMode,
    DeployPlan,
    NodePlan,
    NodeRole,
    assign_processes,
    validate_plan,
)


def node(name, host, role=NodeRole.MIXED, dp=1, tp=8, cards=8, container="c"):
    return NodePlan(server_id=f"id-{name}", server_name=name, host=host,
                    role=role, dp=dp, tp=tp, container=container, cards=cards)


def plan_colocated_single(**kw):
    return DeployPlan(mode=DeployMode.COLOCATED, engine="vllm",
                      device_type=DeviceType.ASCEND,
                      nodes=[node("s1", "10.0.0.1", dp=1, tp=8)],
                      model_path="/models/Qwen3-32B", **kw)


def errors(plan):
    return [i.message for i in validate_plan(plan) if i.is_error]


def warnings(plan):
    return [i.message for i in validate_plan(plan) if not i.is_error]


# ------------------------------------------------------------ 混部
def test_colocated_single_ok():
    p = plan_colocated_single()
    assert errors(p) == []


def test_colocated_single_dp_ok():
    p = plan_colocated_single()
    p.nodes[0].dp = 2
    p.nodes[0].tp = 4
    assert errors(p) == []


def test_colocated_over_cards():
    p = plan_colocated_single()
    p.nodes[0].dp = 2
    assert any("超过可用卡数" in e for e in errors(p))


def test_colocated_missing_model():
    p = DeployPlan(mode=DeployMode.COLOCATED,
                   nodes=[node("s1", "10.0.0.1")], model_path="")
    assert any("模型路径" in e for e in errors(p))


def test_colocated_multinode_dp_rejected():
    p = DeployPlan(mode=DeployMode.COLOCATED, engine="vllm",
                   device_type=DeviceType.ASCEND,
                   model_path="/models/m",
                   nodes=[node("s1", "10.0.0.1", dp=2, tp=4),
                          node("s2", "10.0.0.2", dp=2, tp=4)])
    assert any("DP>1" in e for e in errors(p))


def test_colocated_multinode_tp_split():
    p = DeployPlan(mode=DeployMode.COLOCATED, engine="vllm",
                   device_type=DeviceType.ASCEND,
                   model_path="/models/m",
                   nodes=[node("s1", "10.0.0.1", dp=1, tp=8),
                          node("s2", "10.0.0.2", dp=1, tp=8)])
    assert errors(p) == []  # TP16 均分 2 台各 8 卡
    p.pp = 2
    assert errors(p) == []
    # TP 无法均分到 3 台
    p3 = DeployPlan(mode=DeployMode.COLOCATED, engine="vllm",
                    device_type=DeviceType.ASCEND,
                    model_path="/models/m",
                    nodes=[node("s1", "10.0.0.1", dp=1, tp=4),
                           node("s2", "10.0.0.2", dp=1, tp=4),
                           node("s3", "10.0.0.3", dp=1, tp=4)])
    assert any("无法在" in e and "均分" in e for e in errors(p3))
    # 各节点 TP 不一致
    p3.nodes[0].tp = 8
    assert any("TP 必须一致" in e for e in errors(p3))


# ------------------------------------------------------------ PD
def plan_pd():
    return DeployPlan(
        mode=DeployMode.PD, engine="vllm", device_type=DeviceType.ASCEND,
        model_path="/models/DeepSeek",
        nodes=[
            node("p1", "10.0.0.1", role=NodeRole.P, dp=2, tp=4),
            node("p2", "10.0.0.2", role=NodeRole.P, dp=2, tp=4),
            node("d1", "10.0.0.3", role=NodeRole.D, dp=2, tp=4),
            node("d2", "10.0.0.4", role=NodeRole.D, dp=2, tp=4),
        ])


def test_pd_ok():
    p = plan_pd()
    assert errors(p) == []


def test_pd_missing_d():
    p = plan_pd()
    p.nodes = [n for n in p.nodes if n.role == NodeRole.P]
    assert any("D(Decode)" in e for e in errors(p))


def test_pd_mixed_tp_in_group():
    p = plan_pd()
    p.nodes[1].tp = 8
    assert any("TP 必须一致" in e for e in errors(p))


def test_pd_tp_mismatch_warning():
    p = plan_pd()
    for n in p.d_nodes:
        n.tp = 8
        n.dp = 1
    assert not errors(p)
    assert any("TP 不一致" in w for w in warnings(p))


def test_pd_over_cards():
    p = plan_pd()
    p.nodes[0].dp = 4  # 4*4=16 > 8
    assert any("超过可用卡数" in e for e in errors(p))


# ------------------------------------------------------------ sglang
def test_sglang_pd_rejected():
    p = plan_pd()
    p.engine = "sglang"
    assert any("SGLang" in e for e in errors(p))


def test_sglang_multinode_rejected():
    p = DeployPlan(mode=DeployMode.COLOCATED, engine="sglang",
                   model_path="/m",
                   nodes=[node("s1", "10.0.0.1"), node("s2", "10.0.0.2")])
    assert any("多机" in e for e in errors(p))


def test_sglang_single_ok():
    p = DeployPlan(mode=DeployMode.COLOCATED, engine="sglang",
                   device_type=DeviceType.NVIDIA,
                   model_path="/m",
                   nodes=[node("s1", "10.0.0.1", tp=8)])
    assert errors(p) == []


# ------------------------------------------------------------ 进程分配
def test_assign_colocated_single_dp():
    p = plan_colocated_single()
    p.nodes[0].dp = 4
    p.nodes[0].tp = 2
    specs = assign_processes(p)["id-s1"]
    assert len(specs) == 4
    assert specs[0].device_list == "0,1"
    assert specs[3].device_list == "6,7"
    assert specs[2].server_port == 9002


def test_assign_pd():
    p = plan_pd()
    result = assign_processes(p)
    p1 = result["id-p1"]
    p2 = result["id-p2"]
    d1 = result["id-d1"]
    assert [s.dp_rank for s in p1] == [0, 1]
    assert [s.dp_rank for s in p2] == [2, 3]
    assert [s.mooncake_port for s in p1] == [20001, 20005]
    assert [s.mooncake_port for s in p2] == [20009, 20013]
    assert [s.mooncake_port for s in d1] == [20001, 20005]
    assert p1[1].device_list == "4,5,6,7"
    assert p1[0].server_port == 9000 and p1[1].server_port == 9001


def test_served_name_default():
    p = plan_colocated_single()
    assert p.effective_served_name() == "Qwen3-32B"
    p.served_model_name = "my-service"
    assert p.effective_served_name() == "my-service"
