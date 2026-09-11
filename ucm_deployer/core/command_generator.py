# -*- coding: utf-8 -*-
"""vLLM / SGLang + UCM 启动脚本自动生成。

生成物：
- NodeScript       每台服务器容器内要执行的 bash 脚本 / 配置文件
- DeploymentScripts 一次部署的全部脚本（scripts 按依赖顺序排列）

模板来源（与官方文档保持一致）：
- UCM Quickstart vLLM-Ascend（混部 UCMConnector kv_both）
- UCM Distributed PD Disaggregation（MultiConnector/MooncakeConnectorV1）
- vLLM-Ascend 多机混部教程（ray 集群）
- UCM Quickstart SGLang（hicache UCM 集成）
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List, Optional

from ..utils.log import get_logger
from .models import DeviceType
from .topology import (
    DPProcessSpec,
    DeployMode,
    DeployPlan,
    NodePlan,
    NodeRole,
    assign_processes,
)

logger = get_logger(__name__)

REMOTE_DIR = "/root/ucm-deploy"
LOG_DIR = REMOTE_DIR + "/logs"
VLLM_UCM_MODULE = "ucm.integration.vllm.ucm_connector"
MOONCAKE_PORT_TOKEN = "__MOONCAKE_PORT__"

LB_SCRIPT_BY_PLATFORM = {
    DeviceType.ASCEND: "/vllm-workspace/vllm-ascend/examples/disaggregated_prefill_v1/load_balance_proxy_server_example.py",
    DeviceType.NVIDIA: "/vllm-workspace/vllm/examples/online_serving/disaggregated_prefill_v1/load_balance_proxy_server_example.py",
}


# ============================================================ kv-transfer-config
def _pretty_json(cfg: dict) -> str:
    text = json.dumps(cfg, indent=4, ensure_ascii=False)
    return text.replace(f'"{MOONCAKE_PORT_TOKEN}"', '\'"$mooncake_port"\'')


def kv_config_colocated(ucm_config_file: str) -> str:
    """混部：UCMConnector kv_both（单机/多机混部通用）。"""
    cfg = {
        "kv_connector": "UCMConnector",
        "kv_connector_module_path": VLLM_UCM_MODULE,
        "kv_role": "kv_both",
        "kv_connector_extra_config": {"UCM_CONFIG_FILE": ucm_config_file},
    }
    return _pretty_json(cfg)


def kv_config_producer(dp_p: int, tp_p: int, dp_d: int, tp_d: int,
                       ucm_config_file: str) -> str:
    """P 节点：MultiConnector = Mooncake(producer) + UCM(kv_both)。"""
    cfg = {
        "kv_connector": "MultiConnector",
        "kv_role": "kv_producer",
        "kv_connector_extra_config": {
            "connectors": [
                {
                    "kv_connector": "MooncakeConnectorV1",
                    "kv_role": "kv_producer",
                    "kv_port": MOONCAKE_PORT_TOKEN,
                    "kv_connector_extra_config": {
                        "prefill": {"dp_size": dp_p, "tp_size": tp_p},
                        "decode": {"dp_size": dp_d, "tp_size": tp_d},
                    },
                },
                {
                    "kv_connector": "UCMConnector",
                    "kv_connector_module_path": VLLM_UCM_MODULE,
                    "kv_role": "kv_both",
                    "kv_connector_extra_config": {"UCM_CONFIG_FILE": ucm_config_file},
                },
            ]
        },
    }
    return _pretty_json(cfg)


def kv_config_consumer(dp_p: int, tp_p: int, dp_d: int, tp_d: int) -> str:
    """D 节点：MooncakeConnectorV1 kv_consumer。"""
    cfg = {
        "kv_connector": "MooncakeConnectorV1",
        "kv_role": "kv_consumer",
        "kv_port": MOONCAKE_PORT_TOKEN,
        "kv_connector_extra_config": {
            "prefill": {"dp_size": dp_p, "tp_size": tp_p},
            "decode": {"dp_size": dp_d, "tp_size": tp_d},
        },
    }
    return _pretty_json(cfg)


# ============================================================ 数据结构
@dataclass
class NodeScript:
    server_id: str
    server_name: str
    container: str
    name: str            # 文件名（不含路径）
    role: str            # serve|serve_dp|prefill|decode|ray-head|ray-worker|mooncake-master|load-balancer|config
    path: str            # 容器内绝对路径
    log_path: str        # 日志文件路径
    content: str
    ports: List[int] = field(default_factory=list)   # 对外服务端口
    stop_command: str = ""

    @property
    def is_config(self) -> bool:
        return self.role == "config"


@dataclass
class HealthCheck:
    server_id: str
    server_name: str
    url: str
    label: str


@dataclass
class DeploymentScripts:
    plan: DeployPlan
    scripts: List[NodeScript] = field(default_factory=list)
    config_files: List[NodeScript] = field(default_factory=list)
    health_checks: List[HealthCheck] = field(default_factory=list)
    summary: str = ""


# ============================================================ 生成器
class CommandGenerator:
    def __init__(self, plan: DeployPlan):
        self.plan = plan

    # ------------------------------------------------------------
    def generate(self) -> DeploymentScripts:
        if self.plan.engine == "sglang":
            return self._generate_sglang()
        return self._generate_vllm()

    # ------------------------------------------------------------ vLLM
    def _generate_vllm(self) -> DeploymentScripts:
        plan = self.plan
        ds = DeploymentScripts(plan=plan)
        if plan.mode == DeployMode.PD:
            self._gen_pd(ds)
        elif plan.is_multinode:
            self._gen_colocated_ray(ds)
        else:
            self._gen_colocated_single(ds)
        ds.summary = self._summary()
        return ds

    def _summary(self) -> str:
        plan = self.plan
        if plan.mode == DeployMode.PD:
            p, d = plan.p_nodes, plan.d_nodes
            return (f"PD 分离: {len(p)} P 节点(DP{self._group_dp(p)}/TP{p[0].tp if p else '-'}) + "
                    f"{len(d)} D 节点(DP{self._group_dp(d)}/TP{d[0].tp if d else '-'})")
        if plan.is_multinode:
            return f"多机混部(ray): {len(plan.nodes)} 节点 TP={plan.nodes[0].tp} PP={plan.pp}"
        n = plan.nodes[0]
        return f"单机混部: DP={n.dp} TP={n.tp} (共 {n.used_cards} 卡)"

    @staticmethod
    def _group_dp(nodes: List[NodePlan]) -> int:
        return sum(n.dp for n in nodes)

    # ------------------------------------------------------------ 单机混部
    def _gen_colocated_single(self, ds: DeploymentScripts) -> None:
        plan = self.plan
        node = plan.nodes[0]
        ucm_cfg = plan.ucm_config_file or f"{REMOTE_DIR}/ucm_config.yaml"
        kv = kv_config_colocated(ucm_cfg)

        # UCM 配置模板
        ds.config_files.append(self._ucm_config_script(node, ucm_cfg))

        if node.dp == 1:
            content = self._bash_header(f"vLLM + UCM 混部(单机)", node)
            content += self._env_common()
            content += f"""
vllm serve {self._q(plan.model_path)} \\
    --host 0.0.0.0 \\
    --port {plan.server_port} \\
    --tensor-parallel-size {node.tp} \\
    --served-model-name {self._q(plan.effective_served_name())} \\
{self._extra_args_block()}    --kv-transfer-config \\
'{kv}'
"""
            ds.scripts.append(NodeScript(
                server_id=node.server_id, server_name=node.server_name,
                container=node.container, name="serve.sh", role="serve",
                path=f"{REMOTE_DIR}/serve.sh", log_path=f"{LOG_DIR}/serve.log",
                content=content, ports=[plan.server_port],
                stop_command="pkill -f 'vllm serve' || true"))
        else:
            content = self._dp_loop_script(
                node=node, role_label="混部(DP外置负载均衡)",
                dp_global=node.dp, dp_rank_start=0, dp_master=node.host,
                kv=kv, with_mooncake=False)
            ds.scripts.append(NodeScript(
                server_id=node.server_id, server_name=node.server_name,
                container=node.container, name="serve_dp.sh", role="serve_dp",
                path=f"{REMOTE_DIR}/serve_dp.sh", log_path=f"{LOG_DIR}/serve_dp.log",
                content=content,
                ports=[plan.server_port + i for i in range(node.dp)],
                stop_command="pkill -f 'vllm serve' || true"))

        ds.health_checks.append(HealthCheck(
            node.server_id, node.server_name,
            f"http://127.0.0.1:{plan.server_port}/health", "vLLM 服务"))

    # ------------------------------------------------------------ 多机混部 (ray)
    def _gen_colocated_ray(self, ds: DeploymentScripts) -> None:
        plan = self.plan
        head = plan.head_node
        ucm_cfg = plan.ucm_config_file or f"{REMOTE_DIR}/ucm_config.yaml"
        kv = kv_config_colocated(ucm_cfg)
        ds.config_files.append(self._ucm_config_script(head, ucm_cfg))

        head_content = self._bash_header("ray 集群 Head 节点", head)
        head_content += f"""
ray start --head --port {plan.ray_port} --dashboard-host 0.0.0.0
"""
        ds.scripts.append(NodeScript(
            server_id=head.server_id, server_name=head.server_name,
            container=head.container, name="ray_head.sh", role="ray-head",
            path=f"{REMOTE_DIR}/ray_head.sh", log_path=f"{LOG_DIR}/ray_head.log",
            content=head_content, stop_command="ray stop || true"))

        idx = 0
        for node in plan.nodes:
            if node.server_id == head.server_id:
                continue
            idx += 1
            worker = self._bash_header(f"ray 集群 Worker 节点 {idx}", node)
            worker += f"""
ray start --address {head.host}:{plan.ray_port}
"""
            ds.scripts.append(NodeScript(
                server_id=node.server_id, server_name=node.server_name,
                container=node.container, name=f"ray_worker_{idx}.sh", role="ray-worker",
                path=f"{REMOTE_DIR}/ray_worker_{idx}.sh",
                log_path=f"{LOG_DIR}/ray_worker_{idx}.log",
                content=worker, stop_command="ray stop || true"))

        tp = plan.nodes[0].tp
        serve = self._bash_header("vLLM + UCM 多机混部(在 Head 节点执行)", head)
        serve += self._env_common()
        pp_part = f"    --pipeline-parallel-size {plan.pp} \\\n" if plan.pp > 1 else ""
        serve += f"""
vllm serve {self._q(plan.model_path)} \\
    --host 0.0.0.0 \\
    --port {plan.server_port} \\
    --tensor-parallel-size {tp} \\
{pp_part}    --served-model-name {self._q(plan.effective_served_name())} \\
{self._extra_args_block()}    --kv-transfer-config \\
'{kv}'
"""
        ds.scripts.append(NodeScript(
            server_id=head.server_id, server_name=head.server_name,
            container=head.container, name="serve.sh", role="serve",
            path=f"{REMOTE_DIR}/serve.sh", log_path=f"{LOG_DIR}/serve.log",
            content=serve, ports=[plan.server_port],
            stop_command="pkill -f 'vllm serve' || true; ray stop || true"))
        ds.health_checks.append(HealthCheck(
            head.server_id, head.server_name,
            f"http://127.0.0.1:{plan.server_port}/health", "vLLM 服务"))

    # ------------------------------------------------------------ PD 分离
    def _gen_pd(self, ds: DeploymentScripts) -> None:
        plan = self.plan
        p_nodes, d_nodes = plan.p_nodes, plan.d_nodes
        if not p_nodes or not d_nodes:
            return
        dp_p, tp_p = plan.group_dp(NodeRole.P), p_nodes[0].tp
        dp_d, tp_d = plan.group_dp(NodeRole.D), d_nodes[0].tp
        ucm_cfg = plan.ucm_config_file or f"{REMOTE_DIR}/ucm_config.yaml"
        master = p_nodes[0]

        # ---- mooncake master（在第一个 P 节点上）
        mc = self._bash_header("Mooncake Master 服务", master)
        mc += """
export LD_LIBRARY_PATH=/usr/local/lib:$LD_LIBRARY_PATH
mooncake_master --port {master_port} \\
    --eviction_high_watermark_ratio 0.9 \\
    --eviction_ratio 0.1 \\
    --default_kv_lease_ttl 11000
""".format(master_port=plan.mooncake_master_port)
        ds.scripts.append(NodeScript(
            server_id=master.server_id, server_name=master.server_name,
            container=master.container, name="mooncake_master.sh",
            role="mooncake-master", path=f"{REMOTE_DIR}/mooncake_master.sh",
            log_path=f"{LOG_DIR}/mooncake_master.log", content=mc,
            ports=[plan.mooncake_master_port],
            stop_command="pkill -f mooncake_master || true"))

        # ---- mooncake.json（每个节点）
        protocol = "ascend" if plan.device_type == DeviceType.ASCEND else "tcp"
        mooncake_json = json.dumps({
            "metadata_server": "P2PHANDSHAKE",
            "protocol": protocol,
            "device_name": "",
            "master_server_address": f"{master.host}:{plan.mooncake_master_port}",
            "global_segment_size": "1GB",
        }, indent=4)
        for node in plan.nodes:
            ds.config_files.append(NodeScript(
                server_id=node.server_id, server_name=node.server_name,
                container=node.container, name="mooncake.json", role="config",
                path=f"{REMOTE_DIR}/mooncake.json", log_path="",
                content=mooncake_json + "\n"))

        # ---- UCM 配置模板（P 节点）
        ds.config_files.append(self._ucm_config_script(master, ucm_cfg))

        # ---- P/D 节点启动脚本
        kv_p = kv_config_producer(dp_p, tp_p, dp_d, tp_d, ucm_cfg)
        kv_d = kv_config_consumer(dp_p, tp_p, dp_d, tp_d)
        dp_master_p = p_nodes[0].host
        dp_master_d = d_nodes[0].host

        rank = 0
        for node in p_nodes:
            content = self._dp_loop_script(
                node=node, role_label="PD 分离 Prefill(P) 节点",
                dp_global=dp_p, dp_rank_start=rank, dp_master=dp_master_p,
                kv=kv_p, with_mooncake=True)
            ds.scripts.append(NodeScript(
                server_id=node.server_id, server_name=node.server_name,
                container=node.container, name="prefill.sh", role="prefill",
                path=f"{REMOTE_DIR}/prefill.sh", log_path=f"{LOG_DIR}/prefill.log",
                content=content,
                ports=[plan.server_port + i for i in range(node.dp)],
                stop_command="pkill -f 'vllm serve' || true"))
            rank += node.dp

        rank = 0
        for node in d_nodes:
            content = self._dp_loop_script(
                node=node, role_label="PD 分离 Decode(D) 节点",
                dp_global=dp_d, dp_rank_start=rank, dp_master=dp_master_d,
                kv=kv_d, with_mooncake=True)
            ds.scripts.append(NodeScript(
                server_id=node.server_id, server_name=node.server_name,
                container=node.container, name="decode.sh", role="decode",
                path=f"{REMOTE_DIR}/decode.sh", log_path=f"{LOG_DIR}/decode.log",
                content=content,
                ports=[plan.server_port + i for i in range(node.dp)],
                stop_command="pkill -f 'vllm serve' || true"))
            rank += node.dp

        # ---- 负载均衡（在第一个 P 节点）
        prefiller_hosts, prefiller_ports = [], []
        for node in p_nodes:
            prefiller_hosts += [node.host] * node.dp
            prefiller_ports += [plan.server_port + i for i in range(node.dp)]
        decoder_hosts, decoder_ports = [], []
        for node in d_nodes:
            decoder_hosts += [node.host] * node.dp
            decoder_ports += [plan.server_port + i for i in range(node.dp)]
        lb_path = plan.lb_script_path or LB_SCRIPT_BY_PLATFORM.get(
            plan.device_type, LB_SCRIPT_BY_PLATFORM[DeviceType.NVIDIA])
        lb = self._bash_header("PD 负载均衡代理", master)
        lb += f"""
python {lb_path} \\
    --port {plan.lb_port} \\
    --host 0.0.0.0 \\
    --prefiller-hosts {' '.join(prefiller_hosts)} \\
    --prefiller-ports {' '.join(str(p) for p in prefiller_ports)} \\
    --decoder-hosts {' '.join(decoder_hosts)} \\
    --decoder-ports {' '.join(str(p) for p in decoder_ports)}
"""
        ds.scripts.append(NodeScript(
            server_id=master.server_id, server_name=master.server_name,
            container=master.container, name="load_balancer.sh",
            role="load-balancer", path=f"{REMOTE_DIR}/load_balancer.sh",
            log_path=f"{LOG_DIR}/load_balancer.log", content=lb,
            ports=[plan.lb_port],
            stop_command="pkill -f load_balance_proxy_server || true"))

        # ---- 健康检查
        for node in p_nodes:
            for i in range(node.dp):
                ds.health_checks.append(HealthCheck(
                    node.server_id, node.server_name,
                    f"http://127.0.0.1:{plan.server_port + i}/health",
                    f"P({node.server_name}) dp{i}"))
        for node in d_nodes:
            for i in range(node.dp):
                ds.health_checks.append(HealthCheck(
                    node.server_id, node.server_name,
                    f"http://127.0.0.1:{plan.server_port + i}/health",
                    f"D({node.server_name}) dp{i}"))
        ds.health_checks.append(HealthCheck(
            master.server_id, master.server_name,
            f"http://127.0.0.1:{plan.lb_port}/health", "负载均衡"))

    # ------------------------------------------------------------ SGLang
    def _generate_sglang(self) -> DeploymentScripts:
        plan = self.plan
        node = plan.nodes[0]
        ds = DeploymentScripts(plan=plan)
        kv_dir = plan.kv_cache_dir or "/mnt/ucm-storage"
        hicache = json.dumps({
            "backend_name": "unifiedcache",
            "module_path": "ucm.integration.sglang.unifiedcache_store",
            "class_name": "UnifiedCacheStore",
            "interface_v1": 1,
            "kv_connector_extra_config": {
                "ucm_connector_name": "UcmPipelineStore",
                "ucm_connector_config": {"storage_backends": kv_dir},
            },
        }, indent=2)
        content = self._bash_header("SGLang + UCM 混部(单机)", node)
        content += self._env_common()
        content += f"""
HICACHE_CONFIG='{hicache}'

python3 -m sglang.launch_server \\
    --model-path {self._q(plan.model_path)} \\
    --tensor-parallel-size {node.tp} \\
    --data-parallel-size {node.dp} \\
    --page-size 128 \\
    --port {plan.server_port} \\
    --served-model-name {self._q(plan.effective_served_name())} \\
    --trust-remote-code \\
    --enable-hierarchical-cache \\
    --hicache-mem-layout page_first \\
    --hicache-write-policy write_through \\
    --hicache-storage-backend dynamic \\
    --hicache-storage-prefetch-policy wait_complete \\
    --hicache-storage-backend-extra-config "$HICACHE_CONFIG" \\
{self._extra_args_block()}    > {LOG_DIR}/sglang_detail.log 2>&1
"""
        ds.scripts.append(NodeScript(
            server_id=node.server_id, server_name=node.server_name,
            container=node.container, name="serve.sh", role="serve",
            path=f"{REMOTE_DIR}/serve.sh", log_path=f"{LOG_DIR}/serve.log",
            content=content, ports=[plan.server_port],
            stop_command="pkill -f sglang.launch_server || true"))
        ds.health_checks.append(HealthCheck(
            node.server_id, node.server_name,
            f"http://127.0.0.1:{plan.server_port}/health", "SGLang 服务"))
        ds.summary = f"SGLang 单机混部: DP={node.dp} TP={node.tp}"
        return ds

    # ============================================================ 片段生成
    @staticmethod
    def _q(text: str) -> str:
        from .shell import shq

        return shq(text)

    def _bash_header(self, title: str, node: NodePlan) -> str:
        plan = self.plan
        return f"""#!/bin/bash
# ============================================================
# {title} - 由 UCM Deployer 生成
# 服务器: {node.server_name} ({node.host})   容器: {node.container}
# 引擎: {plan.engine}   模型: {plan.model_path}
# ============================================================
mkdir -p {LOG_DIR}
"""

    def _env_common(self) -> str:
        plan = self.plan
        lines = ["export PYTHONHASHSEED=0"]
        if plan.device_type == DeviceType.ASCEND:
            lines.append(
                "export LD_LIBRARY_PATH=/usr/local/lib:"
                "/usr/local/Ascend/ascend-toolkit/latest/python/site-packages:$LD_LIBRARY_PATH")
            lines.append("export VLLM_USE_MODELSCOPE=True")
        if plan.enable_ucm_patch:
            lines.append("# UCM monkey patch (vLLM >= 0.11)")
            lines.append("export ENABLE_UCM_PATCH=1")
        return "\n".join(lines) + "\n"

    def _extra_args_block(self) -> str:
        parts = []
        for line in self.plan.extra_args.splitlines():
            line = line.strip().rstrip("\\").strip()
            if line:
                parts.append(f"    {line} \\")
        return "\n".join(parts) + ("\n" if parts else "")

    def _ucm_config_script(self, node: NodePlan, ucm_cfg_path: str) -> NodeScript:
        kv_dir = self.plan.kv_cache_dir or "/mnt/ucm-storage"
        content = f"""# UCM 配置模板 - 由 UCM Deployer 生成（请按需修改）
# 参考: unified-cache-management/examples/ucm_config_example.yaml
ucm_connectors:
  - ucm_connector_name: "UcmPipelineStore"
    ucm_connector_config:
      store_pipeline: "Cache|Posix"
      storage_backends: "{kv_dir}"
      cache_buffer_capacity_gb: 64
enable_event_sync: true
use_layerwise: true
"""
        return NodeScript(
            server_id=node.server_id, server_name=node.server_name,
            container=node.container, name="ucm_config.yaml", role="config",
            path=ucm_cfg_path, log_path="", content=content)

    def _dp_loop_script(self, node: NodePlan, role_label: str,
                        dp_global: int, dp_rank_start: int, dp_master: str,
                        kv: str, with_mooncake: bool) -> str:
        """生成「本机循环启动 dp 个引擎进程」的脚本（对应官方 run_multi_dp.sh 模式）。"""
        plan = self.plan
        nic = plan.nic_name or "auto_detect"
        tp = node.tp
        mooncake_lines = ""
        if with_mooncake:
            mooncake_lines = f"""MOONCAKE_PORT_START={plan.mooncake_port + dp_rank_start * tp}
export MOONCAKE_CONFIG_PATH={REMOTE_DIR}/mooncake.json
"""
        device_export = (
            "    export ASCEND_RT_VISIBLE_DEVICES=$device_list"
            if plan.device_type == DeviceType.ASCEND
            else "    export CUDA_VISIBLE_DEVICES=$device_list")

        return f"""{self._bash_header(role_label, node)}
# 本机 DP 进程数={node.dp}  TP={tp}  组内全局 DP={dp_global}
# 说明: 第 i 个进程使用卡段 [i*TP, (i+1)*TP)，端口 SERVER_PORT_START+i，
#       mooncake 端口 MOONCAKE_PORT_START+i*TP（与官方文档一致）
{self._env_common()}
NIC="{nic}"
LOCAL_IP={node.host}
TP={tp}
DP_LOCAL={node.dp}
DP_GLOBAL={dp_global}
DP_RANK_START={dp_rank_start}
DP_ADDRESS={dp_master}
DP_RPC_PORT={plan.dp_rpc_port}
SERVER_PORT_START={plan.server_port}
{mooncake_lines}
# 网卡自动探测（如需固定请直接修改 NIC）
if [ "$NIC" = "auto_detect" ]; then
    NIC=$(ip -o -4 route show to {dp_master} 2>/dev/null | awk '{{print $5}}' | head -1)
    [ -z "$NIC" ] && NIC=$(ip -o -4 route show default 2>/dev/null | awk '{{print $5}}' | head -1)
    [ -z "$NIC" ] && NIC=eth0
fi
export HCCL_IF_IP=$LOCAL_IP
export GLOO_SOCKET_IFNAME=$NIC
export TP_SOCKET_IFNAME=$NIC
export HCCL_SOCKET_IFNAME=$NIC
export OMP_PROC_BIND=false
export OMP_NUM_THREADS=10
export HCCL_BUFFSIZE=256

pids=()
for ((i=0; i<DP_LOCAL; i++)); do
  dp_rank=$((DP_RANK_START + i))
  server_port=$((SERVER_PORT_START + i))
  mooncake_port=$((MOONCAKE_PORT_START + i*TP))
  start_card=$((i * TP))
  device_list=$(seq -s, $start_card $((start_card + TP - 1)))
  echo "[{node.server_name}] 启动 DP rank $dp_rank: port=$server_port devices=$device_list"
  (
{device_export}
    vllm serve {self._q(plan.model_path)} \\
      --host 0.0.0.0 \\
      --port $server_port \\
      --data-parallel-size $DP_GLOBAL \\
      --data-parallel-address $DP_ADDRESS \\
      --data-parallel-rpc-port $DP_RPC_PORT \\
      --data-parallel-rank $dp_rank \\
      --tensor-parallel-size $TP \\
      --served-model-name {self._q(plan.effective_served_name())} \\
{self._extra_args_block()}      --kv-transfer-config \\
'{kv}'
  ) 2>&1 | tee "{LOG_DIR}/engine_dp_$dp_rank.log" &
  pids+=($!)
done

echo "[{node.server_name}] 全部 $DP_LOCAL 个进程已启动，等待退出..."
for pid in "${{pids[@]}}"; do
  wait "$pid"
done
"""
