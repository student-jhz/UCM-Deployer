# -*- coding: utf-8 -*-
"""模拟 Linux 服务器：命令级状态机。

模拟范围（覆盖 UCM Deployer 会执行的全部远端命令）：
- npu-smi / nvidia-smi / lspci           设备探测
- docker info/images/load/pull/build/run/ps/exec/cp/stop/rm
- find / df / ls / mkdir / rm / cat      文件系统（沙箱目录）
- curl / ps aux / pgrep / pkill          进程与健康检查

文件系统沙箱：远端路径 "/" 映射到本地一个临时目录；
容器与宿主机共享同一文件系统（等价于 -v 同路径挂载），docker cp 即复制文件。

注意：脚本（serve.sh 等）不会被真实 bash 执行；`docker exec -d ... nohup bash x.sh`
会注册一个"模拟进程"，并从脚本内容解析服务端口，使 curl 健康检查能够返回 200。
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import threading
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..utils.log import get_logger

logger = get_logger(__name__)


class MockLinux:
    def __init__(self, root: str, device: str = "ascend", cards: int = 8,
                 npu_model: str = "910B3", gpu_model: str = "NVIDIA A800-SXM4-80GB",
                 preload_ucm_image: bool = False):
        self.root = Path(root).resolve()
        self.device = device
        self.cards = int(cards)
        self.npu_model = npu_model
        self.gpu_model = gpu_model
        self.images: Dict[str, dict] = {}
        self.containers: Dict[str, dict] = {}
        self.processes: List[dict] = []
        self.lock = threading.RLock()
        self._init_fs()
        self._seed_images(preload_ucm_image)

    # ------------------------------------------------------------ 基础
    def _init_fs(self) -> None:
        for d in ("models/Qwen3-32B", "mnt/nfs_share", "root", "opt", "home",
                  "workspace", "tmp", "dev",
                  "usr/local/dcmi", "usr/local/bin",
                  "usr/local/Ascend/driver/lib64"):
            (self.root / d).mkdir(parents=True, exist_ok=True)
        for i in range(self.cards):
            (self.root / f"dev/davinci{i}").write_text("mock-npu", encoding="utf-8")

    def _seed_images(self, preload_ucm: bool) -> None:
        if self.device == "ascend":
            base = ["quay.io/ascend/vllm-ascend:v0.23.0-a3",
                    "quay.io/ascend/vllm-ascend:v0.11.0"]
        else:
            base = ["vllm/vllm-openai:latest", "lmsysorg/sglang:v0.5.9"]
        for ref in base:
            self._add_image(ref)
        if preload_ucm:
            self._add_image("ucm-vllm:mock", has_ucm=True, ucm_version="0.2.1")

    def _add_image(self, ref: str, has_ucm: bool = False, ucm_version: str = "") -> dict:
        repo, _, tag = ref.rpartition(":")
        if not repo or "/" in tag:
            repo, tag = ref, "latest"
        info = {
            "repo": repo, "tag": tag, "ref": f"{repo}:{tag}",
            "id": uuid.uuid4().hex[:12], "size": f"{18 + len(self.images)}.2GB",
            "created": "2026-08-01 10:00:00 +0800 CST",
            "has_ucm": has_ucm, "ucm_version": ucm_version,
        }
        self.images[info["ref"]] = info
        return info

    # ------------------------------------------------------------ 路径映射
    def _real(self, path: str) -> str:
        clean = str(path).strip().strip("'\"")
        if clean.startswith("/"):
            clean = clean[1:]
        parts = [p for p in clean.split("/") if p and p != "."]
        if ".." in parts:
            raise ValueError("路径不允许包含 ..")
        return str(self.root.joinpath(*parts)) if parts else str(self.root)

    def _remote(self, real: str) -> str:
        rel = os.path.relpath(real, str(self.root)).replace("\\", "/")
        return "/" + rel

    # ------------------------------------------------------------ 入口
    def execute(self, cmd: str) -> Tuple[int, str, str]:
        with self.lock:
            try:
                code, out, err = self._dispatch(cmd.strip())
            except Exception as exc:  # 模拟器自身异常不能打断客户端
                logger.exception("mock 命令处理异常: %s", cmd)
                code, out, err = 1, "", f"mock-internal-error: {exc}"
            return code, out, err

    def _dispatch(self, cmd: str) -> Tuple[int, str, str]:
        if cmd == "npu-smi info" or cmd.startswith("npu-smi info "):
            if self.device != "ascend":
                return 127, "", "bash: npu-smi: command not found\n"
            return self._npu_smi()
        if cmd.startswith("nvidia-smi --query-gpu=index,name"):
            if self.device != "nvidia":
                return 127, "", "bash: nvidia-smi: command not found\n"
            return self._nvidia_smi()
        if cmd.startswith("nvidia-smi --query-compute-apps"):
            lines = [f"{p['pid']}, python3, 1024 MiB" for p in self.processes]
            return (0, "\n".join(lines) + ("\n" if lines else ""), "")
        if cmd.startswith("lspci"):
            count = self.cards if self.device == "nvidia" else 0
            return (0, "\n".join(["NVIDIA A800"] * count) + ("\n" if count else ""), "")
        if cmd.startswith("docker "):
            return self._docker(cmd)
        if cmd.startswith("find "):
            return self._find(cmd)
        if cmd.startswith("df --output=source,fstype"):
            return self._df(cmd)
        if re.match(r"^ls -1 /dev/ .*grep -cE.*davinci", cmd):
            return (0, f"{self.cards if self.device == 'ascend' else 0}\n", "")
        if cmd.startswith("mkdir -p "):
            return self._mkdir_p(cmd)
        if cmd.startswith("rm -f "):
            return self._rm(cmd, recursive=False)
        if cmd.startswith("rm -rf "):
            return self._rm(cmd, recursive=True)
        if cmd.startswith("curl "):
            return self._curl(cmd)
        if cmd.startswith("ps aux"):
            lines = [f"root {p['pid']} 0.5 2.0 12345 678 ? Sl 10:00 0:01 {p['cmdline']}"
                     for p in self.processes]
            return (0, "\n".join(lines) + ("\n" if lines else ""), "")
        if cmd.startswith("ip -o -4 route"):
            return (0, "default via 10.0.0.1 dev eth0\n", "")
        if cmd.startswith("echo "):
            text = cmd[len("echo "):].strip().strip("'\"")
            return (0, text + "\n", "")
        if cmd.startswith("cat "):
            return self._cat(cmd)
        if cmd == "true" or cmd == "test -d /":
            return (0, "", "")
        return (127, "", f"bash: 命令未模拟: {cmd[:120]}\n")

    # ------------------------------------------------------------ 系统信息
    def _npu_smi(self) -> Tuple[int, str, str]:
        rows = []
        for i in range(self.cards):
            rows.append(f"| {i:<7} {self.npu_model:<16} | OK            | "
                        f"{95 + i % 3}.{i}         {40 + i % 3}                0    / 0 |")
            rows.append(f"| {i:<7} {self.npu_model:<16} | 0000:C{i + 1}:00.0  | "
                        f"0            0    / 0         28 / 31 (90%)  |")
        header = (
            "+------------------------------------------------------------------------------------------------------+\n"
            "| npu-smi 24.1.rc2                Version: 24.1.rc2                                                   |\n"
            "+===========================+===============+=======================================================+\n"
            "| NPU     Name              | Health        | Power(W)     Temp(C)              Hugepages-Usage(page) |\n"
            "| Chip    Device            | Bus-Id        | AICore(%)    Memory-Usage(%)                            |\n"
            "|===========================+===============+=======================================================+\n"
        )
        body = "\n".join(rows) + "\n"
        tail = ("+---------------------------+---------------+-------------------------------------------------------+\n" * 1)
        return 0, header + body + tail, ""

    def _nvidia_smi(self) -> Tuple[int, str, str]:
        lines = [f"{i}, {self.gpu_model}" for i in range(self.cards)]
        return 0, "\n".join(lines) + "\n", ""

    # ------------------------------------------------------------ docker
    def _docker(self, cmd: str) -> Tuple[int, str, str]:
        if cmd.startswith("docker info"):
            return 0, "", ""
        if cmd.startswith("docker --version"):
            return 0, "Docker version 27.3.1, build ce12230\n", ""
        if "images --format" in cmd:
            out = "".join(f"{i['repo']}\t{i['tag']}\t{i['id']}\t{i['size']}\t{i['created']}\n"
                          for i in self.images.values())
            return 0, out, ""
        if cmd.startswith("docker load -i "):
            return self._docker_load(cmd)
        if cmd.startswith("docker pull "):
            ref = shlex.split(cmd)[2]
            self._add_image(ref)
            return 0, f"Loaded image: {ref}\n", ""
        if cmd.startswith("docker build "):
            return self._docker_build(cmd)
        if cmd.startswith("docker run "):
            return self._docker_run(cmd)
        if re.match(r"^docker ps\b", cmd):
            return self._docker_ps(cmd)
        if cmd.startswith("docker exec "):
            return self._docker_exec(cmd)
        if cmd.startswith("docker cp "):
            return self._docker_cp(cmd)
        if cmd.startswith("docker stop"):
            name = shlex.split(cmd)[-1]
            if name in self.containers:
                self.containers[name]["state"] = "exited"
                self.processes = [p for p in self.processes if p["container"] != name]
                return 0, name + "\n", ""
            return 1, "", f"Error: No such container: {name}\n"
        if cmd.startswith("docker rm"):
            tokens = shlex.split(cmd)
            name = tokens[-1]
            if name in self.containers:
                del self.containers[name]
                return 0, name + "\n", ""
            return 1, "", f"Error: No such container: {name}\n"
        return 1, "", f"docker: 未模拟的命令: {cmd[:100]}\n"

    def _docker_load(self, cmd: str) -> Tuple[int, str, str]:
        try:
            tar = shlex.split(cmd)[3]
        except (IndexError, ValueError):
            return 1, "", "docker load: 参数错误\n"
        real = self._real(tar)
        if not os.path.isfile(real):
            return 1, "", f"Error processing tar file {tar}: no such file\n"
        stem = os.path.basename(tar)
        for suffix in (".tar.gz", ".tgz", ".tar"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        if "__" in stem:
            repo, tag = stem.split("__", 1)
            ref = f"{repo}:{tag}"
        else:
            ref = f"{stem}:latest"
        self._add_image(ref)
        return 0, f"Loaded image: {ref}\n", ""

    def _docker_build(self, cmd: str) -> Tuple[int, str, str]:
        tokens = shlex.split(cmd)
        try:
            tag = tokens[tokens.index("-t") + 1]
            ctx = tokens[-1]
        except (ValueError, IndexError):
            return 1, "", "docker build: 参数错误\n"
        ctx_real = self._real(ctx)
        dockerfile = os.path.join(ctx_real, "Dockerfile")
        if not os.path.isfile(dockerfile):
            return 1, "", f"failed to read dockerfile: open {ctx}/Dockerfile: no such file or directory\n"
        lines = [l.strip() for l in
                 open(dockerfile, encoding="utf-8").read().splitlines() if l.strip()]
        steps: List[str] = []
        has_ucm, ucm_version = False, ""
        for idx, line in enumerate(lines, 1):
            steps.append(f"Step {idx}/{len(lines)} : {line}")
            if line.startswith("FROM "):
                base = line.split()[1]
                if base not in self.images:
                    return (1, "\n".join(steps) + "\n",
                            f"pull access denied for {base}, repository does not exist or may require 'docker login'\n")
            m = re.match(r"^COPY (\S+)", line)
            if m and not os.path.exists(os.path.join(ctx_real, m.group(1))):
                return (1, "\n".join(steps) + "\n",
                        f"COPY failed: file not found in build context: {m.group(1)}\n")
            if line.startswith("RUN "):
                for token in line.split():
                    if re.search(r"uc[_-]?manager.*\.whl", token, re.I) or \
                            re.match(r"^ucm.*\.whl", token, re.I):
                        has_ucm = True
                        v = re.search(r"(\d+\.\d+\.\d+)", token)
                        ucm_version = v.group(1) if v else "0.0.0"
        info = self._add_image(tag, has_ucm=has_ucm, ucm_version=ucm_version)
        steps.append(f"Successfully built {info['id']}")
        steps.append(f"Successfully tagged {info['ref']}")
        return 0, "\n".join(steps) + "\n", ""

    _VALUE_OPTS = {"--name", "--net", "--network", "--shm-size", "--device", "-v",
                   "--volume", "--entrypoint", "--gpus", "--runtime", "--restart",
                   "-p", "--publish", "--hostname", "-u", "--user", "--workdir"}

    def _docker_run(self, cmd: str) -> Tuple[int, str, str]:
        tokens = shlex.split(cmd)
        if "--entrypoint" in tokens:
            return self._docker_run_entrypoint(tokens)
        name = image = None
        volumes: List[str] = []
        i = tokens.index("run") + 1
        positional: List[str] = []
        while i < len(tokens):
            t = tokens[i]
            if t in self._VALUE_OPTS:
                if i + 1 >= len(tokens):
                    return 125, "", f"docker: invalid option value: {t}\n"
                val = tokens[i + 1]
                if t == "--name":
                    name = val
                elif t in ("-v", "--volume"):
                    volumes.append(val)
                i += 2
            elif t.startswith("--") and "=" in t:
                key, val = t.split("=", 1)
                if key == "--name":
                    name = val
                i += 1
            elif t.startswith("-"):
                i += 1
            else:
                positional.append(t)
                i += 1
        if not positional:
            return 125, "", "docker: missing image\n"
        image = positional[0]
        if name is None:
            name = "mock-auto-" + uuid.uuid4().hex[:6]
        if name in self.containers:
            return 125, "", (f"docker: Error response from daemon: Conflict. "
                             f"The container name \"/{name}\" is already in use\n")
        if image not in self.images:
            return 125, "", (f"docker: Error response from daemon: "
                             f"Unable to find image {image} locally\n")
        for v in volumes:
            host = v.split(":")[0]
            host_real = self._real(host)
            os.makedirs(host_real, exist_ok=True)
        cid = uuid.uuid4().hex[:12]
        self.containers[name] = {
            "id": cid, "image": image, "state": "running",
            "volumes": volumes, "has_ucm": self.images[image]["has_ucm"],
            "ucm_version": self.images[image]["ucm_version"],
        }
        return 0, cid + "\n", ""

    def _docker_run_entrypoint(self, tokens: str) -> Tuple[int, str, str]:
        try:
            i = tokens.index("--entrypoint")
            entry = tokens[i + 1]
            image = tokens[i + 2]
            c_idx = tokens.index("-c", i)
            payload = tokens[c_idx + 1]
        except (ValueError, IndexError):
            return 125, "", "docker: 无法解析 entrypoint 命令\n"
        if entry not in ("/bin/sh", "/bin/bash"):
            return 125, "", f"docker: entrypoint {entry} 不支持\n"
        if image not in self.images:
            return 125, "", (f"docker: Error response from daemon: "
                             f"Unable to find image {image} locally\n")
        if "pip show" in payload:
            return self._pip_show(self.images[image]["has_ucm"],
                                  self.images[image]["ucm_version"])
        return 0, "", ""

    def _pip_show(self, has_ucm: bool, version: str) -> Tuple[int, str, str]:
        if has_ucm:
            return (0, f"Name: uc-manager\nVersion: {version}\n"
                       "Location: /usr/local/lib/python3.10/site-packages\n", "")
        return (1, "WARNING: Package(s) not found: uc-manager, ucm\n", "")

    def _docker_ps(self, cmd: str) -> Tuple[int, str, str]:
        show_all = bool(re.search(r"\s-a\b", cmd))
        rows = []
        for name, c in self.containers.items():
            if c["state"] != "running" and not show_all:
                continue
            status = ("Up 2 minutes" if c["state"] == "running"
                      else "Exited (0) 1 minute ago")
            rows.append(f"{c['id']}\t{name}\t{c['image']}\t{status}\t{c['state']}")
        return 0, "\n".join(rows) + ("\n" if rows else ""), ""

    def _docker_exec(self, cmd: str) -> Tuple[int, str, str]:
        tokens = shlex.split(cmd)
        detach = "-d" in tokens
        try:
            i = tokens.index("exec")
            while tokens[i + 1].startswith("-"):
                i += 1
            container = tokens[i + 1]
        except (ValueError, IndexError):
            return 1, "", "docker exec: 参数错误\n"
        if container not in self.containers:
            return 1, "", f"Error: No such container: {container}\n"
        if self.containers[container]["state"] != "running":
            return 1, "", (f"Error response from daemon: Container {container} "
                           f"is not running\n")
        if "-c" not in tokens:
            return 0, "", ""
        payload = tokens[tokens.index("-c") + 1]
        code, out, err = self._exec_payload(container, payload)
        if detach:
            return code, "", ""
        return code, out, err

    def _exec_payload(self, container: str, payload: str) -> Tuple[int, str, str]:
        payload = payload.strip()
        # pkill ... || true
        try:
            words = shlex.split(payload)
        except ValueError:
            words = payload.split()
        if not words:
            return 0, "", ""
        head = words[0]
        if head == "mkdir":
            for d in words[2:]:
                os.makedirs(self._real(d), exist_ok=True)
            return 0, "", ""
        if head == "pip":
            c = self.containers[container]
            return self._pip_show(c["has_ucm"], c["ucm_version"])
        m = re.search(r"nohup bash (\S+) > (\S+) 2>&1", payload)
        if m:
            return self._launch_process(container, m.group(1), m.group(2))
        if head == "tail":
            try:
                n = int(words[2])
            except (ValueError, IndexError):
                n = 100
            path = words[-1]
            real = self._real(path)
            if not os.path.isfile(real):
                return 1, "", f"tail: cannot open '{path}' for reading: No such file or directory\n"
            lines = open(real, encoding="utf-8", errors="replace").read().splitlines()
            return 0, "\n".join(lines[-n:]) + "\n", ""
        if head == "pgrep":
            pattern = words[-1]
            count = sum(1 for p in self.processes if pattern in p["cmdline"])
            if "-c" in words:
                return 0, f"{count}\n", ""
            return (0, f"{self.processes[0]['pid']}\n", "") if count else (1, "", "")
        if head == "pkill":
            pattern = words[2] if len(words) > 2 else ""
            self.processes = [p for p in self.processes
                              if pattern not in p["cmdline"]]
            return 0, "", ""
        if payload.startswith("ray stop"):
            self.processes = [p for p in self.processes if "(ray" not in p["cmdline"]]
            return 0, "", ""
        return 0, "", ""

    def _launch_process(self, container: str, script: str, log: str) -> Tuple[int, str, str]:
        real = self._real(script)
        if not os.path.isfile(real):
            return 1, "", f"bash: {script}: No such file or directory\n"
        content = open(real, encoding="utf-8", errors="replace").read()
        ports = self._parse_ports(content)
        hints = []
        for kw in ("vllm serve", "sglang.launch_server", "ray start",
                   "mooncake_master", "load_balance_proxy_server"):
            if kw in content:
                hints.append(kw)
        cmdline = f"bash {script}"
        if hints:
            cmdline += " (" + ", ".join(hints) + ")"
        self.processes.append({
            "container": container, "script": script, "log": log,
            "ports": ports, "cmdline": cmdline, "pid": 10000 + len(self.processes),
        })
        log_real = self._real(log)
        os.makedirs(os.path.dirname(log_real), exist_ok=True)
        with open(log_real, "w", encoding="utf-8") as fh:
            fh.write(f"[ucm-mock] 模拟启动: {cmdline}\n"
                     f"[ucm-mock] 解析到服务端口: {ports}\n"
                     "INFO:     Started server process [12345]\n"
                     "INFO:     Waiting for application startup.\n"
                     "INFO:     Application startup complete.\n")
        return 0, "", ""

    @staticmethod
    def _parse_ports(content: str) -> List[int]:
        ports = set()
        for m in re.finditer(r"--port (\d+)", content):
            ports.add(int(m.group(1)))
        m = re.search(r"SERVER_PORT_START=(\d+)", content)
        if m:
            start = int(m.group(1))
            m2 = re.search(r"\bDP_LOCAL=(\d+)", content)
            count = int(m2.group(1)) if m2 else 1
            ports.update(range(start, start + count))
        return sorted(ports)

    def running_ports(self) -> set:
        return {p for proc in self.processes for p in proc["ports"]}

    def _docker_cp(self, cmd: str) -> Tuple[int, str, str]:
        tokens = shlex.split(cmd)
        if len(tokens) < 4:
            return 1, "", "docker cp: 参数错误\n"
        src, dst = tokens[2], tokens[3]
        if ":" in dst:
            cname, dpath = dst.split(":", 1)
            if cname not in self.containers:
                return 1, "", f"Error: No such container: {cname}\n"
            src_real, dst_real = self._real(src), self._real(dpath)
            if not os.path.exists(src_real):
                return 1, "", f"Error: {src}: no such file or directory\n"
            os.makedirs(os.path.dirname(dst_real), exist_ok=True)
            shutil.copyfile(src_real, dst_real)
            return 0, "", ""
        return 1, "", "docker cp: 仅支持 宿主机->容器 方向\n"

    # ------------------------------------------------------------ 文件系统
    def _find(self, cmd: str) -> Tuple[int, str, str]:
        m = re.match(r"^find (\S+)", cmd)
        if not m:
            return 1, "", "find: 参数错误\n"
        base = m.group(1)
        real = self._real(base)
        if not os.path.isdir(real):
            return 0, "", ""
        out = []
        for entry in os.listdir(real):
            if os.path.isdir(os.path.join(real, entry)):
                out.append(self._remote(os.path.join(real, entry)))
        out.sort()
        return 0, "\n".join(out[:200]) + ("\n" if out else ""), ""

    def _df(self, cmd: str) -> Tuple[int, str, str]:
        m = re.search(r"fstype (\S+)", cmd)
        if not m:
            return 1, "", "df: 参数错误\n"
        path = m.group(1)
        if not os.path.exists(self._real(path)):
            return 1, "", f"df: {path}: No such file or directory\n"
        if path.startswith("/mnt"):
            return 0, "Filesystem Type\nnfsserver:/export nfs4\n", ""
        return 0, "Filesystem Type\n/dev/sda1 ext4\n", ""

    def _mkdir_p(self, cmd: str) -> Tuple[int, str, str]:
        for d in shlex.split(cmd.replace("mkdir -p", "").strip()):
            os.makedirs(self._real(d), exist_ok=True)
        return 0, "", ""

    def _rm(self, cmd: str, recursive: bool) -> Tuple[int, str, str]:
        prefix = "rm -rf " if recursive else "rm -f "
        for p in shlex.split(cmd[len(prefix):]):
            real = self._real(p)
            if os.path.isdir(real) and recursive:
                shutil.rmtree(real, ignore_errors=True)
            elif os.path.isfile(real):
                os.unlink(real)
        return 0, "", ""

    def _curl(self, cmd: str) -> Tuple[int, str, str]:
        m = re.search(r"https?://(\S+):(\d+)/\S*", cmd)
        if not m:
            return 0, "000", ""
        port = int(m.group(2))
        return (0, "200", "") if port in self.running_ports() else (0, "000", "")

    def _cat(self, cmd: str) -> Tuple[int, str, str]:
        path = shlex.split(cmd)[-1]
        real = self._real(path)
        if not os.path.isfile(real):
            return 1, "", f"cat: {path}: No such file or directory\n"
        return 0, open(real, encoding="utf-8", errors="replace").read(), ""
