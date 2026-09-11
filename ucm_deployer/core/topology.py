# -*- coding: utf-8 -*-
"""部署拓扑（PD 混部 / PD 分离）的建模、校验与进程分配。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from .models import DeviceInfo, DeviceType


class DeployMode(str, Enum):
    COLOCATED = "colocated"   # PD 混部（不区分 P/D）
    PD = "pd"                 # PD 分离


class NodeRole(str, Enum):
    MIXED = "mixed"           # 混部节点
    P = "P"                   # Prefill 节点
    D = "D"                   # Decode 节点


@dataclass
class NodePlan:
    server_id: str
    server_name: str
    host: str                 # 服务器内网 IP（ray/mooncake 通信地址）
    role: NodeRole = NodeRole.MIXED
    dp: int = 1               # 本节点 DP 进程数
    tp: int = 1               # 每进程 TP 卡数
    container: str = ""       # 容器名
    cards: int = 0            # 可用卡数（设备探测结果，0=未知）

    @property
    def used_cards(self) -> int:
        return self.dp * self.tp

    @property
    def role_display(self) -> str:
        return {"mixed": "混部", "P": "P(Prefill)", "D": "D(Decode)"}[self.role.value]


@dataclass
class DeployPlan:
    mode: DeployMode = DeployMode.COLOCATED
    engine: str = "vllm"                     # vllm | sglang
    device_type: DeviceType = DeviceType.UNKNOWN
    nodes: List[NodePlan] = field(default_factory=list)

    # 模型与服务
    model_path: str = ""                     # 容器内模型路径
    served_model_name: str = ""
    ucm_config_file: str = ""                # 容器内 UCM 配置路径
    kv_cache_dir: str = ""                   # kvcache 共享目录（写入 UCM 配置模板）
    enable_ucm_patch: bool = True            # vLLM>=0.11 monkey patch
    pp: int = 1                              # 流水线并行（多机混部 ray 用）

    # 端口规划
    server_port: int = 9000
    mooncake_port: int = 20001
    dp_rpc_port: int = 13395
    mooncake_master_port: int = 50088
    lb_port: int = 7850
    ray_port: int = 6379

    # 网络
    nic_name: str = ""                       # 空=运行时自动探测

    # 其他
    extra_args: str = ""                     # 用户附加引擎参数（多行原样追加）
    lb_script_path: str = ""                 # 负载均衡脚本路径（空=按平台默认）

    # ------------------------------------------------------------ 分组
    def nodes_by_role(self, role: NodeRole) -> List[NodePlan]:
        return [n for n in self.nodes if n.role == role]

    @property
    def p_nodes(self) -> List[NodePlan]:
        return self.nodes_by_role(NodeRole.P)

    @property
    def d_nodes(self) -> List[NodePlan]:
        return self.nodes_by_role(NodeRole.D)

    @property
    def head_node(self) -> Optional[NodePlan]:
        """ray head（混部）或 mooncake master（PD）所在节点。"""
        if not self.nodes:
            return None
        if self.mode == DeployMode.PD:
            return self.p_nodes[0] if self.p_nodes else self.nodes[0]
        return self.nodes[0]

    @property
    def is_multinode(self) -> bool:
        return len({n.server_id for n in self.nodes}) > 1

    def effective_served_name(self) -> str:
        if self.served_model_name.strip():
            return self.served_model_name.strip()
        base = self.model_path.rstrip("/").split("/")[-1] if self.model_path else ""
        return base or "default-model"

    def group_dp(self, role: NodeRole) -> int:
        return sum(n.dp for n in self.nodes_by_role(role))


@dataclass
class ValidationIssue:
    level: str      # "error" | "warning"
    message: str

    @property
    def is_error(self) -> bool:
        return self.level == "error"


def validate_plan(plan: DeployPlan) -> List[ValidationIssue]:
    """校验部署计划，返回问题列表（error 必须解决，warning 提示）。"""
    issues: List[ValidationIssue] = []

    if not plan.nodes:
        issues.append(ValidationIssue("error", "未选择任何部署节点"))
        return issues
    if not plan.model_path.strip():
        issues.append(ValidationIssue("error", "未指定模型路径（容器内路径）"))
    for n in plan.nodes:
        if not n.container.strip():
            issues.append(ValidationIssue("error", f"服务器 {n.server_name} 未选择容器"))
        if not n.host.strip():
            issues.append(ValidationIssue("error", f"服务器 {n.server_name} 缺少内网 IP"))

    # 卡数基础校验
    for n in plan.nodes:
        if n.dp < 1 or n.tp < 1:
            issues.append(ValidationIssue("error", f"{n.server_name}: DP/TP 必须 >= 1"))
        if n.cards > 0 and n.used_cards > n.cards:
            issues.append(ValidationIssue(
                "error", f"{n.server_name}: 需要卡数 DP*TP={n.used_cards} 超过可用卡数 {n.cards}"))

    if plan.engine not in ("vllm", "sglang"):
        issues.append(ValidationIssue("error", f"不支持的引擎: {plan.engine}"))

    if plan.engine == "sglang":
        if plan.mode == DeployMode.PD:
            issues.append(ValidationIssue(
                "error", "SGLang 暂不支持 PD 分离模式自动生成（UCM 的 PD 方案基于 vLLM），"
                         "请使用 vLLM 引擎或手动编写脚本"))
        if plan.is_multinode:
            issues.append(ValidationIssue(
                "error", "SGLang 多机部署暂不支持自动生成，请参考 SGLang 多机文档手动配置"))
        return issues

    if plan.mode == DeployMode.COLOCATED:
        for n in plan.nodes:
            if n.role != NodeRole.MIXED:
                issues.append(ValidationIssue(
                    "error", f"混部模式下节点 {n.server_name} 的角色应为「混部」"))
        if plan.is_multinode:
            if any(n.dp > 1 for n in plan.nodes):
                issues.append(ValidationIssue(
                    "error", "多机混部(ray)模式下暂不支持 DP>1（多机 DP 请使用 PD 分离模式或手动编辑脚本）"))
            tps = {n.tp for n in plan.nodes}
            if len(tps) > 1:
                issues.append(ValidationIssue(
                    "error", "多机混部各节点 TP 必须一致: " +
                             ", ".join(f"{n.server_name}(tp={n.tp})" for n in plan.nodes)))
            total_cards = sum(n.cards for n in plan.nodes if n.cards > 0)
            known = sum(1 for n in plan.nodes if n.cards > 0)
            tp_pp = plan.nodes[0].tp * plan.pp
            if known == len(plan.nodes) and tp_pp > total_cards:
                issues.append(ValidationIssue(
                    "error", f"多机混部 TP*PP={tp_pp} 超过总卡数 {total_cards}"))
            elif known == len(plan.nodes) and tp_pp % len(plan.nodes) != 0:
                issues.append(ValidationIssue(
                    "error", f"多机混部 TP*PP={tp_pp} 无法在 {len(plan.nodes)} 台服务器间均分"))
    else:  # PD
        p_nodes, d_nodes = plan.p_nodes, plan.d_nodes
        if not p_nodes:
            issues.append(ValidationIssue("error", "PD 分离模式至少需要 1 个 P(Prefill) 节点"))
        if not d_nodes:
            issues.append(ValidationIssue("error", "PD 分离模式至少需要 1 个 D(Decode) 节点"))
        for role, nodes in (("P", p_nodes), ("D", d_nodes)):
            tps = {n.tp for n in nodes}
            if len(tps) > 1:
                issues.append(ValidationIssue(
                    "error", f"{role} 组内各节点 TP 必须一致: " +
                             ", ".join(f"{n.server_name}(tp={n.tp})" for n in nodes)))
        if p_nodes and d_nodes and p_nodes[0].tp != d_nodes[0].tp:
            issues.append(ValidationIssue(
                "warning", "P/D 两组 TP 不一致：mooncake 端口步进按 P 侧 TP 计算，"
                           "请确认与引擎文档一致"))
        p_and_d_same_server = {n.server_id for n in p_nodes} & {n.server_id for n in d_nodes}
        if p_and_d_same_server:
            issues.append(ValidationIssue(
                "warning", "同一服务器上同时部署 P 与 D 实例，请确认资源与端口不冲突"))

    if not plan.nic_name.strip() and plan.device_type == DeviceType.ASCEND:
        issues.append(ValidationIssue("warning", "未指定通信网卡，将自动探测（多网卡环境建议手动指定）"))

    if any(n.cards <= 0 for n in plan.nodes):
        issues.append(ValidationIssue("warning", "部分服务器卡数未知，已跳过卡数上限校验"))
    return issues


@dataclass
class DPProcessSpec:
    """一个 DP 引擎进程的启动规格。"""

    node: NodePlan
    dp_rank: int            # 组内全局 rank
    dp_rank_local: int      # 节点内序号
    server_port: int
    mooncake_port: int      # PD 模式
    device_list: str        # "0,1,2,3"


def _devices(start: int, count: int) -> str:
    return ",".join(str(i) for i in range(start, start + count))


def assign_processes(plan: DeployPlan) -> Dict[str, List[DPProcessSpec]]:
    """为每台服务器计算 DP 进程分配（端口/卡段/全局rank）。

    返回 {server_id: [DPProcessSpec]}；无 DP 拆分的节点不在结果中。
    """
    result: Dict[str, List[DPProcessSpec]] = {}

    if plan.mode == DeployMode.COLOCATED:
        if len(plan.nodes) == 1:
            node = plan.nodes[0]
            if node.dp > 1:
                result[node.server_id] = [
                    DPProcessSpec(node=node, dp_rank=i, dp_rank_local=i,
                                  server_port=plan.server_port + i,
                                  mooncake_port=0,
                                  device_list=_devices(i * node.tp, node.tp))
                    for i in range(node.dp)
                ]
        return result

    # PD 模式
    tp_p = plan.p_nodes[0].tp if plan.p_nodes else 1
    for group in (plan.p_nodes, plan.d_nodes):
        rank = 0
        for node in group:
            specs = []
            for i in range(node.dp):
                specs.append(DPProcessSpec(
                    node=node,
                    dp_rank=rank + i,
                    dp_rank_local=i,
                    server_port=plan.server_port + i,
                    # mooncake 端口步进与 UCM 分布式 PD 文档一致: base + rank*TP
                    mooncake_port=plan.mooncake_port + (rank + i) * tp_p,
                    device_list=_devices(i * node.tp, node.tp),
                ))
            result[node.server_id] = specs
            rank += node.dp
    return result
