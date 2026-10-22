# 步骤3 · 容器创建

基于 UCM 镜像创建推理容器：自动全量映射加速卡，配置挂载，生成的 docker run 命令**可编辑后执行**。

![步骤3 界面](../screenshots/step3-container.png)

## 操作流程

### 1. 选择 UCM 镜像

进入本页自动加载镜像列表；**选择镜像时自动后台校验镜像内 UCM 安装情况**：

- ✓ 已安装：显示版本，自动记录为本服务器使用的镜像；
- ✗ 未安装：提示回步骤2 构建或换镜像（创建容器前会再次拦截确认）。

### 2. 容器参数

| 参数 | 默认 | 说明 |
|---|---|---|
| 容器名 | `ucm-vllm` | — |
| shm-size | `512g` | UCM KV-cache 页缓存要求 ≥512 GiB |
| 网络 | `host` | Ascend 容器使用；NVIDIA 容器固定用 `-p 8000:8000` 端口映射 |

### 3. 挂载配置

- **KV Cache 目录**：可添加多个（「浏览」弹出**容器内/宿主机目录浏览器**）；挂载到容器内相同路径；「🔍 文件系统校验」检查目录所在文件系统（本地/网络存储）；
- **模型路径**：宿主机路径 → 容器内路径（默认 `/models`），默认只读；
- **自定义挂载**：任意宿主机/容器路径映射，可勾选只读。

![目录浏览](../screenshots/dialog-remote-dirs.png)

### 4. 生成 / 执行命令

点「🔁 生成/刷新命令」自动生成 docker run——按探测卡数**全量映射加速卡**：

=== "NPU（vllm-ascend）"

    ```bash
    docker run -itd \
        --name ucm-vllm \
        --net=host --shm-size 512g \
        --device /dev/davinci0 ... --device /dev/davinci7 \
        --device /dev/davinci_manager --device /dev/devmm_svm --device /dev/hisi_hdc \
        -v /usr/local/dcmi:/usr/local/dcmi \
        -v /usr/local/bin/npu-smi:/usr/local/bin/npu-smi \
        -v /usr/local/Ascend/driver/lib64/:/usr/local/Ascend/driver/lib64/ \
        ...（完整驱动挂载） \
        -v /mnt/kvcache:/mnt/kvcache \
        -v /data/models/Qwen3-32B:/models:ro \
        vllm-ascend-ucm:v0.26.0rc1 bash
    ```

=== "GPU（vllm）"

    ```bash
    docker run -itd \
        --name ucm-vllm \
        --runtime nvidia --gpus all \
        -p 8000:8000 --ipc=host --shm-size 512g \
        -v /mnt/kvcache:/mnt/kvcache \
        -v /data/models:/models:ro \
        vllm-openai-ucm:latest bash
    ```

命令**可编辑**，点「🚀 创建容器」按编辑框内容执行；完成后可点「✅ 检查容器内 UCM」确认。

!!! note "下一步"
    容器创建成功后，进入[步骤4 · 环境预检](step4.md)在容器内做部署前检查。
