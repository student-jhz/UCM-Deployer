# 常见问题

## 密码安全吗？

密码使用 Fernet 对称加密保存在本地（`~/.ucm_deployer/servers.json` 密文 + `.secret.key` 密钥）。密钥与密文同目录，防止 servers.json 被单独拷贝后直接泄露明文；属内网运维工具的折中方案。

## 镜像构建失败：pip install 报 wrapt 找不到？

请确认选择的 wrapt whl 与服务器架构匹配（manylinux x86_64 / aarch64）；输入框下方的即时反馈与「校验匹配」都会提示架构标记。

## 为什么仓库拉取不需要本地 Docker Desktop？

v0.3.5 起镜像仓库模式改为**服务器直连拉取**：工具通过 SSH 在目标服务器上执行 `docker pull`，并按已探测架构追加 `--platform`。本地不下载、不导出、不上传镜像包，因此无需安装 Docker Desktop；目标服务器需已安装 Docker 且能访问对应仓库。

## 为什么创建容器后没有启动 vLLM？

这是设计行为。UCM Deployer 只负责创建可预检的 UCM 容器，不部署或启动推理服务。生成的命令固定使用 `--entrypoint /bin/bash` 覆盖镜像默认 ENTRYPOINT，且不在镜像后追加 `bash`；因此不会把 `bash` 误当成 vLLM 模型参数，也不会自动加载模型。

## 构建成功但提示「未检测到 UCM 安装」？

UCM 包名存在**平台变体**（如 `uc-manager-cann910-a2`）。本工具按 `uc-manager*` 前缀宽泛匹配检测（`pip list | grep`），已覆盖各变体；若仍报错请确认 UCM whl 是否正确安装（可手动验证：`docker run --rm <镜像> pip list | grep uc`）。

## 「匹配校验不通过：镜像疑似 ascend/nvidia 平台...」？

按镜像名推断的平台与服务器设备不一致。请更换与服务器设备匹配的基础镜像；若镜像名不含平台关键字（如自定义镜像名），会降级为警告而非错误。

## Ascend 950PR 创建容器时报 `/dev/devmm_svm` 不存在？

这是 A5/950PR 驱动节点差异，不是工具探测失败。v0.3.3 起步骤3 会逐个探测 `/dev/davinci*`、`davinci_manager`、`hisi_hdc`、`devmm_svm`、`ummu`、`uburma`，只映射实际存在的节点；探测到 `ummu` 或 `uburma` 时自动添加 `--privileged=true`。请更新到 v0.3.3 或更新版本，无需手工删除该参数。

## 预检显示 `uc_manager -` 或 `HDK=?`？

v0.3.4 起，Deployer 会在 toolkit 识别失败时独立补齐事实：UCM 从 pip freeze/表格输出识别 `uc-manager-cann910-a5`、`uc-manager-cuda-cu129` 等平台变体；Ascend 驱动依次读取 `/usr/local/Ascend/driver/version.info` 与 `npu-smi info`，例如 `Version=25.6.rc1.b999` 会显示为 `HDK=25.6.rc1.b999` 并按 `> 25.2.0` 判定。详情区会标注 `[deployer] fact source`，便于核对来源。

## 容器内 npu-smi 报 rc=172 / `device is used (-8020)`？

NPU 正被其他进程/容器占用（宿主机 `npu-smi info` 可正常查看）。该 WARN 不影响推理与带宽检查，停用占用方后在容器内重跑 `npu-smi info` 即可恢复。

## 带宽测试某组合报 `combo timed out after 120s`？

单组合超时（默认 120s，常见于 aio 引擎 + 高并发 + 慢存储）。可通过 [预检参数配置](precheck-config.md) 调整 `combo_timeout`、裁剪 `engines`/`worker_counts`，或提高 `fs.aio-max-nr`。

## 带宽结果低于 8 GB/s 阈值但存储实际更快？

默认非 Direct I/O 读取可能被 OS 页缓存满足，结果应解释为**该测试路径的吞吐**。可通过预检参数配置确认 I/O 模式与负载（`shard_sizes`/`worker_counts`/`engines`）。

## 步骤4 提示「镜像未安装 UCM」？

选择镜像时会自动校验；若未安装请回步骤2 构建，或选择其他已装 UCM 的镜像。

## 日志在哪里？

`~/.ucm_deployer/logs/ucm_deployer.log`（菜单「帮助 → 打开日志目录」）。

## 如何在无真实服务器时验证程序？

```powershell
.venv\Scripts\python -m pytest    # 159 项自动化测试，无需真实服务器
```
