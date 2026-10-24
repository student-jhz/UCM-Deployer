# UCM Deployer v{VERSION}

**UCM (Unified Cache Management) 安装与环境预检工具 —— Windows 桌面版**

面向 vLLM / vLLM-Ascend / SGLang 运行环境（Ascend NPU / NVIDIA GPU），提供四步向导：

```
① 服务器管理 → ② 镜像准备 → ③ 容器准备 → ④ 环境预检
```

## 本版本更新

- **镜像仓库拉取**：输入 `quay.io/ascend/vllm-ascend:v0.30.0rc2-a5-openeuler` 这类镜像地址，本地 Docker 按目标服务器 CPU 架构执行 `docker pull --platform`，导出 `tar.gz` 后自动上传服务器并 `docker load`。
- **复用已有 UCM 镜像**：基础镜像已安装 UCM 时，可直接记录并进入容器准备，无需重复构建；安装包区域明确标记为“仅在需要构建新 UCM 镜像时填写”。
- **复用已有 UCM 容器**：已有容器无需重建，可在步骤 4 刷新容器列表并选择该容器直接预检。
- **显式目标容器**：步骤 4 新增目标容器下拉框与刷新按钮，预检命令始终在用户显式选择的容器内执行。
- **产品定位修正**：工具定位为 UCM 安装与环境预检，不负责启动或部署推理服务。
- **文档**：在线文档更新新流程与新截图；「下载 EXE」改为直接下载最新版附件。

## 主要功能

- **步骤1 服务器管理**：SSH 凭据本地加密保存/复用；自动探测设备型号、卡数与 CPU 架构。
- **步骤2 镜像准备**：支持服务器已有镜像、本地 tar 上传、镜像仓库拉取；按需离线构建 UCM 镜像并校验平台/架构匹配。
- **步骤3 容器准备**：选择 UCM 镜像自动校验安装情况；按设备卡数生成可编辑 `docker run` 命令；支持 KV Cache、模型与自定义挂载。
- **步骤4 环境预检**：显式选择容器；检查驱动、内核、共享内存、AIO 与存储带宽；支持 JSON 参数叠加配置。
- **工具菜单**：按关键字筛选并清理容器/镜像，删除前展示完整确认列表。

## 使用

双击 `UCM-Deployer.exe` 即可（无需 Python）。产物自检：

```powershell
.\UCM-Deployer.exe --smoke
```

源码运行 / 开发见 [README](https://github.com/student-jhz/UCM-Deployer#readme)。
