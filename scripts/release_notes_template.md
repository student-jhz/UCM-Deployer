# UCM Deployer v0.3.6

**UCM (Unified Cache Management) 安装与环境预检工具 —— Windows 桌面版**

面向 vLLM / vLLM-Ascend / SGLang 运行环境（Ascend NPU / NVIDIA GPU），提供四步向导：

```
① 服务器管理 → ② 镜像准备 → ③ 容器准备 → ④ 环境预检
```

## 本版本更新

- **基础镜像运行时探测**：自动检测 Python 版本/ABI、CPU 架构、glibc、CANN/CUDA 与 Ascend A2/A3/A5 变体，并给出建议的 UCM/wrapt wheel 文件名。
- **安装包精确匹配**：构建前校验 wrapt、UCM、ucm-toolkit 的包名、Python ABI、架构、manylinux/glibc 要求，以及 UCM 的 CANN/CUDA runtime 和设备变体；`cu129`/`cu130` 解析已修复。
- **只创建容器、不启动推理服务**：步骤3 生成的 `docker run` 固定使用 `--entrypoint /bin/bash`，不再在镜像后追加 `bash`，避免触发 vLLM 默认入口或把 `bash` 当作模型参数。
- **命令执行防护**：即使用户编辑过命令，创建前也会校验入口仍是 `/bin/bash`，并拒绝移除入口或在镜像后追加 `bash`。
- **文档与截图**：更新步骤2 运行时匹配说明和步骤3 安全入口示例，截图继续使用中文字体渲染，避免小方框。
- **服务器直连拉取镜像**：输入镜像仓库地址后，在目标服务器执行 `docker pull --platform`；本地无需 Docker Desktop，也不再导出或上传 tar.gz。
- **命令透明**：服务器拉取前展示可编辑命令，且仅允许以 `docker pull` 开头的命令执行。
- **重设计步骤2镜像来源**：默认「服务器已有镜像」，无需填写仓库地址；离线场景保留 tar 导入；仓库场景独立显示，避免多个输入框和按钮同时出现。
- **自动回到复用流程**：tar 导入或服务器拉取成功后自动切回「服务器已有镜像」并选中新镜像。
- **修复来源切换显示**：选择本地镜像包或镜像仓库时，界面会切换到对应的输入区。
- **CUDA UCM 变体回归**：预检补齐链路覆盖 `uc-manager-cuda-cu129==0.9.0rc3` 实测样例。
- **测试**：新增镜像引用校验、架构转换、服务器拉取命令、GUI 来源切换与 CUDA/A5 预检增强用例。

## 主要功能

- **步骤1 服务器管理**：SSH 凭据本地加密保存/复用；自动探测设备型号、卡数与 CPU 架构。
- **步骤2 镜像准备**：支持服务器已有镜像、本地 tar 上传、镜像仓库拉取；自动探测基础镜像运行时并校验 UCM/wrapt/ucm-toolkit wheel 兼容性。
- **步骤3 容器准备**：选择 UCM 镜像自动校验安装情况；按宿主机实际存在的设备节点与系统挂载生成可编辑 `docker run` 命令；固定 Bash 入口，不启动推理服务；支持 KV Cache、模型与自定义挂载。
- **步骤4 环境预检**：显式选择容器；检查驱动、内核、共享内存、AIO 与存储带宽；UCM 与驱动版本支持多来源补齐；支持 JSON 参数叠加配置。
- **工具菜单**：按关键字筛选并清理容器/镜像，删除前展示完整确认列表。

## 使用

双击 `UCM-Deployer.exe` 即可（无需 Python）。产物自检：

```powershell
.\UCM-Deployer.exe --smoke
```

源码运行 / 开发见 [README](https://github.com/student-jhz/UCM-Deployer#readme)。
