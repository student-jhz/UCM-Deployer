# UCM Deployer

**UCM (Unified Cache Management) 一键部署工具 —— Windows 桌面版**

面向 vLLM / vLLM-Ascend / SGLang 推理容器（Ascend NPU / NVIDIA GPU），把 UCM 的部署简化为三个向导步骤：

```
① 服务器管理 → ② 镜像构建 → ③ 容器创建
```

- **步骤1 服务器管理**：SSH 登录信息本地加密保存/复用；单选登录一台服务器；`npu-smi`/`nvidia-smi` 设备探测（型号/卡数/CPU 架构）
- **步骤2 镜像构建**：选服务器已有镜像（**关键字筛选**）或上传 tar 包 `docker load`；上传 wrapt whl、UCM whl、（可选）ucm-toolkit 源码包（输入框附**在线下载链接**）；构建前自动**校验服务器/镜像/安装包匹配**（设备平台 + whl 架构标记）；生成 Dockerfile 构建带 UCM 的镜像
- **步骤3 容器创建**：选 UCM 镜像（**选择时自动校验**镜像内 UCM）；自动识别卡数**全量映射加速卡**（NPU: 全部 davinci 设备 + 驱动挂载 + `--net=host`；GPU: `--runtime nvidia --gpus all -p 8000:8000`）；KV Cache / 模型只读 / 自定义挂载；docker run 命令**可编辑**后执行

所有生成的命令都是**先展示、可编辑、再执行**。

## 快速开始

### 直接使用 exe

双击 `UCM-Deployer.exe` 即可（无需 Python）。自检：

```powershell
.\UCM-Deployer.exe --smoke
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

推送 `v*` 标签即触发 GitHub Actions：云端跑全量测试 → PyInstaller 打包 → 产物自检（`--smoke`）→ 自动挂到对应 Release（`.github/workflows/release.yml`）：

```powershell
git tag v0.2.0 && git push origin v0.2.0
```

也支持在 Actions 页面手动运行（workflow_dispatch，指定标签）。

## 自验证体系（无真实服务器）

| 层级 | 手段 | 覆盖 |
|---|---|---|
| 单元测试 | `tests/`（FakeSSH 脚本化 paramiko 替身） | SSH/加密/注册表/设备解析/docker 解析/Dockerfile/命令生成/匹配校验 等 85 项 |
| GUI | offscreen 冒烟 | 三页面实例化、导航守卫、单选、镜像筛选、下载链接、whl 反馈、像素采样断言界面无黑块 |
| 产物 | `exe --smoke` | 打包后程序自检 |

## 文档

- [用户手册](docs/用户手册.md) —— 安装、三步操作详解、FAQ
- [架构设计](docs/architecture.md) —— 模块划分、关键决策

## 目录结构

```
UCM-Deployer/
├── main.py                       # GUI 入口（--smoke 无头自检）
├── ucm_deployer/
│   ├── core/                     # 核心逻辑（无 GUI 依赖）
│   │   ├── models.py             # 数据模型
│   │   ├── crypto.py             # 凭据加密
│   │   ├── ssh_client.py         # SSH/SFTP 封装
│   │   ├── server_registry.py    # 服务器注册表
│   │   ├── device_detector.py    # 设备/架构探测
│   │   ├── docker_manager.py     # docker 操作
│   │   ├── image_builder.py      # 匹配校验 + 镜像构建
│   │   └── container_manager.py  # docker run 生成/执行
│   ├── gui/                      # PySide6 三步向导
│   │   ├── widgets/              # 三个步骤页面 + 通用组件
│   │   ├── main_window.py        # 主窗口/导航守卫/状态栏
│   │   └── theme.py              # QSS 主题 + 图标
│   └── utils/                    # 日志/路径
├── tests/                        # 85 项测试（FakeSSH + GUI 冒烟）
├── docs/                         # 用户手册 / 架构文档
├── scripts/build_exe.ps1         # 打包脚本
└── ucm_deployer.spec             # PyInstaller 配置
```

## 依赖

- Python ≥ 3.9（开发/源码运行）
- paramiko（SSH/SFTP）、PySide6（GUI）、cryptography（凭据加密）
- 目标服务器：Linux + SSH + Docker；Ascend 需 npu-smi，NVIDIA 需 nvidia-smi
