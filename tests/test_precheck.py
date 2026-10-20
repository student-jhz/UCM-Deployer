# -*- coding: utf-8 -*-
"""环境预检（ucm-toolkit precheck）核心逻辑测试。"""
import re

import pytest

from ucm_deployer.core.models import CommandResult, ServerInfo
from ucm_deployer.core.precheck import (PrecheckError, PrecheckRunner,
                                        parse_precheck_output)
from ucm_deployer.core.ssh_client import SSHClient

# 用户实测的 precheck 输出样例（--no-color）
SAMPLE_OUTPUT = """UCM environment pre-check
──────────────────────────────────────────────────────────────────────────────
[INFO] serving_stack        vllm=0.26.0, vllm-ascend=0.26.0rc1
[INFO] uc_manager           -
[FAIL] accelerator_driver   -
        no nvidia-smi or npu-smi found (no GPU/NPU driver detected)
        fix: install the NVIDIA or Ascend driver so nvidia-smi/npu-smi is available on PATH
[PASS] kernel               5.10.0
        threshold: >= 5.10
[WARN] memory_shm           shm=0.06 GiB (0.06 GiB free)
        threshold: >= 512 GiB
        /dev/shm 0.1 GiB is below the 512 GiB UCM minimum
        fix: enlarge /dev/shm to >= 512 GiB (UCM KV-cache page cache needs it)
[INFO] aio_resources        max-nr=65536 nr=0 available=65536 max_aio_workers=16
[SKIP] bandwidth            -
        skipped: provide --mount-path (or mount_path in config) to run
        fix: set --mount-path to the UCM cache mount point
──────────────────────────────────────────────────────────────────────────────
Result: FAILED  |  1 pass, 1 warn, 1 fail, 1 skip, 3 info
"""


def make_ssh(fake_ssh) -> SSHClient:
    ssh = SSHClient(ServerInfo.create(name="s", host="127.0.0.1"))
    ssh.connect()
    return ssh


# ============================================================ 解析
def test_parse_sample_output():
    res = CommandResult(command="precheck", exit_code=1, stdout=SAMPLE_OUTPUT)
    result = parse_precheck_output(res)
    assert not result.ok
    assert len(result.items) == 7
    assert result.passed_count == 1
    assert result.failed_count == 1
    assert result.warn_count == 1
    assert "FAILED" in result.summary and "1 pass" in result.summary

    by_name = {i.name: i for i in result.items}
    assert by_name["kernel"].status == "PASS"
    assert by_name["kernel"].summary == "5.10.0"
    assert "threshold: >= 5.10" in by_name["kernel"].detail
    assert by_name["accelerator_driver"].status == "FAIL"
    assert "no nvidia-smi or npu-smi found" in by_name["accelerator_driver"].detail
    assert "fix:" in by_name["accelerator_driver"].detail
    assert by_name["memory_shm"].status == "WARN"
    assert by_name["bandwidth"].status == "SKIP"
    assert by_name["serving_stack"].status == "INFO"
    assert by_name["serving_stack"].summary == "vllm=0.26.0, vllm-ascend=0.26.0rc1"


def test_parse_ok_result():
    text = ("[PASS] kernel               5.10.0\n"
            "Result: PASSED  |  1 pass, 0 warn, 0 fail\n")
    result = parse_precheck_output(
        CommandResult(command="x", exit_code=0, stdout=text))
    assert result.ok
    assert result.items[0].status == "PASS"
    assert "PASSED" in result.summary


def test_parse_empty_output():
    result = parse_precheck_output(
        CommandResult(command="x", exit_code=1, stdout=""))
    assert not result.ok
    assert result.items == []
    assert "未产出可解析结果" in result.summary


def test_parse_multiline_detail():
    text = ("[WARN] memory_shm           shm=0.06 GiB\n"
            "        threshold: >= 512 GiB\n"
            "        fix: enlarge /dev/shm\n")
    result = parse_precheck_output(CommandResult(command="x", exit_code=1,
                                                 stdout=text))
    item = result.items[0]
    assert item.detail == "threshold: >= 512 GiB\nfix: enlarge /dev/shm"


# ============================================================ 执行
def test_run_env_only(fake_ssh):
    """无挂载目录：--skip-bandwidth，不创建/清理子目录。"""
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"ucm-toolkit run precheck",
                    stdout="[PASS] kernel  5.10.0\n")
    result = PrecheckRunner(ssh).run("ucm-ctr", mount_path="",
                                     skip_bandwidth=True)
    assert result.ok
    cmds = [c.executed_command for c in ssh._client.channels]
    assert any("ucm-toolkit run precheck --no-color --skip-bandwidth" in c
               for c in cmds)
    assert not any("mkdir -p" in c for c in cmds)


def test_run_with_mount_creates_and_cleans_subdir(fake_ssh):
    """指定挂载目录：在其下创建临时子目录执行带宽测试并清理。"""
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"mkdir -p ", stdout="")                       # 建子目录
    ssh._client.add(r"ucm-toolkit run precheck",
                    stdout="[PASS] kernel  5.10.0\n[WARN] bandwidth  7.0\n")
    ssh._client.add(r"rm -rf ", stdout="")                         # 清子目录
    result = PrecheckRunner(ssh).run("ucm-ctr", mount_path="/mnt/kvcache")
    assert result.ok
    assert result.warn_count == 1

    cmds = [c.executed_command for c in ssh._client.channels]
    mkdir = next(c for c in cmds if "mkdir -p" in c)
    assert "/mnt/kvcache/ucm-precheck-" in mkdir
    precheck = next(c for c in cmds if "ucm-toolkit run precheck" in c)
    m = re.search(r"--mount-path (\S+)", precheck)
    assert m and m.group(1).startswith("/mnt/kvcache/ucm-precheck-")
    rm = next(c for c in cmds if "rm -rf" in c)
    # 清理的正是创建的同一个子目录
    assert m.group(1) in rm and mkdir.split()[-1].strip("'\"") in rm


def test_run_cleans_subdir_even_on_failure(fake_ssh):
    """precheck 失败（退出码 1）也要清理子目录。"""
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"mkdir -p ", stdout="")
    ssh._client.add(r"ucm-toolkit run precheck", stdout="[FAIL] x  -\n",
                    exit_code=1)
    ssh._client.add(r"rm -rf ", stdout="")
    result = PrecheckRunner(ssh).run("ucm-ctr", mount_path="/mnt/kv")
    assert not result.ok
    cmds = [c.executed_command for c in ssh._client.channels]
    assert any("rm -rf" in c and "/mnt/kv/ucm-precheck-" in c for c in cmds)


def test_run_usage_error_raises(fake_ssh):
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"ucm-toolkit run precheck", stderr="usage error",
                    exit_code=2)
    with pytest.raises(PrecheckError, match="用法错误"):
        PrecheckRunner(ssh).run("ucm-ctr", mount_path="")


def test_run_mkdir_failure_raises(fake_ssh):
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"mkdir -p ", stderr="Permission denied", exit_code=1)
    with pytest.raises(PrecheckError, match="创建预检临时目录失败"):
        PrecheckRunner(ssh).run("ucm-ctr", mount_path="/mnt/kv")
