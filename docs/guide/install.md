# 安装与启动

## 方式一：直接使用 exe（推荐）

从 [Releases 直接下载最新版 `UCM-Deployer.exe`](https://github.com/student-jhz/UCM-Deployer/releases/latest/download/UCM-Deployer.exe)，双击即可运行（无需安装 Python）。

产物自检（可选）：

```powershell
.\UCM-Deployer.exe --smoke
```

## 方式二：源码运行 / 开发

```powershell
git clone https://github.com/student-jhz/UCM-Deployer.git
cd UCM-Deployer
python -m venv .venv                       # Python 3.9+
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest             # 全量自检测试
.venv\Scripts\python main.py               # 启动 GUI
```

## 目标服务器要求

| 项 | 要求 |
|---|---|
| 系统 | Linux + SSH + Docker |
| Ascend NPU 服务器 | 宿主机有 `npu-smi`，驱动目录可挂载 |
| NVIDIA GPU 服务器 | 宿主机有 `nvidia-smi`，nvidia-container-runtime 可用 |

## 准备安装包（步骤2 需要）

| 包 | 获取途径 | 说明 |
|---|---|---|
| **UCM whl** | [GitHub Releases](https://github.com/ModelEngine-Group/unified-cache-management/releases) | `uc_manager-*.whl`（含平台变体包名） |
| **wrapt whl** | [PyPI wrapt 1.17.2](https://pypi.org/project/wrapt/1.17.2/#files) | 选择匹配服务器 Python 版本与 CPU 架构的 whl（manylinux x86_64 / aarch64） |
| ucm-toolkit whl（可选） | [GitHub Releases](https://github.com/ModelEngine-Group/unified-cache-management/releases)（与 UCM 同站点） | 预检工具（步骤4 需要） |

> 界面步骤2 中每个包输入框下方都有**可点击的下载链接**，本地没有包时直达浏览器下载。

## 打包 exe（开发者）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
# 先跑全部测试，再产出 dist\UCM-Deployer.exe
```

推送 `v*` 标签触发 GitHub Actions 云端构建并自动发布 Release。
