# UCM Deployer

**UCM (Unified Cache Management) 一键部署工具 —— Windows 桌面版**

面向 vLLM / vLLM-Ascend / SGLang 推理集群（Ascend NPU / NVIDIA GPU），把 UCM 的部署从手工操作变成五个向导步骤：

```
① 服务器管理 → ② 镜像构建 → ③ 容器创建 → ④ 部署配置 → ⑤ 拉起服务
```

- **步骤1 服务器管理**：SSH 登录信息本地加密保存/复用；选择本次部署的服务器；`npu-smi`/`nvidia-smi` 设备探测与多机型号一致性校验
- **步骤2 镜像构建**：服务器基础镜像选择，或上传 tar 包 `docker load`；上传本地 UCM whl（离线模式含 wrapt whl、可选 ucm-toolkit 源码安装）自动生成 Dockerfile 并 `docker build` 成带 UCM 的引擎镜像；已有 UCM 镜像可校验后跳过
- **步骤3 容器创建**：自动识别卡数生成全量 `docker run`（Ascend 全部 davinci 设备 + 驱动挂载 / NVIDIA `--gpus all`）；kvcache 挂载目录共享文件系统校验；模型只读映射；命令可编辑后执行
- **步骤4 部署配置**：PD 混部 / PD 分离拓扑（每节点 DP×TP 卡数校验）；卡资源占用检查；自动生成含 UCM `--kv-transfer-config` 的 vllm/sglang 启动脚本（mooncake master/mooncake.json/ray 集群/负载均衡/UCM 配置模板），**全部可编辑**
- **步骤5 拉起服务**：脚本 `docker cp` 落位、按依赖顺序拉起、`curl /health` 健康检查、容器日志跟踪、一键停止

所有生成的命令与脚本都是**先展示、可编辑、再执行**。

## 快速开始

### 直接使用 exe

双击 `UCM-Deployer.exe` 即可（无需 Python）。自检：

```powershell
.\UCM-Deployer.exe --selftest --out report.txt
```

### 源码运行 / 开发

```powershell
python -m venv .venv                       # Python 3.9+
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest             # 全部自检测试
.venv\Scripts\python main.py               # GUI
.venv\Scripts\python main.py --smoke       # 无头 GUI 冒烟
```

### 打包 exe

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
# 先跑全部测试，再产出 dist\UCM-Deployer.exe
```

### 云端自动构建发布（推荐）

推送 `v*` 标签即触发 GitHub Actions：云端跑全量测试 → PyInstaller 打包 → 产物自检（`--selftest`/`--smoke`）→ 自动挂到对应 Release（`.github/workflows/release.yml`）：

```powershell
git tag v0.2.0 && git push origin v0.2.0
```

也支持在 Actions 页面手动运行（workflow_dispatch，指定标签）。本地 `scripts\create_release.ps1` 为无 Actions 时的离线备选。

## 无真实服务器？用内置模拟服务器

```powershell
.venv\Scripts\python -m ucm_deployer.mock.mock_server --port 2222 --device ascend --cards 8
```

然后在 GUI 中添加服务器 `127.0.0.1:2222`（root/root），即可体验/验证**完整五步流程**（模拟 npu-smi、docker load/build/run/exec/cp、健康检查等）。

## 自验证体系（无真实环境）

| 层级 | 手段 | 覆盖 |
|---|---|---|
| 单元测试 | `tests/`（FakeSSH 脚本化 paramiko 替身） | SSH/加密/注册表/设备解析/docker 解析/Dockerfile/命令生成/拓扑校验 等 127 项 |
| 端到端 | 真实 paramiko 客户端 ↔ 本地模拟 SSH 服务器 | 15 步全流程 + 双服务器 PD 分离 |
| GUI | offscreen 冒烟 + GUI×模拟服务器集成测试 | 五页面实例化、多线程任务面板、真实构建/创建/生成 |
| 产物 | `exe --selftest` / `exe --smoke` | 打包后程序自检 |

生成的 bash 脚本在可用环境下执行 `bash -n` 语法检查；不可用时退化为引号配平 + 续行 + 内嵌 kv JSON 解析的静态检查。

## 文档

- [用户手册](docs/用户手册.md) —— 安装、五步操作详解、CLI、FAQ
- [架构设计](docs/architecture.md) —— 模块划分、关键决策、阶段实现记录
- [需求原文](design.md)

## 目录结构

```
UCM-Deployer/
├── main.py / cli.py            # GUI / CLI 入口
├── ucm_deployer/
│   ├── core/                   # 核心逻辑（无 GUI 依赖）
│   ├── service/                # 部署编排（脚本落位/拉起/健康检查）
│   ├── gui/                    # PySide6 五步向导
│   ├── mock/                   # 模拟 SSH 服务器 + 自检
│   └── utils/                  # 日志/路径
├── tests/                      # 127 项测试
├── docs/                       # 用户手册 / 架构文档
├── scripts/build_exe.ps1       # 打包脚本
└── ucm_deployer.spec           # PyInstaller 配置
```

## 依赖

- Python ≥ 3.9（开发/源码运行）
- paramiko（SSH/SFTP）、PySide6（GUI）、cryptography（凭据加密）
- 目标服务器：Linux + SSH + Docker；Ascend 需 npu-smi，NVIDIA 需 nvidia-smi
