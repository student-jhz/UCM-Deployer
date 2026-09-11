# -*- coding: utf-8 -*-
import pytest

from ucm_deployer.core.command_generator import CommandGenerator
from ucm_deployer.core.docker_manager import DockerManager
from ucm_deployer.core.models import DeviceType, ServerInfo
from ucm_deployer.core.ssh_client import SSHClient
from ucm_deployer.core.topology import DeployMode, DeployPlan, NodePlan, NodeRole
from ucm_deployer.service import deploy_service


def node(name, host, role=NodeRole.MIXED, dp=1, tp=8):
    return NodePlan(server_id=f"id-{name}", server_name=name, host=host,
                    role=role, dp=dp, tp=tp, container=f"c-{name}", cards=8)


def make_plan():
    return DeployPlan(mode=DeployMode.COLOCATED, engine="vllm",
                      device_type=DeviceType.ASCEND,
                      model_path="/models/m", served_model_name="svc",
                      nodes=[node("s1", "10.0.0.1")])


@pytest.fixture
def env(fake_ssh):
    ssh = SSHClient(ServerInfo.create(name="s1", host="127.0.0.1"))
    ssh.connect()
    c = fake_ssh.instances[0]
    c.add(r"^docker exec .* /bin/bash -c ", stdout="")
    c.add(r"^docker cp ", stdout="")
    c.add(r"^rm -f ", stdout="")
    c.add(r"^curl ", stdout="200")
    return ssh, DockerManager(ssh), c


def test_ordered_scripts_order():
    p = make_plan()
    p.mode = DeployMode.PD
    p.nodes = [node("p1", "10.0.0.1", role=NodeRole.P),
               node("d1", "10.0.0.2", role=NodeRole.D)]
    ds = CommandGenerator(p).generate()
    roles = [s.role for s in deploy_service.ordered_scripts(ds)]
    assert roles.index("mooncake-master") < roles.index("prefill") < roles.index("load-balancer")
    assert roles.index("prefill") < roles.index("decode")


def test_scripts_by_server():
    p = make_plan()
    p.mode = DeployMode.PD
    p.nodes = [node("p1", "10.0.0.1", role=NodeRole.P),
               node("d1", "10.0.0.2", role=NodeRole.D)]
    ds = CommandGenerator(p).generate()
    grouped = deploy_service.scripts_by_server(ds)
    assert set(grouped) == {"id-p1", "id-d1"}
    assert all(s.server_id == sid for sid, lst in grouped.items() for s in lst)


def test_deploy_files(env):
    ssh, docker, fake = env
    p = make_plan()
    ds = CommandGenerator(p).generate()
    deploy_service.deploy_files(ssh, docker, "c-s1", ds.scripts + ds.config_files)
    # 临时文件写入后又被删除；docker cp 被调用
    cp_cmds = [c.executed_command for c in fake.channels
               if c.executed_command and c.executed_command.startswith("docker cp")]
    assert any("serve.sh" in cmd for cmd in cp_cmds)
    assert any("ucm_config.yaml" in cmd for cmd in cp_cmds)
    # mkdir 在容器内执行
    assert any(c.executed_command.startswith("docker exec c-s1")
               and "mkdir -p /root/ucm-deploy" in c.executed_command
               for c in fake.channels)


def test_launch_script(env):
    ssh, docker, fake = env
    p = make_plan()
    ds = CommandGenerator(p).generate()
    s = ds.scripts[0]
    deploy_service.launch_script(ssh, docker, "c-s1", s)
    cmd = fake.channels[-1].executed_command
    assert cmd.startswith("docker exec -d c-s1")
    assert "nohup bash /root/ucm-deploy/serve.sh" in cmd
    assert "/root/ucm-deploy/logs/serve.log" in cmd


def test_tail_log(env):
    ssh, docker, fake = env
    fake.add(r"docker exec c-s1 .*tail -n 200 ", stdout="line1\nline2\n")
    out = deploy_service.tail_log(ssh, docker, "c-s1", "/root/ucm-deploy/logs/serve.log")
    assert "line1" in out and "line2" in out


def test_health_check(env):
    ssh, docker, fake = env
    ok, msg = deploy_service.health_check(ssh, "http://127.0.0.1:9000/health")
    assert ok and "200" in msg
    fake.add(r"^curl ", stdout="000")
    ok, msg = deploy_service.health_check(ssh, "http://127.0.0.1:9000/health")
    assert not ok


def test_script_running(env):
    ssh, docker, fake = env
    p = make_plan()
    ds = CommandGenerator(p).generate()
    s = ds.scripts[0]
    fake.add(r"docker exec c-s1 .*pgrep -fc ", stdout="2\n")
    assert deploy_service.script_running(ssh, docker, "c-s1", s)
    fake.add(r"docker exec c-s1 .*pgrep -fc ", stdout="0\n")
    assert not deploy_service.script_running(ssh, docker, "c-s1", s)


def test_stop_script(env):
    ssh, docker, fake = env
    p = make_plan()
    ds = CommandGenerator(p).generate()
    s = ds.scripts[0]
    deploy_service.stop_script(ssh, docker, "c-s1", s)
    assert any("pkill -f" in c.executed_command for c in fake.channels)
