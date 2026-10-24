# UCM Deployer 总体架构设计

> v0.2.0 起为单服务器三步流程的精简重写版。

## 1. 目标

在 Windows 上提供一个桌面程序（单个 exe），辅助运维人员在一台服务器上的
vLLM / vLLM-Ascend / SGLang 运行容器中安装 UCM 并完成环境预检，
覆盖「服务器登录 → 镜像准备 → 容器准备 → 环境预检」流程。

暂缓（架构预留，未实现）：部署配置（PD 拓扑/启动脚本生成）与服务拉起。

## 2. 技术选型

| 项 | 选择 | 理由 |
|---|---|---|
| 语言/运行时 | Python 3.9+ | 生态成熟、易打包 |
| GUI | PySide6 (Qt6) | 原生观感、表格/多线程信号槽成熟、PyInstaller 支持好 |
| SSH | paramiko | 事实标准，支持 exec + SFTP |
| 凭据加密 | cryptography (Fernet) | 本地密钥文件加密保存密码 |
| 并发模型 | Python `threading.Thread` + Qt 信号投递 | 见 5.1（QThread 在此环境有清理竞态） |
| 打包 | PyInstaller (单文件/windowed) | 产出 `UCM-Deployer.exe` |
| 测试 | pytest + FakeSSH | 无真实服务器环境下的自验证 |

依赖锁定：`cryptography<46`（历史提交 5e83e69：≥46 与 Qt 共存在 Windows 上有线程崩溃）。

## 3. 分层架构

```
┌────────────────────────────────────────────────────────┐
│ GUI (PySide6)   4 步向导：服务器/镜像/容器/预检          │
│   main_window.py + widgets/{server,image,container,     │
│   precheck}_page；工具菜单: cleanup_dialog.py           │
├────────────────────────────────────────────────────────┤
│ Core 核心逻辑   ucm_deployer/core/*（不依赖 Qt）        │
│   ssh_client  server_registry  crypto  models  shell    │
│   device_detector  docker_manager                       │
│   image_builder  container_manager  precheck            │
├────────────────────────────────────────────────────────┤
│ 自验证设施                                              │
│   tests/fake_ssh.py    单元测试级：脚本化 paramiko 替身   │
└────────────────────────────────────────────────────────┘
```

## 4. 三步流程与模块映射

| 步骤 | 功能 | 模块 |
|---|---|---|
| 步骤1 | 登录信息保存/复用、单选登录、设备与架构探测 | `server_registry` + `device_detector` + `ssh_client` |
| 步骤2 | 基础镜像选择（筛选）/上传 tar、上传 wrapt/UCM/ucm-toolkit whl、匹配校验、构建 | `docker_manager` + `image_builder` |
| 步骤3 | 选 UCM 镜像（自动校验）、全卡映射 docker run 生成/编辑/执行、挂载管理 | `container_manager` |
| 步骤4 | 容器内 ucm-toolkit precheck（环境检查 + 挂载目录带宽测试，临时子目录自动清理） | `precheck` |

导航策略：各步之间**允许自由跳转**（不做前置条件校验）；前置条件
（已登录服务器/已确定 UCM 镜像/已创建容器）由各步骤内的操作在执行时拦截提示。
步骤3/4 页面紧凑排版，1280x860 一屏可见。

## 5. 关键设计决策

### 5.1 任务线程：Python threading + Qt 信号（而非 QThread）

后台任务（SSH 操作）用 `threading.Thread` 承载，信号经 `QObject` 容器
（`TaskSignals`/`QuickSignals`）跨线程 emit（Qt 自动队列投递到 GUI 线程）。

原因：paramiko 等纯 Python 库在 Qt C++ 线程（QThread）中运行存在清理竞态
——QThread 结束时 shiboken 清理临时线程状态与解释器交互，曾稳定复现
access violation（0xC0000005）。Python 线程由解释器统一管理，且消除了
`threading._DummyThread` 壳残留。`SSHClient.close()` 会 `transport.join(3)`
等待 paramiko 线程退出，避免残留线程。

### 5.2 测试收尾的进程级保险（tests/conftest.py）

Windows + PySide6 在本类项目上有两个已实测复现的收尾崩溃：
1. 主线程 GC 清理与工作线程交互的 Qt 对象 wrapper 时偶发 access violation；
2. 进程退出阶段 Qt DLL 卸载钩子（DLL_PROCESS_DETACH，连 `os._exit` 也会触发）
   崩溃。

规避：`pytest_runtestloop` hookwrapper 中——测试前 `gc.freeze()` + `gc.disable()`；
测试循环一结束立即 `TerminateProcess`（不执行任何 DLL 卸载钩子）。
**注意 ctypes 细节**：`GetCurrentProcess` 返回 64 位伪句柄 -1，必须显式声明
`argtypes=[c_void_p, c_uint]`，否则默认 int 转换溢出抛 `ArgumentError`、
TerminateProcess 从未执行（这正是最初"保险失效"的原因）。

副作用：pytest 统计行被截断（dots 已实时打印），退出码与 pytest 一致；
`UCM_TEST_NO_EARLY_EXIT=1` 可关闭该保险用于调试（可能触发收尾崩溃）。

### 5.3 密码安全

保存目录：`~/.ucm_deployer/servers.json`（密码密文）+ `~/.ucm_deployer/.secret.key`
（Fernet 密钥）。密钥与密文同目录本地保存，防止 servers.json 被拷贝后直接泄露
明文；属内网运维工具的折中方案。

### 5.4 匹配校验（步骤2 核心保障）

构建前强制校验「服务器设备 / 基础镜像 / 安装包」匹配（`image_builder.validate_build_match`）：
- 设备已探测且卡数 > 0（否则错误）；
- 镜像平台（按名称推断 ascend/cann → ascend；nvidia/cuda → nvidia）与设备类型一致
  （无法识别降级为警告）；
- whl 文件名架构标记（`whl_arch`：manylinux2014_x86_64/aarch64 等）与服务器
  `uname -m` 一致（纯 Python 包跳过）。

### 5.5 命令生成策略

- 所有 docker run 命令**必须**完整展示给用户（可编辑）后再执行；
- 生成器只负责「确定性的部分」（设备映射、挂载、--net/--gpus/--runtime），
  其余参数由用户在编辑框补充；
- `shell.shq` 智能引号：安全字符集内字符串不加引号（命令可读），
  含空格/单引号才包裹。

### 5.6 易用性内建

- 镜像下拉：可编辑筛选 + 补全器包含匹配 + 弹层限高 + 只能选列表项
  （`common.setup_image_combo`）；
- 安装包输入框下方附在线下载链接（UCM → GitHub Releases；wrapt → PyPI 1.17.2）；
- whl 选择即时反馈（文件有效性 + 架构标记解析），帮助用户在上传前确认匹配；
- 步骤3 选择镜像时自动后台校验镜像内 UCM；
- 文件/目录选择记忆上次目录（QSettings）；窗口几何记忆。

## 6. 测试体系

- **单元测试（FakeSSH）**：`tests/fake_ssh.py` 用脚本化替身（实例级/类级两级注册，
  LIFO 匹配）替换 `paramiko.SSHClient`，覆盖内存文件系统/SFTP 记录/流式 channel，
  所有 core 模块可在纯本机跑单测；
- **GUI 冒烟**：`QT_QPA_PLATFORM=offscreen` 下实例化页面验证交互逻辑
  （自由跳转、操作拦截、镜像筛选、命名建议联动、一屏排版断言）；
  界面渲染质量用像素采样断言（不依赖人工看图）；
- 共 155 项测试。

## 7. 发布流程

`scripts/build_exe.ps1`：venv → 依赖 → 全量测试 → PyInstaller 单文件 exe。
CI（`.github/workflows/release.yml`）：推送 `v*` 标签 → 云端测试/打包/自检
（`--smoke`）→ 挂 Release。
