# UCM Deployer v{VERSION}

**UCM (Unified Cache Management) 安装与环境预检工具 —— Windows 桌面版**

面向 vLLM / vLLM-Ascend / SGLang 运行环境（Ascend NPU / NVIDIA GPU），提供四步向导：

```
① 服务器管理 → ② 镜像准备 → ③ 容器准备 → ④ 环境预检
```

## 本版本更新

- **设备型号识别**：修复新版 `npu-smi` 表格解析，正确显示 `Ascend950PR`，不再误报“型号未知”。
- **动态设备节点映射**：创建容器前逐个探测 `/dev/davinci*`、`davinci_manager`、`hisi_hdc`、`devmm_svm`、`ummu`、`uburma`，仅映射实际存在的节点；950PR/A5 没有 `/dev/devmm_svm` 时不会再生成该参数。
- **A5 系统资源适配**：自动筛选实际存在的 Ascend 驱动、工具与配置挂载；检测到 `/dev/ummu` 或 `/dev/uburma` 时自动添加 `--privileged=true`。
- **文档与截图**：在线手册、首页与截图更新 950PR/A5 场景，并保留中文可读截图。
- **产品定位**：工具定位为 UCM 安装与环境预检，不负责启动或部署推理服务。

## 主要功能

- **步骤1 服务器管理**：SSH 凭据本地加密保存/复用；自动探测设备型号、卡数与 CPU 架构。
- **步骤2 镜像准备**：支持服务器已有镜像、本地 tar 上传、镜像仓库拉取；按需离线构建 UCM 镜像并校验平台/架构匹配。
- **步骤3 容器准备**：选择 UCM 镜像自动校验安装情况；按宿主机实际存在的设备节点与系统挂载生成可编辑 `docker run` 命令；支持 KV Cache、模型与自定义挂载。
- **步骤4 环境预检**：显式选择容器；检查驱动、内核、共享内存、AIO 与存储带宽；支持 JSON 参数叠加配置。
- **工具菜单**：按关键字筛选并清理容器/镜像，删除前展示完整确认列表。

## 使用

双击 `UCM-Deployer.exe` 即可（无需 Python）。产物自检：

```powershell
.\UCM-Deployer.exe --smoke
```

源码运行 / 开发见 [README](https://github.com/student-jhz/UCM-Deployer#readme)。
