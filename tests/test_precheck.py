# -*- coding: utf-8 -*-
"""环境预检（ucm-toolkit precheck）核心逻辑测试。"""
import re

import pytest

from ucm_deployer.core.models import CommandResult, ServerInfo, UCMInfo
from ucm_deployer.core.precheck import (PrecheckError, PrecheckFacts,
                                        PrecheckRunner,
                                        enhance_precheck_result,
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

A5_UNKNOWN_OUTPUT = """UCM environment pre-check
[INFO] serving_stack        vllm=0.26.0, vllm-ascend=0.26.0rc1
[INFO] uc_manager           -
[WARN] accelerator_driver   HDK=?
        threshold: HDK > 25.2.0
        could not extract an HDK/driver version; heuristic parse found: none
[PASS] kernel               6.6.0
[PASS] memory_shm           shm=512.00 GiB (512.00 GiB free)
[INFO] aio_resources        max-nr=1048576 nr=0 available=1048576
Result: PASSED (with warnings)  |  2 pass, 1 warn, 0 fail, 0 skip, 3 info
"""

A5_FACT_OUTPUT = """__DEPLOYER_UCM__
Package                     Version
uc-manager-cann910-a5        0.9.0rc1
ucm-toolkit                  0.9.0rc1
__DEPLOYER_ASCEND_DRIVER__
Version=25.6.rc1.b999
ascendhal_version=7.35.23
__DEPLOYER_NPU_SMI__
| npu-smi 25.6.rc1.b999                            Version: 25.6.rc1.b999 |
__DEPLOYER_NVIDIA_DRIVER__
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
    assert result.verdict == "failed"
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


def test_parse_chinese_names_and_advice():
    """已知检查项填充中文名与排查建议。"""
    res = CommandResult(command="precheck", exit_code=1, stdout=SAMPLE_OUTPUT)
    result = parse_precheck_output(res)
    by_name = {i.name: i for i in result.items}
    assert by_name["kernel"].name_zh == "内核版本"
    assert "升级" in by_name["kernel"].advice
    assert by_name["memory_shm"].name_zh == "共享内存 /dev/shm"
    assert "--shm-size" in by_name["memory_shm"].advice
    assert by_name["accelerator_driver"].name_zh == "加速器驱动"
    assert "npu-smi" in by_name["accelerator_driver"].advice
    assert by_name["bandwidth"].name_zh == "存储带宽"
    assert "NVMe" in by_name["bandwidth"].advice


def test_parse_unknown_item_falls_back():
    text = "[PASS] some_new_check  ok\n"
    result = parse_precheck_output(CommandResult(command="x", exit_code=0,
                                                 stdout=text))
    item = result.items[0]
    assert item.name == "some_new_check"
    assert item.name_zh == "some_new_check"   # 未知项回退原名
    assert item.advice == ""


def test_verdict_three_states():
    """三态判定：passed / completed（有警告）/ failed。"""
    ok = "[PASS] kernel  5.10.0\n"
    warn = ("[PASS] kernel  5.10.0\n"
            "[WARN] memory_shm  1 GiB\n"
            "Result: PASSED (with warnings)  |  1 pass, 1 warn, 0 fail\n")
    fail = ("[FAIL] kernel  4.19.0\n"
            "Result: FAILED  |  0 pass, 0 warn, 1 fail\n")
    assert parse_precheck_output(
        CommandResult(command="x", exit_code=0, stdout=ok)).verdict == "passed"
    completed = parse_precheck_output(
        CommandResult(command="x", exit_code=0, stdout=warn))
    assert completed.verdict == "completed"
    assert completed.warn_count == 1
    # 含空格的判定（PASSED (with warnings)）也要完整解析进 summary
    assert "PASSED (with warnings)" in completed.summary
    assert "1 pass, 1 warn, 0 fail" in completed.summary
    assert parse_precheck_output(
        CommandResult(command="x", exit_code=1, stdout=fail)).verdict == "failed"


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


def test_collect_precheck_facts_a5(fake_ssh):
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"__DEPLOYER_UCM__", stdout=A5_FACT_OUTPUT)
    facts = PrecheckRunner(ssh).collect_precheck_facts("ucm-a5")
    assert facts.ucm.installed
    assert facts.ucm.package == "uc-manager-cann910-a5"
    assert facts.ucm.version == "0.9.0rc1"
    assert facts.driver_version == "25.6.rc1.b999"
    assert facts.driver_source == "/usr/local/Ascend/driver/version.info"


def test_enhance_precheck_result_a5():
    result = parse_precheck_output(CommandResult(
        command="precheck", exit_code=0, stdout=A5_UNKNOWN_OUTPUT))
    facts = PrecheckFacts(
        ucm=UCMInfo(installed=True, package="uc-manager-cann910-a5",
                    version="0.9.0rc1"),
        driver_version="25.6.rc1.b999",
        driver_source="/usr/local/Ascend/driver/version.info")
    enhance_precheck_result(result, facts)
    by_name = {item.name: item for item in result.items}
    assert by_name["uc_manager"].summary == "uc-manager-cann910-a5=0.9.0rc1"
    assert "pip list" in by_name["uc_manager"].detail
    assert by_name["accelerator_driver"].status == "PASS"
    assert by_name["accelerator_driver"].summary == "HDK=25.6.rc1.b999"
    assert "version.info" in by_name["accelerator_driver"].detail
    assert result.verdict == "passed"
    assert result.summary == ("Result: PASSED |  3 pass, 0 warn, 0 fail, "
                              "0 skip, 3 info")


def test_enhance_precheck_result_keeps_low_driver_warning():
    result = parse_precheck_output(CommandResult(
        command="precheck", exit_code=0, stdout=A5_UNKNOWN_OUTPUT))
    facts = PrecheckFacts(driver_version="25.1.0",
                          driver_source="/usr/local/Ascend/driver/version.info")
    enhance_precheck_result(result, facts)
    by_name = {item.name: item for item in result.items}
    assert by_name["accelerator_driver"].status == "WARN"
    assert by_name["accelerator_driver"].summary == "HDK=25.1.0"


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


def test_run_enhances_unknown_a5_facts(fake_ssh):
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"ucm-toolkit run precheck", stdout=A5_UNKNOWN_OUTPUT)
    ssh._client.add(r"__DEPLOYER_UCM__", stdout=A5_FACT_OUTPUT)
    result = PrecheckRunner(ssh).run("ucm-a5", mount_path="",
                                     skip_bandwidth=True)
    by_name = {item.name: item for item in result.items}
    assert by_name["uc_manager"].summary == "uc-manager-cann910-a5=0.9.0rc1"
    assert by_name["accelerator_driver"].status == "PASS"
    assert by_name["accelerator_driver"].summary == "HDK=25.6.rc1.b999"
    assert result.verdict == "passed"


def test_list_dirs_uses_container_view(fake_ssh):
    """目录浏览为容器内视角（docker exec find）。"""
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"find /mnt -maxdepth 1", stdout="/mnt/a\n/mnt/b\n")
    dirs = PrecheckRunner(ssh).list_dirs("ucm-ctr", "/mnt")
    assert dirs == ["/mnt/a", "/mnt/b"]
    cmd = ssh._client.channels[-1].executed_command
    assert cmd.startswith("docker exec ucm-ctr /bin/bash -c ")
    assert "find /mnt" in cmd


def test_run_mount_not_in_container_rejected(fake_ssh):
    """挂载目录容器内不存在：拒绝执行且不创建任何目录（防测错容器写层）。"""
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"test -d /mnt/kv", stderr="", exit_code=1)
    with pytest.raises(PrecheckError, match="容器内不存在"):
        PrecheckRunner(ssh).run("ucm-ctr", mount_path="/mnt/kv")
    cmds = [c.executed_command for c in ssh._client.channels]
    assert not any("mkdir" in c for c in cmds), "不存在时不得凭空创建目录"
    assert not any("ucm-toolkit run precheck" in c for c in cmds)


def test_run_with_mount_creates_and_cleans_subdir(fake_ssh):
    """指定挂载目录（容器内已存在）：创建临时子目录执行带宽测试并清理（播报路径）。"""
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"test -d /mnt/kvcache", stdout="")          # 容器内存在
    ssh._client.add(r"mkdir -p ", stdout="")                      # 建子目录
    ssh._client.add(r"ucm-toolkit run precheck",
                    stdout="[PASS] kernel  5.10.0\n[WARN] bandwidth  7.0\n")
    ssh._client.add(r"rm -rf ", stdout="")                        # 清子目录
    lines = []
    result = PrecheckRunner(ssh).run("ucm-ctr", mount_path="/mnt/kvcache",
                                     on_line=lines.append)
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
    # 运行时播报临时目录路径与清理结果（便于用户在环境上核对）
    assert any("[deployer] 带宽测试临时目录" in l and "ucm-precheck-" in l
               for l in lines)
    assert any("[deployer] 已清理临时目录" in l and "ucm-precheck-" in l
               for l in lines)


def test_run_no_mount_no_notify_lines(fake_ssh):
    """无挂载目录（仅环境检查）：无临时目录播报。"""
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"ucm-toolkit run precheck", stdout="[PASS] kernel  5.10.0\n")
    lines = []
    PrecheckRunner(ssh).run("ucm-ctr", mount_path="",
                            skip_bandwidth=True, on_line=lines.append)
    assert not any("[deployer]" in l for l in lines)


def test_run_cleans_subdir_even_on_failure(fake_ssh):
    """precheck 失败（退出码 1）也要清理子目录。"""
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"test -d /mnt/kv", stdout="")
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
    ssh._client.add(r"test -d /mnt/kv", stdout="")
    ssh._client.add(r"mkdir -p ", stderr="Permission denied", exit_code=1)
    with pytest.raises(PrecheckError, match="创建预检临时目录失败"):
        PrecheckRunner(ssh).run("ucm-ctr", mount_path="/mnt/kv")


# ============================================================ 配置文件
def test_locate_default_config(fake_ssh):
    ssh = make_ssh(fake_ssh)
    ssh._client.add(
        r"python3 -c ",
        stdout="/usr/local/lib/python3.10/site-packages/ucm_toolkit/tools/"
               "precheck/precheck.defaults.json\n")
    path = PrecheckRunner(ssh).locate_default_config("ucm-ctr")
    assert path.endswith("ucm_toolkit/tools/precheck/precheck.defaults.json")
    cmd = ssh._client.channels[-1].executed_command
    assert cmd.startswith("docker exec ucm-ctr /bin/bash -c ")
    assert "import ucm_toolkit" in cmd


def test_locate_default_config_failure(fake_ssh):
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"python3 -c ", stderr="No module named ucm_toolkit",
                    exit_code=1)
    assert PrecheckRunner(ssh).locate_default_config("ucm-ctr") == ""


def test_read_default_config(fake_ssh):
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"python3 -c ",
                    stdout="/site/ucm_toolkit/tools/precheck/"
                           "precheck.defaults.json\n")
    ssh._client.add(r"cat /site/", stdout='{"kernel_min": "5.10"}\n')
    text = PrecheckRunner(ssh).read_default_config("ucm-ctr")
    assert '"kernel_min"' in text
    cat_cmd = ssh._client.channels[-1].executed_command
    assert "cat " in cat_cmd and "precheck.defaults.json" in cat_cmd


def test_read_default_config_not_installed(fake_ssh):
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"python3 -c ", exit_code=1)
    with pytest.raises(PrecheckError, match="定位"):
        PrecheckRunner(ssh).read_default_config("ucm-ctr")


def test_save_config_valid(fake_ssh):
    import json as _json

    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"mkdir -p /root/ucm-deploy", stdout="")
    payload = _json.dumps({"bandwidth": {"threshold_gb": 4.0}})
    PrecheckRunner(ssh).save_config("ucm-ctr", payload)
    cmd = ssh._client.channels[-1].executed_command
    assert "mkdir -p /root/ucm-deploy" in cmd
    assert "printf" in cmd and "%s" in cmd     # %s 经 shq 转义为 '\''%s'\''
    assert "/root/ucm-deploy/precheck.json" in cmd
    assert payload in cmd        # JSON 内容原样进入容器内文件


def test_save_config_invalid_json_rejected(fake_ssh):
    ssh = make_ssh(fake_ssh)
    with pytest.raises(PrecheckError, match="不是合法 JSON"):
        PrecheckRunner(ssh).save_config("ucm-ctr", "{broken")
    assert not [c for c in ssh._client.channels if "printf" in c.executed_command]


def test_config_exists_and_remove(fake_ssh):
    ssh = make_ssh(fake_ssh)
    runner = PrecheckRunner(ssh)
    ssh._client.add(r"test -f /root/ucm-deploy/precheck.json", stdout="",
                    exit_code=1)
    assert not runner.config_exists("ucm-ctr")
    assert runner.remove_config("ucm-ctr") is False    # 不存在 -> 无需删

    ssh._client.add(r"test -f /root/ucm-deploy/precheck.json", stdout="")
    ssh._client.add(r"rm -f /root/ucm-deploy/precheck.json", stdout="")
    assert runner.config_exists("ucm-ctr")
    assert runner.remove_config("ucm-ctr") is True


def test_run_with_config_path(fake_ssh):
    """--config 引用容器内自定义配置；文件缺失时报错。"""
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"test -f /root/ucm-deploy/precheck.json", exit_code=1)
    with pytest.raises(PrecheckError, match="自定义预检配置在容器内不存在"):
        PrecheckRunner(ssh).run("ucm-ctr", config_path="/root/ucm-deploy/precheck.json")

    ssh._client.add(r"test -f /root/ucm-deploy/precheck.json", stdout="")
    ssh._client.add(r"ucm-toolkit run precheck", stdout="[PASS] kernel  5.10.0\n")
    result = PrecheckRunner(ssh).run(
        "ucm-ctr", mount_path="", skip_bandwidth=True,
        config_path="/root/ucm-deploy/precheck.json")
    assert result.ok
    cmd = ssh._client.channels[-1].executed_command
    assert "--config /root/ucm-deploy/precheck.json" in cmd
    assert "--skip-bandwidth" in cmd
