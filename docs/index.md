# UCM Deployer

**UCM (Unified Cache Management) 安装与环境预检工具 —— Windows 桌面版**

面向 vLLM / vLLM-Ascend / SGLang 运行环境（Ascend NPU / NVIDIA GPU），把 UCM 的安装与环境预检简化为四个向导步骤：

```
① 服务器管理 → ② 镜像构建 → ③ 容器创建 → ④ 环境预检
```

[快速开始 :material-rocket-launch:](guide/install.md){ .md-button .md-button--primary }
[下载最新版 exe](https://github.com/student-jhz/UCM-Deployer/releases/latest/download/UCM-Deployer.exe){ .md-button }

---

## 功能总览

![步骤1 界面](screenshots/step1-servers.png)

| 步骤 | 功能亮点 |
|---|---|
| **① 服务器管理** | SSH 登录信息本地加密保存/复用；单选登录；`npu-smi`/`nvidia-smi` 设备探测（型号/卡数/CPU 架构） |
| **② 镜像构建** | 基础镜像**关键字筛选**或上传 tar `docker load`；上传 wrapt/UCM/ucm-toolkit whl（附**在线下载链接**与即时反馈）；构建前自动**匹配校验**（设备平台 + whl 架构标记） |
| **③ 容器创建** | 选 UCM 镜像（**选择时自动校验**）；自动探测并映射实际存在的 Ascend 设备节点、驱动与工具挂载，兼容 Ascend950PR/A5；GPU 使用 `--runtime nvidia --gpus all`；docker run 命令**可编辑**后执行 |
| **④ 环境预检** | 容器内运行 `ucm-toolkit run precheck`（环境检查 + **可选带宽测试**）；UCM 与驱动版本支持多来源补齐；**预检参数可配置**；结果表格+详情区展示（中文名与排查建议） |
| **工具菜单** | 清理指定容器/镜像（关键字筛选、悬浮看全名） |

## 核心特性

- **单服务器流程**：一次登录一台服务器，全部操作作用于勾选的服务器
- **先展示、可编辑、再执行**：所有生成的命令完整展示给用户，可修改后再执行
- **自由跳转**：步骤间可任意跳转，前置条件在操作执行时拦截提示
- **凭据安全**：密码 Fernet 加密保存在本地（`~/.ucm_deployer/`）

## 环境要求

- 本地：Windows + 双击 `UCM-Deployer.exe` 即可（无需 Python）
- 目标服务器：Linux + SSH + Docker；Ascend 需 `npu-smi`，NVIDIA 需 `nvidia-smi`

## 相关链接

- [GitHub 仓库](https://github.com/student-jhz/UCM-Deployer)
- [下载发布版](https://github.com/student-jhz/UCM-Deployer/releases)
- [UCM 项目（unified-cache-management）](https://github.com/ModelEngine-Group/unified-cache-management)
