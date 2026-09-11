# UCM Deployer 总体架构设计

> 对应需求：根目录 `design.md`（用户需求原文）
> 本文是随代码迭代的设计文档，每个阶段提交时更新「模块实现记录」。

## 1. 目标

在 Windows 上提供一个桌面程序（单个 exe），辅助运维人员把 UCM（Unified Cache Management）部署到多台 GPU/NPU 服务器的 vLLM / vLLM-Ascend / SGLang 容器集群中，完成从「服务器选择 → 镜像构建 → 容器创建 → 推理服务部署 → 拉起」的全流程。

## 2. 技术选型

| 项 | 选择 | 理由 |
|---|---|---|
| 语言/运行时 | Python 3.9+ | 生态成熟、易打包 |
| GUI | PySide6 (Qt6) | 原生观感、表格/树/多线程信号槽成熟、PyInstaller 支持好 |
| SSH | paramiko | 事实标准，支持 exec + SFTP |
| 凭据加密 | cryptography (Fernet) | paramiko 自带依赖，本地密钥文件加密保存密码 |
| 并发模型 | 每服务器一个 QThread | 多服务器并行操作，进度/日志互不阻塞 |
| 打包 | PyInstaller (onedir/windowed) | 产出 `UCM-Deployer.exe` |
| 测试 | pytest + FakeSSH + Mock SSH Server | 无真实服务器环境下的自验证 |

## 3. 分层架构

```
┌────────────────────────────────────────────────────────┐
│ GUI (PySide6)   5 步向导：服务器/镜像/容器/部署/拉起      │
│   gui/main_window.py + gui/widgets/*                   │
├────────────────────────────────────────────────────────┤
│ Service 编排    ucm_deployer/service/*                  │
│   把 core 能力组合成「每服务器一个任务」的闭包           │
├────────────────────────────────────────────────────────┤
│ Core 核心逻辑   ucm_deployer/core/*（不依赖 Qt）        │
│   ssh_client   server_registry   crypto    models      │
│   device_detector   docker_manager   image_builder      │
│   container_manager   topology   command_generator      │
├────────────────────────────────────────────────────────┤
│ 自验证设施                                             │
│   tests/fake_ssh.py      单元测试级：脚本化 paramiko 替身 │
│   ucm_deployer/mock/*    进程级：本地模拟 SSH 服务器      │
│                          （模拟 npu-smi/docker/文件系统）│
└────────────────────────────────────────────────────────┘
```

## 4. 五步流程与模块映射

| 步骤 | 功能 | 模块 |
|---|---|---|
| 步骤1 | 服务器登录信息保存/复用、选择部署服务器、设备型号一致性校验 | `server_registry` + `device_detector` + `ssh_client` |
| 步骤2 | 基础镜像选择/上传 tar 并 docker load、上传 UCM whl（离线时含 wrapt whl）、构建带 UCM 镜像 | `docker_manager` + `image_builder` |
| 步骤3 | 校验镜像内 UCM、配置 kvcache 挂载（共享文件系统校验）/模型只读映射/其他映射、自动识别卡数生成并执行 docker run | `docker_manager` + `container_manager` |
| 步骤4 | 选择容器并校验 UCM、PD 混部/PD 分离拓扑、卡资源占用检查、自动生成 vllm/sglang + UCM kv-transfer-config 启动脚本（用户可编辑） | `topology` + `command_generator` |
| 步骤5 | ray 集群（多机混部）/ mooncake master（PD）、逐节点拉起、日志跟踪、健康检查 | `command_generator` 产物 + `service/deploy_service` |

## 5. 关键设计决策

### 5.1 无真实服务器的自验证（三层）

1. **单元测试（FakeSSH）**：`tests/fake_ssh.py` 用脚本化替身替换 `paramiko.SSHClient`，所有 core 模块可在纯本机跑单测；
2. **模拟服务器（Mock Server）**：`ucm_deployer.mock` 在本机 127.0.0.1 起一个真 SSH 服务（paramiko 服务端），模拟 npu-smi/nvidia-smi/docker/文件系统/网卡等行为，可对 GUI/CLI 做**端到端**联调；
3. **GUI 冒烟测试**：`QT_QPA_PLATFORM=offscreen` 下实例化全部页面验证无异常。

### 5.2 密码安全

- 保存目录：`~/.ucm_deployer/servers.json`（密码密文）+ `~/.ucm_deployer/.secret.key`（Fernet 密钥）；
- 密钥与密文同目录本地保存，防止 servers.json 被拷贝后直接泄露明文；属内网运维工具的折中方案。

### 5.3 命令生成策略

- 所有生成的 docker run / vllm serve 脚本**必须**完整展示给用户（可编辑）后再执行；
- 生成器只负责「确定性的部分」（设备映射、DP/TP 分配、kv-transfer-config、端口规划），其余参数由用户补充；
- 生成脚本统一落到容器内 `/root/ucm-deploy/`，通过 `docker exec -d + nohup` 后台拉起，日志重定向到文件供 GUI tail。

### 5.4 多服务器任务模型

GUI 对「每台服务器」起一个 QThread，任务函数签名统一为
`fn(ssh, ctx)`，其中 `ctx` 提供 `progress(pct, msg)` / `log(line)` / `cancelled()` 回调；
core 层完全不知道 Qt 的存在，便于复用与测试。

## 6. 目录结构

```
UCM-Deployer/
├── main.py                    # GUI 入口
├── cli.py                     # CLI 入口（无 GUI 自检/演示）
├── ucm_deployer/
│   ├── core/                  # 核心逻辑（无 Qt 依赖）
│   │   ├── models.py          # 数据模型
│   │   ├── shell.py           # 远端 shell 引号工具
│   │   ├── crypto.py          # 凭据加密
│   │   ├── ssh_client.py      # SSH/SFTP 封装
│   │   ├── server_registry.py # 服务器注册表
│   │   ├── device_detector.py # 设备探测（阶段2）
│   │   ├── docker_manager.py  # docker 操作（阶段2）
│   │   ├── image_builder.py   # UCM 镜像构建（阶段3）
│   │   ├── container_manager.py # 容器创建（阶段3）
│   │   ├── topology.py        # 拓扑规划与校验（阶段4）
│   │   └── command_generator.py # 启动命令生成（阶段4）
│   ├── service/               # 编排层（阶段4-5）
│   ├── gui/                   # PySide6 界面（阶段6）
│   ├── mock/                  # 模拟 SSH 服务器（阶段5）
│   └── utils/                 # 日志/路径
├── tests/                     # pytest + FakeSSH
├── docs/                      # 文档
├── scripts/                   # 打包脚本
└── ucm_deployer.spec          # PyInstaller 配置
```

## 7. 阶段计划与实现记录

| 阶段 | 内容 | 状态 |
|---|---|---|
| 1 | 基础框架：models/crypto/ssh_client/server_registry + 26 项单元测试 | ✅ 已提交 |
| 2 | device_detector（npu-smi/nvidia-smi 解析、型号一致性校验）+ docker_manager（镜像/容器/UCM 检查/load/build/exec/cp）+ 25 项测试 | ✅ 已提交 |
| 3 | image_builder（Dockerfile 生成/whl 上传/构建/校验/取消）+ container_manager（Ascend/NVIDIA docker run 生成、共享文件系统校验、目录浏览）+ 23 项测试 | ✅ 已提交 |
| 4 | topology（混部/PD 拓扑建模与校验、DP 进程分配）+ command_generator（vLLM/SGLang + UCM 全量脚本生成）+ resource_checker + service/deploy_service（部署/拉起/日志/健康检查编排）+ 35 项测试 | ✅ 已提交 |
| 5 | mock 模拟 SSH 服务器（paramiko 服务端 + SFTP + docker/npu-smi 状态机）+ CLI（mock-server/self-test/check/build）+ 端到端测试 13 项 | ✅ 已提交 |
| 6 | PySide6 GUI 五步向导（服务器/镜像/容器/部署/拉起），ParallelTaskPanel 多服务器并行进度+日志，脚本编辑器，5 项 GUI 测试（含模拟服务器全链路） | ✅ 已提交 |
| 7 | PyInstaller 打包（单文件窗口 exe 46.7MB，`--selftest`/`--smoke` 产物级自检通过）+ 用户手册 + README | ✅ 已提交 |

### 7.1 打包产物自验证

`dist\UCM-Deployer.exe` 构建后执行：

- `UCM-Deployer.exe --selftest --out report.txt` → 退出码 0，15 步全流程（模拟服务器）在打包环境中通过；
- `UCM-Deployer.exe --smoke` → 退出码 0，五页面 offscreen 实例化通过。

### 7.2 测试总账

- 单元/集成测试：127 通过 / 6 跳过（bash -n：本机 WSL 损坏，有 bash 的环境自动启用；真实服务器测试：需环境变量 `UCM_TEST_REAL_HOST`）
- CLI 自检：`self-test` ascend/nvidia 均通过
- 期间发现并修复的问题：paramiko 5.0 SFTPHandle 构造签名（首参为 flags）、SFTP open() 收到的是 os.* 标志、模拟服务器 exec worker 与 paramiko 包线程的 EOF 竞态（服务端不主动 close）、offscreen 模式模态对话框阻塞（自动应答）。
| 4 | topology + command_generator | ⬜ |
| 5 | mock server + CLI + e2e | ⬜ |
| 6 | GUI | ⬜ |
| 7 | 打包 + 用户手册 | ⬜ |
