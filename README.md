# UCM Deployer

UCM (Unified Cache Management) 一键部署工具（Windows 桌面版）。

面向 vLLM / vLLM-Ascend / SGLang 推理集群，覆盖：

1. **服务器管理** —— SSH 登录信息本地加密保存、复用，多服务器设备型号一致性校验
2. **镜像构建** —— 基于服务器上的基础镜像（或上传 tar 包 docker load），安装 UCM whl（含离线 wrapt 依赖），打成带 UCM 的引擎镜像
3. **容器创建** —— 自动识别 Ascend/NVIDIA 设备数量，生成可编辑的 `docker run` 命令（kvcache 挂载、模型只读映射、共享文件系统校验）
4. **服务部署** —— PD 混部 / PD 分离拓扑规划与卡资源校验，自动生成含 UCM `--kv-transfer-config` 的 vllm/sglang 启动脚本
5. **拉起服务** —— ray 集群 / mooncake master / 负载均衡 / 健康检查，实时日志

详细文档见 `docs/`。开发与打包说明见 `README.md`（持续完善中）。

## 快速开始（开发模式）

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest          # 运行全部自检测试
.venv\Scripts\python main.py            # 启动 GUI
```

## 模拟服务器（无真实环境演示/自验证）

```powershell
.venv\Scripts\python -m ucm_deployer.mock.mock_server --port 2222 --device ascend --cards 8
# 然后在 GUI 中添加服务器 127.0.0.1:2222 (root/root) 即可走完整流程
```
