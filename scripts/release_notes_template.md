# UCM Deployer v{VERSION}

**UCM (Unified Cache Management) 一键部署工具 —— Windows 桌面版**

面向 vLLM / vLLM-Ascend / SGLang 推理容器（Ascend NPU / NVIDIA GPU），单服务器三步向导：

```
① 服务器管理 → ② 镜像构建 → ③ 容器创建
```

## 功能

- **步骤1 服务器管理**：SSH 登录信息本地加密保存/复用；单选登录；`npu-smi`/`nvidia-smi` 设备探测（型号/卡数/CPU 架构）
- **步骤2 镜像构建**：选服务器已有镜像（关键字筛选）或上传 tar `docker load`；上传 wrapt whl、UCM whl、（可选）ucm-toolkit whl 包（均附在线下载链接，toolkit 与 UCM 同站点）；构建前自动校验服务器/镜像/安装包匹配（设备平台 + whl 架构标记）；生成 Dockerfile 并构建、校验 UCM 安装
- **步骤3 容器创建**：选 UCM 镜像（选择时自动校验镜像内 UCM）；自动识别卡数全量映射（NPU: 全部 davinci 设备 + 驱动挂载 + `--net=host`；GPU: `--runtime nvidia --gpus all -p 8000:8000`）；KV Cache 目录、模型只读挂载、自定义挂载；docker run 命令可编辑后执行
- 所有生成的命令**先展示、可编辑、再执行**

## 使用

双击 `UCM-Deployer.exe` 即可（无需 Python）。产物自检：

```powershell
.\UCM-Deployer.exe --smoke
```

源码运行 / 开发见 [README](https://github.com/student-jhz/UCM-Deployer#readme)。
