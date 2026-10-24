# -*- coding: utf-8 -*-
from ucm_deployer.core.device_detector import DeviceDetector
from ucm_deployer.core.models import DeviceType, ServerInfo
from ucm_deployer.core.ssh_client import SSHClient

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

_NPU_SMI_950 = """+------------------------------------------------------------------------------------------------------------------+
| npu-smi 25.6.rc1.b999                            Version: 25.6.rc1.b999                                          |
+--------+------------------+---------------+----------------------------------------------------------------------+
| NPU ID | Name             | Health        | Power(W)              Temp(C)                  Hugepages-Usage(page) |
        |                  | Bus-Id        | NPU Util(%)           Memory-Usage(MB)         HBM-Usage(MB)          |
+========+==================+===============+======================================================================+
| 0      | Ascend950PR      | OK            | 270.2                 45                       0     / 0             |
        |                  | NA            | 0                     0                        5249  / 131072          |
+------------------------------------------------------------------------------------------------------------------+"""


def make_ssh(fake_ssh) -> SSHClient:
    ssh = SSHClient(ServerInfo.create(name="s", host="127.0.0.1"))
    ssh.connect()
    return ssh


def test_detect_ascend_by_npu_smi(fake_ssh):
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"^npu-smi info", stdout=_NPU_SMI)
    dev = DeviceDetector(ssh).detect()
    assert dev.device_type == DeviceType.ASCEND
    assert dev.model == "Ascend 910B3"
    assert dev.count == 4


def test_detect_ascend_950_pr_by_npu_smi(fake_ssh):
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"^npu-smi info", stdout=_NPU_SMI_950)
    dev = DeviceDetector(ssh).detect()
    assert dev.device_type == DeviceType.ASCEND
    assert dev.model == "Ascend950PR"
    assert dev.count == 1


def test_detect_ascend_by_dev_nodes(fake_ssh):
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"^npu-smi info", exit_code=127)
    ssh._client.add(r"ls -1 /dev/ .*grep -cE.*davinci", stdout="16\n")
    dev = DeviceDetector(ssh).detect()
    assert dev.device_type == DeviceType.ASCEND
    assert dev.count == 16


def test_detect_nvidia(fake_ssh):
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"^npu-smi info", exit_code=127)
    ssh._client.add(r"^nvidia-smi --query-gpu",
                    stdout="0, NVIDIA A800-SXM4-80GB\n1, NVIDIA A800-SXM4-80GB\n")
    dev = DeviceDetector(ssh).detect()
    assert dev.device_type == DeviceType.NVIDIA
    assert dev.model == "NVIDIA A800-SXM4-80GB"
    assert dev.count == 2


def test_detect_none(fake_ssh):
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"^npu-smi info", exit_code=127)
    ssh._client.add(r"^nvidia-smi", exit_code=127)
    ssh._client.add(r"^lspci", stdout="0\n")
    dev = DeviceDetector(ssh).detect()
    assert dev.device_type == DeviceType.UNKNOWN
    assert dev.count == 0


def test_detect_arch(fake_ssh):
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"^uname -m", stdout="aarch64\n")
    assert DeviceDetector(ssh).detect_arch() == "aarch64"


def test_detect_arch_unsupported(fake_ssh):
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"^uname -m", stdout="riscv64\n")
    assert DeviceDetector(ssh).detect_arch() == ""


def test_detect_includes_arch(fake_ssh):
    ssh = make_ssh(fake_ssh)
    ssh._client.add(r"^npu-smi info", stdout=_NPU_SMI)
    ssh._client.add(r"^uname -m", stdout="aarch64\n")
    dev = DeviceDetector(ssh).detect()
    assert dev.arch == "aarch64"
