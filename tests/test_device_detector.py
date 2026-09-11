# -*- coding: utf-8 -*-
from ucm_deployer.core.device_detector import DeviceDetector
from ucm_deployer.core.models import DeviceInfo, DeviceType, ServerInfo
from ucm_deployer.core.ssh_client import SSHClient

NPU_SMI_8 = """+------------------------------------------------------------------------------------------------------+
| npu-smi 24.1.rc2                Version: 24.1.rc2                                                   |
+===========================+===============+=======================================================+
| NPU     Name              | Health        | Power(W)     Temp(C)              Hugepages-Usage(page) |
| Chip    Device            | Bus-Id        | AICore(%)    Memory-Usage(%)                              |
|===========================+===============+=======================================================+
| 0       910B3             | OK            | 97.0         41                    0    / 0              |
| 0       910B3             | 0000:C1:00.0  | 0            0    / 0             28 / 31 (90%)          |
+---------------------------+---------------+-------------------------------------------------------+
| 1       910B3             | OK            | 95.5         42                    0    / 0              |
| 1       910B3             | 0000:C2:00.0  | 0            0    / 0             28 / 31 (90%)          |
+---------------------------+---------------+-------------------------------------------------------+
| 2       910B3             | OK            | 95.0         41                    0    / 0              |
| 2       910B3             | 0000:C3:00.0  | 0            0    / 0             28 / 31 (90%)          |
+---------------------------+---------------+-------------------------------------------------------+
| 3       910B3             | OK            | 94.0         40                    0    / 0              |
| 3       910B3             | 0000:C4:00.0  | 0            0    / 0             28 / 31 (90%)          |
+---------------------------+---------------+-------------------------------------------------------+
| 4       910B3             | OK            | 96.0         42                    0    / 0              |
| 4       910B3             | 0000:C5:00.0  | 0            0    / 0             28 / 31 (90%)          |
+---------------------------+---------------+-------------------------------------------------------+
| 5       910B3             | OK            | 97.0         43                    0    / 0              |
| 5       910B3             | 0000:C6:00.0  | 0            0    / 0             28 / 31 (90%)          |
+---------------------------+---------------+-------------------------------------------------------+
| 6       910B3             | OK            | 95.0         41                    0    / 0              |
| 6       910B3             | 0000:C7:00.0  | 0            0    / 0             28 / 31 (90%)          |
+---------------------------+---------------+-------------------------------------------------------+
| 7       910B3             | OK            | 96.0         42                    0    / 0              |
| 7       910B3             | 0000:C8:00.0  | 0            0    / 0             28 / 31 (90%)          |
+---------------------------+---------------+-------------------------------------------------------+
"""

NVIDIA_SMI = """0, NVIDIA A800-SXM4-80GB
1, NVIDIA A800-SXM4-80GB
2, NVIDIA A800-SXM4-80GB
3, NVIDIA A800-SXM4-80GB
4, NVIDIA A800-SXM4-80GB
5, NVIDIA A800-SXM4-80GB
6, NVIDIA A800-SXM4-80GB
7, NVIDIA A800-SXM4-80GB
"""


def make_ssh(fake_ssh):
    ssh = SSHClient(ServerInfo.create(name="s", host="127.0.0.1"))
    ssh.connect()
    return ssh


def test_detect_ascend(fake_ssh):
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"^npu-smi info$", stdout=NPU_SMI_8)
    info = DeviceDetector(ssh).detect()
    assert info.device_type == DeviceType.ASCEND
    assert info.model == "Ascend 910B3"
    assert info.count == 8


def test_detect_ascend_fallback_dev(fake_ssh):
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"^npu-smi info$", exit_code=1, stderr="command not found")
    ssh._client.add(r"ls -1 /dev/ .*grep -cE.*davinci", stdout="16\n")
    info = DeviceDetector(ssh).detect()
    assert info.device_type == DeviceType.ASCEND
    assert info.count == 16


def test_detect_nvidia(fake_ssh):
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"^npu-smi info$", exit_code=127, stderr="command not found")
    ssh._client.add(r"nvidia-smi --query-gpu", stdout=NVIDIA_SMI)
    info = DeviceDetector(ssh).detect()
    assert info.device_type == DeviceType.NVIDIA
    assert info.model == "NVIDIA A800-SXM4-80GB"
    assert info.count == 8


def test_detect_nothing(fake_ssh):
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"^npu-smi info$", exit_code=127)
    ssh._client.add(r"nvidia-smi --query-gpu", exit_code=127)
    ssh._client.add(r"lspci", exit_code=1, stdout="0\n")
    info = DeviceDetector(ssh).detect()
    assert info.device_type == DeviceType.UNKNOWN
    assert info.count == 0


def test_consistency_same(fake_ssh):
    infos = {
        "s1": DeviceInfo(DeviceType.ASCEND, "Ascend 910B3", 8),
        "s2": DeviceInfo(DeviceType.ASCEND, "Ascend 910B3", 8),
    }
    r = DeviceDetector.verify_consistency(infos)
    assert r.ok and not r.warnings


def test_consistency_count_diff_warns(fake_ssh):
    infos = {
        "s1": DeviceInfo(DeviceType.ASCEND, "Ascend 910B3", 8),
        "s2": DeviceInfo(DeviceType.ASCEND, "Ascend 910B3", 16),
    }
    r = DeviceDetector.verify_consistency(infos)
    assert r.ok and r.warnings


def test_consistency_model_mismatch(fake_ssh):
    infos = {
        "s1": DeviceInfo(DeviceType.ASCEND, "Ascend 910B3", 8),
        "s2": DeviceInfo(DeviceType.ASCEND, "Ascend 910C", 16),
    }
    r = DeviceDetector.verify_consistency(infos)
    assert not r.ok and "不一致" in r.message


def test_consistency_undetected(fake_ssh):
    infos = {
        "s1": DeviceInfo(DeviceType.ASCEND, "Ascend 910B3", 8),
        "s2": DeviceInfo(DeviceType.UNKNOWN, "", 0),
    }
    r = DeviceDetector.verify_consistency(infos)
    assert not r.ok and "s2" in r.message


def test_consistency_single(fake_ssh):
    r = DeviceDetector.verify_consistency(
        {"s1": DeviceInfo(DeviceType.ASCEND, "Ascend 910B3", 8)})
    assert r.ok
