# UCM-Deployer Release 说明模板（{VERSION} 会被替换为实际版本号）

UCM Deployer {VERSION} —— UCM 一键部署工具（Windows 桌面版）

## 下载

- **UCM-Deployer.exe**：Windows x64 单文件绿色程序，无需安装 Python，双击即用

## 功能概览

五步向导：**服务器管理 → 镜像构建 → 容器创建 → 部署配置 → 拉起服务**

- **服务器管理**：SSH 登录信息本地加密保存/复用；`npu-smi` / `nvidia-smi` 设备探测与多机型号一致性校验
- **镜像构建与分发**：选一台构建服务器，基础镜像选择或上传 tar 包 `docker load`；在线/离线（wrapt whl）安装 UCM whl，可选 ucm-toolkit 源码安装，一键构建带 UCM 的 vllm-ascend/vllm/sglang 镜像；**构建一次自动分发到其他服务器**（每台独立进度/日志，结束汇总成功/失败及原因）；已有 UCM 镜像可校验后直接跳过
- **容器创建**：自动识别卡数生成 Ascend（davinci 全量设备 + 驱动挂载）/ NVIDIA（--gpus all）的 `docker run` 命令；kvcache 挂载目录共享文件系统校验（NFS/3FS 等）；模型只读映射；命令可编辑后执行
- **部署配置**：PD 混部 / PD 分离拓扑（DP×TP 卡数校验、组内 TP 一致性）；卡资源占用检查；自动生成含 UCM `--kv-transfer-config` 的 vLLM/SGLang 启动脚本（mooncake master/json、ray 集群、负载均衡、UCM 配置模板），全部可编辑
- **拉起服务**：脚本部署到容器、按依赖顺序拉起、`curl /health` 健康检查、容器日志跟踪、一键停止

## 使用

1. 双击 `UCM-Deployer.exe`
2. 按向导连接真实服务器；或先用内置模拟服务器体验完整流程（源码环境：`python -m ucm_deployer.mock.mock_server`）
3. 完整文档：程序内「帮助 → 用户手册」，或仓库 `docs/用户手册.md`

## 自检

```powershell
.\UCM-Deployer.exe --selftest --out report.txt   # 内置模拟服务器跑 15 步全流程，退出码 0 表示通过
.\UCM-Deployer.exe --smoke                       # GUI 五页面无头冒烟
```

## 质量

- 自动化测试全部通过（单元 / 端到端 / GUI / 打包产物自检四层，见 CI 提交记录）
- 生成的启动脚本与 UCM 官方文档模板一一对应（Quickstart vLLM-Ascend / Distributed PD / Quickstart SGLang）
