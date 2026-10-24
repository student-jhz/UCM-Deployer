# UCM Deployer

**UCM (Unified Cache Management) 安装与环境预检工具 —— Windows 桌面版**

:book: **[在线使用文档（含界面截图）](https://student-jhz.github.io/UCM-Deployer/)**

面向 vLLM / vLLM-Ascend / SGLang 运行环境（Ascend NPU / NVIDIA GPU），把 UCM 安装与环境预检简化为四个向导步骤：

```
① 服务器管理 → ② 镜像准备 → ③ 容器准备 → ④ 环境预检
```

- **步骤1 服务器管理**：SSH 登录信息本地加密保存/复用；单选登录一台服务器；`npu-smi`/`nvidia-smi` 设备探测（型号/卡数/CPU 架构）
- **步骤2 镜像准备**：选服务器已有镜像（**关键字筛选**）、上传 tar 包 `docker load`，或输入镜像仓库地址（如 `quay.io/ascend/vllm-ascend:v0.30.0rc2-a5-openeuler`）由本地 Docker **按目标服务器架构拉取**、导出 `tar.gz` 并自动上传加载；已含 UCM 的镜像可直接复用并跳过构建；确需构建时上传 wrapt/UCM/ucm-toolkit whl，构建前自动校验设备平台与架构
- **步骤3 容器准备**：选 UCM 镜像（**选择时自动校验**镜像内 UCM）；自动探测宿主机实际存在的 Ascend 设备节点与驱动/工具挂载，兼容 Ascend 950PR/A5（缺失 `/dev/devmm_svm` 时不再生成该参数，存在 `/dev/ummu` 或 `/dev/uburma` 时自动添加 `--privileged=true`）；GPU 使用 `--runtime nvidia --gpus all -p 8000:8000`；KV Cache / 模型只读 / 自定义挂载；docker run 命令**可编辑**后执行；已有 UCM 容器可跳过创建
- **步骤4 环境预检**：**显式选择目标容器**（刷新 `docker ps -a` 后选择已有容器或输入容器名）；在所选容器内运行 `ucm-toolkit run precheck`（驱动/内核/共享内存/AIO 检查 + **可选挂载目录带宽测试**）；当 toolkit 无法识别 UCM 平台变体或 Ascend 驱动版本时，自动用 pip 元数据、`version.info`、`npu-smi`、`nvidia-smi` 多来源补齐并标注来源；**预检参数可配置**；结果**表格+详情区展示**（PASS/WARN/FAIL/INFO 着色、中文名与排查建议）
- **工具菜单**：清理指定容器（`docker ps -a` 勾选删除）/ 清理指定镜像（`docker images` 勾选删除），作用于当前登录的服务器
- 步骤间**自由跳转**（前置条件在操作执行时拦截）；所有生成的命令**先展示、可编辑、再执行**；本工具负责 UCM 安装与环境预检，不负责启动或部署推理服务

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
| 单元测试 | `tests/`（FakeSSH 脚本化 paramiko 替身） | SSH/加密/注册表/设备解析/docker 解析/Dockerfile/命令生成/匹配校验 等 155 项 |
| GUI | offscreen 冒烟 | 三页面实例化、导航守卫、单选、镜像筛选、下载链接、whl 反馈、像素采样断言界面无黑块 |
| 产物 | `exe --smoke` | 打包后程序自检 |

## 文档

- [用户手册](docs/用户手册.md) —— 安装、四步操作详解、FAQ
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
│   ├── gui/                      # PySide6 四步向导
│   │   ├── widgets/              # 三个步骤页面 + 通用组件
│   │   ├── main_window.py        # 主窗口/导航守卫/状态栏
│   │   └── theme.py              # QSS 主题 + 图标
│   └── utils/                    # 日志/路径
├── tests/                        # 155 项测试（FakeSSH + GUI 冒烟）
├── docs/                         # 用户手册 / 架构文档
├── scripts/build_exe.ps1         # 打包脚本
└── ucm_deployer.spec             # PyInstaller 配置
```

## 依赖

- Python ≥ 3.9（开发/源码运行）
- paramiko（SSH/SFTP）、PySide6（GUI）、cryptography（凭据加密）
- 目标服务器：Linux + SSH + Docker；Ascend 需 npu-smi，NVIDIA 需 nvidia-smi
