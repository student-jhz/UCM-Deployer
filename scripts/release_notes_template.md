# UCM Deployer v{VERSION}

**UCM (Unified Cache Management) 安装与环境预检工具 —— Windows 桌面版**

面向 vLLM / vLLM-Ascend / SGLang 运行环境（Ascend NPU / NVIDIA GPU），提供四步向导：

```
① 服务器管理 → ② 镜像准备 → ③ 容器准备 → ④ 环境预检
```

## 本版本更新

- **UCM 平台变体识别**：预检输出 `uc_manager -` 时，自动从 pip freeze/表格输出识别 `uc-manager-cann910-a5` 等包名，显示实际版本。
- **Ascend 驱动版本补齐**：`HDK=?` 时自动读取 `/usr/local/Ascend/driver/version.info`，例如 `Version=25.6.rc1.b999` 会显示为 `HDK=25.6.rc1.b999`，并按 `> 25.2.0` 判定。
- **通用多来源探测**：驱动版本依次支持 `version.info`、`npu-smi info`、`nvidia-smi`；探测来源写入详情区 `[deployer] fact source`，并保留 toolkit 原始输出。
- **LD_PRELOAD 噪声隔离**：事实探测忽略缺失 `libjemalloc.so.2` 产生的 loader 警告，只解析有效 stdout。
- **测试**：新增 A5 实测样例覆盖，包含完整预检增强链路。

## 主要功能

- **步骤1 服务器管理**：SSH 凭据本地加密保存/复用；自动探测设备型号、卡数与 CPU 架构。
- **步骤2 镜像准备**：支持服务器已有镜像、本地 tar 上传、镜像仓库拉取；按需离线构建 UCM 镜像并校验平台/架构匹配。
- **步骤3 容器准备**：选择 UCM 镜像自动校验安装情况；按宿主机实际存在的设备节点与系统挂载生成可编辑 `docker run` 命令；支持 KV Cache、模型与自定义挂载。
- **步骤4 环境预检**：显式选择容器；检查驱动、内核、共享内存、AIO 与存储带宽；UCM 与驱动版本支持多来源补齐；支持 JSON 参数叠加配置。
- **工具菜单**：按关键字筛选并清理容器/镜像，删除前展示完整确认列表。

## 使用

双击 `UCM-Deployer.exe` 即可（无需 Python）。产物自检：

```powershell
.\UCM-Deployer.exe --smoke
```

源码运行 / 开发见 [README](https://github.com/student-jhz/UCM-Deployer#readme)。
