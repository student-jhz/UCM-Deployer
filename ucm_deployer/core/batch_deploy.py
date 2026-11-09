# -*- coding: utf-8 -*-
"""批量部署：在一台源服务器制作镜像后，向多台目标服务器分发镜像、
创建容器并执行环境预检。

本模块不依赖 GUI：
- DeployTemplate        部署数据模板（服务器登录信息 + 镜像 + 容器/预检配置），
                        支持导出/导入 JSON，复用于多台服务器的重复配置；
- export_image_tar      在源服务器上 docker save | gzip 导出镜像包；
- load_image_tar        上传镜像包到目标服务器并 docker load + 校验；
- create_container_from_template / run_precheck_on_target
                        每台目标服务器的流水线阶段（按设备类型自动适配）。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .container_manager import ContainerCreateConfig, ContainerManager
from .device_detector import DeviceDetector
from .docker_manager import DockerManager
from .models import DeviceInfo, DeviceType, ServerInfo, VolumeMount
from .precheck import CONFIG_CONTAINER_PATH, PrecheckRunner
from .shell import shq
from .ssh_client import SSHClient

ProgressFn = Callable[[int, str], None]
LogFn = Callable[[str], None]
CancelledFn = Callable[[], bool]

TEMPLATE_KIND = "ucm-deployer-batch-template"
TEMPLATE_VERSION = 1


class BatchDeployError(Exception):
    """批量部署配置或执行错误。"""


class TemplateError(Exception):
    """部署模板解析/校验错误。"""


# ============================================================ 配置模板
@dataclass
class BatchContainerConfig:
    """容器创建参数模板（与设备无关；执行时按各服务器设备自动适配）。

    与 ContainerCreateConfig 的区别：不含设备探测结果（device_type/
    device_nodes/system_mounts 等），这些在每台目标服务器上现场探测。
    """

    name: str = "ucm-vllm"
    shm_size: str = "512g"
    network: str = "host"                  # Ascend 使用（--net=host）
    ports: List[str] = field(default_factory=lambda: ["8000:8000"])  # NVIDIA
    kv_cache_dirs: List[str] = field(default_factory=list)
    model_mount: Optional[VolumeMount] = None
    extra_mounts: List[VolumeMount] = field(default_factory=list)
    extra_args: str = ""

    def validate(self) -> None:
        if not self.name or not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", self.name):
            raise BatchDeployError(f"非法的容器名: {self.name!r}")
        for d in self.kv_cache_dirs:
            if not d.startswith("/"):
                raise BatchDeployError(f"kvcache 挂载目录必须是绝对路径: {d}")

    def to_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "shm_size": self.shm_size,
            "network": self.network,
            "ports": list(self.ports),
            "kv_cache_dirs": list(self.kv_cache_dirs),
            "model_mount": (_mount_to_dict(self.model_mount)
                            if self.model_mount is not None else None),
            "extra_mounts": [_mount_to_dict(m) for m in self.extra_mounts],
            "extra_args": self.extra_args,
        }

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, object]]) -> "BatchContainerConfig":
        d = d or {}
        cfg = cls()
        cfg.name = str(d.get("name") or "ucm-vllm")
        cfg.shm_size = str(d.get("shm_size") or "512g")
        cfg.network = str(d.get("network") or "host")
        cfg.ports = [str(p) for p in _as_list(d.get("ports"))] or ["8000:8000"]
        cfg.kv_cache_dirs = [str(p) for p in _as_list(d.get("kv_cache_dirs"))]
        cfg.model_mount = _parse_mount(d.get("model_mount"))
        cfg.extra_mounts = [_parse_mount(m) for m in
                            _as_list(d.get("extra_mounts")) if _parse_mount(m)]
        cfg.extra_args = str(d.get("extra_args") or "")
        return cfg

    def to_container_create_config(
        self, image: str, device_type: DeviceType, device_count: int,
        device_nodes: List[str], system_mounts: List[Tuple[str, str]],
        privileged: bool,
    ):
        """按目标服务器探测到的设备信息，生成完整的容器创建配置。"""
        return ContainerCreateConfig(
            image=image, name=self.name, device_type=device_type,
            device_count=device_count, shm_size=self.shm_size,
            network=self.network, ports=list(self.ports),
            kv_cache_dirs=list(self.kv_cache_dirs),
            model_mount=self.model_mount,
            extra_mounts=list(self.extra_mounts),
            extra_args=self.extra_args,
            device_nodes=list(device_nodes),
            system_mounts=list(system_mounts),
            privileged=privileged)


@dataclass
class BatchPrecheckConfig:
    """预检参数模板（每台目标容器内一致）。"""

    bandwidth_check: bool = True
    mount_path: str = ""                   # 容器内挂载目录（带宽测试目录）
    custom_config_enabled: bool = False
    custom_config_json: str = ""           # 叠加于出厂默认之上

    def validate(self) -> None:
        if self.custom_config_enabled and self.custom_config_json.strip():
            try:
                json.loads(self.custom_config_json)
            except ValueError as exc:
                raise BatchDeployError(f"自定义预检配置不是合法 JSON: {exc}") from exc

    def to_dict(self) -> Dict[str, object]:
        # custom_config_json 尽量导出为 JSON 对象（便于手工编辑模板文件）
        config_value: object = self.custom_config_json
        if self.custom_config_json.strip():
            try:
                config_value = json.loads(self.custom_config_json)
            except ValueError:
                config_value = self.custom_config_json
        return {
            "bandwidth_check": self.bandwidth_check,
            "mount_path": self.mount_path,
            "custom_config_enabled": self.custom_config_enabled,
            "custom_config_json": config_value,
        }

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, object]]) -> "BatchPrecheckConfig":
        d = d or {}
        cfg = cls()
        cfg.bandwidth_check = bool(d.get("bandwidth_check", True))
        cfg.mount_path = str(d.get("mount_path") or "")
        cfg.custom_config_enabled = bool(d.get("custom_config_enabled", False))
        raw = d.get("custom_config_json")
        if raw is None:
            cfg.custom_config_json = ""
        elif isinstance(raw, dict):
            cfg.custom_config_json = json.dumps(raw, ensure_ascii=False, indent=2)
        else:
            cfg.custom_config_json = str(raw)
        return cfg


@dataclass
class BatchStages:
    """批量执行阶段开关。"""

    distribute_image: bool = True
    create_container: bool = True
    run_precheck: bool = True

    def to_dict(self) -> Dict[str, bool]:
        return {"distribute_image": self.distribute_image,
                "create_container": self.create_container,
                "run_precheck": self.run_precheck}

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, object]]) -> "BatchStages":
        d = d or {}
        stages = cls()
        stages.distribute_image = bool(d.get("distribute_image", True))
        stages.create_container = bool(d.get("create_container", True))
        stages.run_precheck = bool(d.get("run_precheck", True))
        return stages


@dataclass
class BatchTargetReport:
    """一台目标服务器的批量部署结果。"""
    server_id: str
    server_name: str = ""
    image: str = ""
    image_skipped: bool = False
    container: str = ""
    precheck_summary: str = ""
    precheck_verdict: str = ""             # passed / completed / failed / ""

    def describe(self) -> str:
        parts: List[str] = []
        if self.image:
            parts.append("镜像" + ("已存在(跳过)" if self.image_skipped else "已分发"))
        if self.container:
            parts.append(f"容器 {self.container} 已创建")
        if self.precheck_verdict:
            verdict_zh = {"passed": "预检通过", "completed": "预检完成(有警告)",
                          "failed": "预检未通过"}.get(self.precheck_verdict,
                                                      self.precheck_verdict)
            parts.append(verdict_zh)
        return "；".join(parts) if parts else "无操作"


@dataclass
class DeployTemplate:
    """批量部署数据模板：登录信息 + 镜像来源 + 容器/预检配置。

    导出为 JSON 文件后在其他机器导入，即可复用整套部署配置；
    登录密码/私钥口令默认不导出（导出时可选，导入后存入本地加密注册表）。
    """

    image_mode: str = "tar"                # tar（镜像包中转）| pull（仓库拉取）
    image_ref: str = ""
    source_host: str = ""                  # 源服务器主机（tar 模式提示用）
    source_port: int = 22
    skip_existing: bool = True
    recreate_container: bool = False       # 同名容器先删除再创建
    servers: List[ServerInfo] = field(default_factory=list)
    container: BatchContainerConfig = field(default_factory=BatchContainerConfig)
    precheck: BatchPrecheckConfig = field(default_factory=BatchPrecheckConfig)
    stages: BatchStages = field(default_factory=BatchStages)

    # ------------------------------------------------------------ 序列化
    def to_dict(self, include_secrets: bool = False) -> Dict[str, object]:
        return {
            "kind": TEMPLATE_KIND,
            "template_version": TEMPLATE_VERSION,
            "image": {
                "mode": self.image_mode,
                "ref": self.image_ref,
                "source_host": self.source_host,
                "source_port": self.source_port,
                "skip_existing": self.skip_existing,
                "recreate_container": self.recreate_container,
            },
            "container": self.container.to_dict(),
            "precheck": self.precheck.to_dict(),
            "stages": self.stages.to_dict(),
            "servers": [s.to_dict(include_secret=include_secrets)
                        for s in self.servers],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "DeployTemplate":
        if not isinstance(data, dict):
            raise TemplateError("模板文件内容不是 JSON 对象")
        kind = data.get("kind")
        if kind is not None and kind != TEMPLATE_KIND:
            raise TemplateError(f"不是 UCM Deployer 批量部署模板（kind={kind!r}）")
        version = data.get("template_version", 1)
        if not isinstance(version, int) or version != TEMPLATE_VERSION:
            raise TemplateError(f"不支持的模板版本: {version!r}（当前支持 {TEMPLATE_VERSION}）")

        image = data.get("image") or {}
        tpl = cls()
        tpl.image_mode = str(image.get("mode") or "tar")
        if tpl.image_mode not in ("tar", "pull"):
            raise TemplateError(f"非法的镜像分发方式: {tpl.image_mode!r}")
        tpl.image_ref = str(image.get("ref") or "")
        tpl.source_host = str(image.get("source_host") or "")
        try:
            tpl.source_port = int(image.get("source_port") or 22)
        except (TypeError, ValueError):
            raise TemplateError("源服务器端口必须是数字") from None
        tpl.skip_existing = bool(image.get("skip_existing", True))
        tpl.recreate_container = bool(image.get("recreate_container", False))

        servers: List[ServerInfo] = []
        for i, item in enumerate(_as_list(data.get("servers"))):
            if not isinstance(item, dict):
                raise TemplateError(f"第 {i + 1} 台服务器信息格式错误")
            host = str(item.get("host") or "").strip()
            if not host:
                raise TemplateError(f"第 {i + 1} 台服务器缺少 host")
            try:
                port = int(item.get("port") or 22)
            except (TypeError, ValueError):
                raise TemplateError(f"第 {i + 1} 台服务器端口必须是数字") from None
            servers.append(ServerInfo.create(
                name=str(item.get("name") or host), host=host, port=port,
                username=str(item.get("username") or "root"),
                password=str(item.get("password") or ""),
                auth_type="key" if str(item.get("auth_type")) == "key" else "password",
                key_path=str(item.get("key_path") or ""),
                passphrase=str(item.get("passphrase") or ""),
                remark=str(item.get("remark") or "")))
        tpl.servers = servers
        tpl.container = BatchContainerConfig.from_dict(data.get("container"))
        tpl.precheck = BatchPrecheckConfig.from_dict(data.get("precheck"))
        tpl.stages = BatchStages.from_dict(data.get("stages"))
        try:
            tpl.container.validate()
            tpl.precheck.validate()
        except BatchDeployError as exc:
            raise TemplateError(f"模板配置无效: {exc}") from exc
        return tpl

    def save(self, path: str, include_secrets: bool = False) -> None:
        """导出模板到 JSON 文件。"""
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self.to_dict(include_secrets=include_secrets), fh,
                          ensure_ascii=False, indent=2)
        except OSError as exc:
            raise TemplateError(f"写入模板文件失败: {exc}") from exc

    @classmethod
    def load(cls, path: str) -> "DeployTemplate":
        """从 JSON 文件导入模板。"""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except OSError as exc:
            raise TemplateError(f"读取模板文件失败: {exc}") from exc
        except ValueError as exc:
            raise TemplateError(f"模板文件不是合法 JSON: {exc}") from exc
        return cls.from_dict(data)


def upsert_servers_from_template(registry, servers: List[ServerInfo]
                                 ) -> Tuple[List[ServerInfo], List[str]]:
    """把模板中的服务器导入本地注册表。

    按 host+port+username 匹配：命中则更新（模板密码为空时保留本地密码），
    未命中则新增。返回 (导入后的服务器列表, 提示消息列表)。
    """
    imported: List[ServerInfo] = []
    messages: List[str] = []
    for incoming in servers:
        existing = next(
            (s for s in registry.list_servers()
             if s.host == incoming.host and s.port == incoming.port
             and s.username == incoming.username), None)
        if existing is not None:
            existing.name = incoming.name or existing.name
            existing.remark = incoming.remark or existing.remark
            existing.auth_type = incoming.auth_type
            existing.key_path = incoming.key_path
            if incoming.password:
                existing.password = incoming.password
            if incoming.passphrase:
                existing.passphrase = incoming.passphrase
            registry.upsert(existing)
            imported.append(existing)
            messages.append(f"更新服务器 {existing.name} ({existing.endpoint})")
        else:
            registry.upsert(incoming)
            imported.append(incoming)
            messages.append(f"新增服务器 {incoming.name} ({incoming.endpoint})")
    return imported, messages


# ============================================================ 镜像分发
def export_image_tar(ssh: SSHClient, docker: DockerManager, image_ref: str,
                     remote_path: str, log: Optional[LogFn] = None,
                     cancelled: Optional[CancelledFn] = None) -> None:
    """在源服务器上导出镜像：docker save | gzip -1（失败自动清理半成品）。"""
    log = log or (lambda line: None)
    if not docker.image_exists(image_ref):
        raise BatchDeployError(f"源服务器上不存在镜像 {image_ref}")
    if cancelled is not None and cancelled():
        raise BatchDeployError("已取消")
    log(f"$ docker save {image_ref} | gzip -1 > {remote_path}")
    try:
        docker.save_image(image_ref, remote_path, on_line=lambda line: log(line))
    except Exception as exc:
        _try_rm(ssh, remote_path)
        raise BatchDeployError(f"导出镜像失败: {exc}") from exc


def load_image_tar(ssh: SSHClient, docker: DockerManager, local_tar: str,
                   image_ref: str, progress: Optional[ProgressFn] = None,
                   log: Optional[LogFn] = None,
                   cancelled: Optional[CancelledFn] = None) -> List[str]:
    """上传镜像包到目标服务器并 docker load（校验镜像后清理远端临时包）。

    返回 docker load 解析出的镜像引用列表。progress 收到 [0,100] 的
    分发阶段内部进度（调用方负责映射到整条流水线的区间）。
    """
    progress = progress or (lambda pct, msg: None)
    log = log or (lambda line: None)
    if not os.path.isfile(local_tar):
        raise BatchDeployError(f"本机镜像包不存在: {local_tar}")
    if cancelled is not None and cancelled():
        raise BatchDeployError("已取消")

    name = os.path.basename(local_tar)
    remote = f"/tmp/{name}"
    size = os.path.getsize(local_tar)
    log(f"上传镜像包 {name}（{size / 1e6:.1f} MB）")

    def cb(sent: int, total: int) -> None:
        if cancelled is not None and cancelled():
            raise BatchDeployError("已取消（上传镜像包）")
        progress(int(60 * sent / max(total, 1)),
                 f"上传镜像包 {sent / 1e6:.0f}/{total / 1e6:.0f} MB")

    try:
        ssh.upload_file(local_tar, remote, progress_cb=cb)
    except BatchDeployError:
        _try_rm(ssh, remote)
        raise
    except Exception as exc:
        _try_rm(ssh, remote)
        raise BatchDeployError(f"上传镜像包失败: {exc}") from exc

    progress(65, "docker load ...")
    log(f"$ docker load -i {remote}")
    try:
        refs = docker.load_image(remote, on_line=lambda l: log(l))
    except Exception as exc:
        _try_rm(ssh, remote)
        raise BatchDeployError(f"docker load 失败: {exc}") from exc
    finally:
        _try_rm(ssh, remote)

    if not docker.image_exists(image_ref):
        loaded = "、".join(refs) if refs else "（无）"
        raise BatchDeployError(
            f"镜像包导入完成但未找到镜像 {image_ref}（加载出: {loaded}）")
    progress(100, f"镜像 {image_ref} 导入完成")
    return refs


def pull_image_on_target(docker: DockerManager, image_ref: str,
                         progress: Optional[ProgressFn] = None,
                         log: Optional[LogFn] = None) -> str:
    """目标服务器直接从镜像仓库拉取。"""
    progress = progress or (lambda pct, msg: None)
    log = log or (lambda line: None)
    progress(10, f"docker pull {image_ref} ...")
    log(f"$ docker pull {image_ref}")
    try:
        docker.pull_image(image_ref, on_line=lambda l: log(l))
    except Exception as exc:
        raise BatchDeployError(f"docker pull 失败: {exc}") from exc
    progress(100, f"镜像 {image_ref} 拉取完成")
    return image_ref


def stage_progress_ranges(stages: BatchStages) -> Dict[str, Tuple[int, int]]:
    """把三个阶段按权重（分发45/创建25/预检30）映射到 0-100 的进度区间。"""
    weights: List[Tuple[str, int]] = []
    if stages.distribute_image:
        weights.append(("image", 45))
    if stages.create_container:
        weights.append(("container", 25))
    if stages.run_precheck:
        weights.append(("precheck", 30))
    total = sum(w for _, w in weights) or 1
    ranges: Dict[str, Tuple[int, int]] = {}
    cursor = 0
    for index, (name, weight) in enumerate(weights):
        lo = int(100 * cursor / total)
        cursor += weight
        hi = 100 if index == len(weights) - 1 else int(100 * cursor / total)
        ranges[name] = (lo, hi)
    return ranges


# ============================================================ 流水线阶段
def detect_target_device(ssh: SSHClient,
                         log: Optional[LogFn] = None) -> DeviceInfo:
    """探测目标服务器设备；识别不到加速设备时报错。"""
    log = log or (lambda line: None)
    dev = DeviceDetector(ssh).detect()
    if dev.device_type not in (DeviceType.ASCEND, DeviceType.NVIDIA):
        raise BatchDeployError(
            "未探测到加速设备（npu-smi/nvidia-smi 均无结果），无法按设备创建容器")
    log(f"设备: {dev.display}（{dev.device_type.value}）"
        + (f" / {dev.arch}" if dev.arch else ""))
    return dev


def create_container_from_template(
    ssh: SSHClient, docker: DockerManager, template: BatchContainerConfig,
    image_ref: str, progress: Optional[ProgressFn] = None,
    log: Optional[LogFn] = None,
    recreate: bool = False,
) -> Tuple[str, str]:
    """在目标服务器上按模板创建容器。

    流程：校验镜像 UCM -> 探测设备 -> （Ascend）探测实际设备节点与系统挂载
    -> 生成 docker run -> 执行。返回 (容器名, 命令)。
    """
    progress = progress or (lambda pct, msg: None)
    log = log or (lambda line: None)

    progress(5, "校验镜像内 UCM 安装")
    ucm = docker.image_ucm_info(image_ref)
    log(f"镜像 UCM: {ucm}")
    if not ucm.installed:
        raise BatchDeployError(
            f"镜像 {image_ref} 未安装 UCM，无法创建容器（请回步骤2构建/选择含 UCM 的镜像）")

    progress(25, "探测设备...")
    dev = detect_target_device(ssh, log=log)

    device_nodes: List[str] = []
    system_mounts: List[Tuple[str, str]] = []
    privileged = False
    if dev.device_type == DeviceType.ASCEND:
        progress(45, "探测 Ascend 设备节点与系统挂载...")
        manager = ContainerManager(ssh)
        device_nodes = manager.list_ascend_devices()
        system_mounts = manager.list_ascend_mounts()
        privileged = any(node in ("/dev/ummu", "/dev/uburma")
                         for node in device_nodes)
        log(f"Ascend 设备节点: {', '.join(device_nodes)}")
        log(f"Ascend 系统挂载: {len(system_mounts)} 项"
            + ("（含 /dev/ummu|uburma，将使用 --privileged）" if privileged else ""))

    cfg = template.to_container_create_config(
        image_ref, dev.device_type, dev.count, device_nodes,
        system_mounts, privileged)
    cfg.validate()
    command = ContainerManager.generate_run_command(cfg)

    if recreate:
        progress(65, f"删除旧容器 {cfg.name}（如存在）...")
        log(f"$ docker rm -f {cfg.name}（已选择重建容器）")
        try:
            ssh.exec_stream(f"docker rm -f {shq(cfg.name)}", timeout=120)
        except Exception:  # 容器不存在时 rm 返回非零，忽略
            pass

    progress(70, "创建容器...")
    log("$ " + command.replace("\n", " \\\n"))
    manager = ContainerManager(ssh, DockerManager(ssh))
    try:
        manager.create(command, on_line=lambda l: log(l))
    except Exception as exc:
        raise BatchDeployError(f"创建容器失败: {exc}") from exc
    progress(100, f"容器 {cfg.name} 已创建")
    return cfg.name, command


def run_precheck_on_target(
    ssh: SSHClient, precheck: BatchPrecheckConfig, container: str,
    progress: Optional[ProgressFn] = None, log: Optional[LogFn] = None,
):
    """在目标容器内执行环境预检（可选写入自定义配置）。返回 PrecheckResult。"""
    progress = progress or (lambda pct, msg: None)
    log = log or (lambda line: None)
    runner = PrecheckRunner(ssh)

    use_config = bool(precheck.custom_config_enabled
                      and precheck.custom_config_json.strip())
    if use_config:
        progress(10, "写入自定义预检配置...")
        try:
            runner.save_config(container, precheck.custom_config_json)
            log(f"已保存自定义预检配置到容器内 {CONFIG_CONTAINER_PATH}")
        except Exception as exc:
            raise BatchDeployError(f"保存预检配置失败: {exc}") from exc

    progress(20, "在容器内运行 ucm-toolkit run precheck ...")
    try:
        result = runner.run(
            container,
            mount_path=(precheck.mount_path if precheck.bandwidth_check else ""),
            skip_bandwidth=not precheck.bandwidth_check,
            config_path=CONFIG_CONTAINER_PATH if use_config else "",
            on_line=lambda line: log(line))
    except Exception as exc:
        raise BatchDeployError(f"预检执行失败: {exc}") from exc

    for item in result.items:
        detail = (item.summary + ("\n" + item.detail if item.detail else "")).strip()
        log(f"[{item.status}] {item.name}  {detail}")
    if result.summary:
        log(result.summary)
    progress(100, result.summary or "预检完成")
    return result


# ============================================================ 内部工具
def _as_list(value) -> List[object]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _mount_to_dict(mount: VolumeMount) -> Dict[str, object]:
    return {"host_path": mount.host_path,
            "container_path": mount.container_path,
            "readonly": mount.readonly}


def _parse_mount(value) -> Optional[VolumeMount]:
    if not value:
        return None
    if isinstance(value, dict):
        return VolumeMount(
            host_path=str(value.get("host_path") or ""),
            container_path=str(value.get("container_path") or ""),
            readonly=bool(value.get("readonly", False)))
    text = str(value)
    parts = text.split(":", 1)
    if len(parts) == 2:
        host, cont = parts[0].strip(), parts[1].strip()
        readonly = cont.endswith(":ro")
        if readonly:
            cont = cont[:-3]
        return VolumeMount(host_path=host, container_path=cont, readonly=readonly)
    return None


def _try_rm(ssh: SSHClient, remote_path: str) -> None:
    try:
        ssh.exec(f"rm -f {shq(remote_path)}", timeout=60)
    except Exception:  # pragma: no cover - 清理失败不影响主流程
        pass
