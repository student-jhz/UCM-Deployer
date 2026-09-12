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

- 单元/集成测试：131 通过 / 6 跳过（bash -n：本机 WSL 损坏，有 bash 的环境自动启用；真实服务器测试：需环境变量 `UCM_TEST_REAL_HOST`）
- CLI 自检：`self-test` ascend/nvidia 均通过
- 期间发现并修复的问题：paramiko 5.0 SFTPHandle 构造签名（首参为 flags）、SFTP open() 收到的是 os.* 标志、模拟服务器 exec worker 与 paramiko 包线程的 EOF 竞态（服务端不主动 close）、offscreen 模式模态对话框阻塞（自动应答）。

## 8. 交互漏洞 / 美工 / 易用性专项审计（阶段8）

对 GUI 全量代码审查后发现并修复 12 项交互漏洞：

| # | 漏洞 | 修复 |
|---|---|---|
| 1 | `panel.finished_all.connect(on_done)` 手动 connect/disconnect 模式：任务被拒（忙碌）时残留连接，下次完成时误触发旧回调 | 面板新增 `run_tasks(tasks, title, on_finished)`，回调一次性连接、内部管理 |
| 2 | `_on_done` 中 `len(self._results) == len(self._threads)` 判等：取消后重开任务时线程列表重建，可能出现永不触发 finished_all 的竞态 | 改为 `_total` 计数器 + `len(results) >= total` |
| 3 | 上一轮 QThread 对象未清理即被覆盖，运行中重开触发 "QThread: Destroyed while thread is still running" 崩溃 | start() 前清理已结束线程；新增 `shutdown()`（退出窗口时取消+等待） |
| 4 | 步骤2 镜像列表只在 `_image_lists` 全空时刷新：换服务器组合后新服务器下拉为空 | 改为检查「任一已选服务器缺失」即刷新；已有列表直接回填 |
| 5 | 步骤3/4 容器下拉框只在步骤3手动「检查容器」后才回填 ctx.containers，用户跳过时节点表容器列为空 | 步骤4 进入时自动异步刷新容器列表并同步节点表（`_sync_node_containers`） |
| 6 | **步骤3 全部服务器共用一个命令编辑框**：卡数不同的服务器生成的命令被最后一台覆盖，多机部署实际执行错误命令 | 每台服务器独立命令页签，编辑互不影响 |
| 7 | 步骤4 重新生成脚本静默覆盖用户编辑 | 检测编辑差异后弹确认框（No 保留） |
| 8 | 步骤5 端口被占用等失败时仍继续拉后续脚本 | 失败即停并提示，可单独重试 |
| 9 | 「全部拉起」运行时点单脚本「拉起」产生并发竞态 | `_launching` 运行锁 + 单发按钮完成前禁用 |
| 10 | 关闭窗口直接退出，后台任务线程被强杀（SSH 残留/资源泄漏） | closeEvent 检测运行中任务 → 确认 → `panel.shutdown()` 优雅等待 |
| 11 | 服务器表格勾选后 ctx.selected 才更新，但步骤2-4的旧数据未失效 | 删除服务器时清理 devices/镜像/容器并广播 |
| 12 | 模态对话框在无头/打包环境可能永久阻塞 | offscreen 自检路径自动应答 |

美工改进：

- 新增 `gui/theme.py`：全局 QSS（Fusion 风格 + 蓝色主色 #2f6fed），统一卡片式 QGroupBox、圆角输入框、accent 主按钮、导航高亮、表格斑马纹、自定义滚动条/进度条/菜单/Tooltip；
- 程序图标代码生成（蓝渐变 UCM 方块，无资源文件）；
- 进度文本升级为真实 QProgressBar；日志/脚本编辑器统一等宽字体。

易用性改进：

- 主窗口：菜单栏（文件/帮助：用户手册、打开日志目录、打开配置目录、自检、关于）+ **状态栏实时汇总**（已选服务器/镜像/容器/脚本/后台任务数，1s 刷新）+ 窗口几何记忆；
- 空态占位提示（未选服务器时步骤2/3 显示引导文案）；文件选择对话框记忆上次目录（QSettings）；
- 文件/目录选择、关键参数（模型路径、served-model-name、UCM 配置、网卡）增加 tooltip；
- 远端目录浏览对话框支持多服务器切换（跨服务器挑目录）；
- 健康检查失败时给出明确后续指引文案。

### 8.1 美术自验证方式

本环境无法人工目检截图，采用：离屏渲染五页面截图（1320×880，13-27KB 有效内容，无异常）+ 全部 GUI 测试在 QSS 应用状态下通过 + exe 产物 selftest/smoke 退出码 0。

### 8.2 新增测试

- `run_tasks` 一次性回调（拒绝时不残留、可重复运行）
- 每服务器独立命令页签（8卡/16卡生成不同命令、编辑互不影响）
- 重新生成确认（No 保留 / Yes 覆盖）
- `sync_edits` 编辑回写

累计 131 项测试全部通过。
| 4 | topology + command_generator | ⬜ |
| 5 | mock server + CLI + e2e | ⬜ |
| 6 | GUI | ⬜ |
| 7 | 打包 + 用户手册 | ⬜ |
