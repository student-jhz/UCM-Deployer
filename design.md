设计一个ucm安装程序，windows版。

以下是ucm的项目仓：https://github.com/ModelEngine-Group/unified-cache-management/

ucm是一个whl包，需要安装在vllm-ascend/vllm/sglang引擎的镜像容器中。
pip install ucm的whl包过程需要联网下载对应的依赖包：wrapt, 如果服务器每联网，只能手动上传wrapt包，进行pip install的安装。

ucm也有一个ucm-toolkit工具，当前只能通过源码来安装。

我需要这个程序的流程是：
步骤1：选择要部署推理服务的服务器，并校验设备型号是否一致。
先登录可能要部署推理服务的服务器，可能是单个或是多个服务器。这些登录信息要求能保存到一个目录，用于后续能在程序窗口上直接选择这些已有的服务器。
然后选择本次实际要部署的服务器。

步骤2：创建镜像（默认在上步骤选择本次实际部署的所有服务器上执行，并可单独执行，要显示每个服务器的进度和日志）
[ 可选 ] 若已有安装了ucm的镜像，则直接跳过该步骤到下一个步骤，选择容器

在服务器上选择要使用的vllm-ascend或vllm或sglang的基础镜像。如果服务器上没有可用的基础镜像，可提供上传镜像tar/tar.gz包的方式，然后再服务器上docker load，然后再选择。
然后本地选择要安装的ucm包，wrapt包（ucm的依赖包，服务器无法联网时需要从本地上传）
之后根据以上选择在基础镜像的基础上安装ucm的依赖包wrapt（服务器联网时不需要单独安装）, 和 ucm，并打成一个带ucm的vllm-ascend或vllm或sglang的镜像，这个镜像的命提供一个默认名，可以由用户更改。


步骤3：创建容器（默认在上步骤选择本次实际部署的所有服务器上执行，并可单独执行，要显示每个服务器的进度和日志）
[ 可选 ] 若已有安装了ucm的容器，则直接跳过该步骤到下一个步骤，进入容器部署推理服务
选择安装了ucm的镜像，要进行检查ucm的版本和是否已安装ucm，否则回到上一步。

部署docker
指定要创建的容器名
选择服务器上ucm要使用的持久化推理过程中kvcache的挂载目录，可以选多个挂载目录，但多个挂载目录要求是同一个共享文件系统。（要提示用户，同一种模型、同一种部署模式：P节点数量，DP TP数量一致，才能共用一个文件系统/或同一套挂载目录）
选择要映射到的模型路径 （只读模式）
选择要映射到容器的其他路径
基于上一步带ucm的镜像，运行docker run -itd命令创建一个已内置了ucm的容器，命令中要将区分是nvidia设备和ascend的设备进行参数配置。
要注意这个创建docker的命令中的 -v参数要映射模型路径，上面的挂载目录。可由用户增加映射目录，且宿主机上的目录可以以下拉框形式。
并且提供用户全量的命令编辑窗口。

以下是ascend上的起容器示例, 由于ascend的A2 A3服务器卡数一个是8 一个16，所以--device建议是自动识别卡数，然后映射全部。

'''bash
export IMAGE=quay.io/ascend/vllm-ascend:v0.23.0-a3
export NAME=vllm-ascend

# Run the container using the defined variables
# Note: If you are running bridge network with docker, please expose available ports for multiple nodes communication in advance
docker run --itd \
--name $NAME \
--net=host \
--shm-size=512g \
--device /dev/davinci0 \
--device /dev/davinci1 \
--device /dev/davinci2 \
--device /dev/davinci3 \
--device /dev/davinci4 \
--device /dev/davinci5 \
--device /dev/davinci6 \
--device /dev/davinci7 \
--device /dev/davinci8 \
--device /dev/davinci9 \
--device /dev/davinci10 \
--device /dev/davinci11 \
--device /dev/davinci12 \
--device /dev/davinci13 \
--device /dev/davinci14 \
--device /dev/davinci15 \
--device /dev/davinci_manager \
--device /dev/devmm_svm \
--device /dev/hisi_hdc \
-v /usr/local/dcmi:/usr/local/dcmi \
-v /usr/local/Ascend/driver/tools/hccn_tool:/usr/local/Ascend/driver/tools/hccn_tool \
-v /usr/local/bin/npu-smi:/usr/local/bin/npu-smi \
-v /usr/local/Ascend/driver/lib64/:/usr/local/Ascend/driver/lib64/ \
-v /usr/local/Ascend/driver/version.info:/usr/local/Ascend/driver/version.info \
-v /etc/ascend_install.info:/etc/ascend_install.info \
-v /root/.cache:/root/.cache \
-it $IMAGE bash
'''
以下是vllm原生的NV生态的用户指南：https://docs.vllm.ai/en/latest/deployment/docker/

步骤4：部署推理服务
选择已安装了ucm的容器，要进行检查容器内是否已安装。
选择服务部署形态，比如PD混部模式就是所有服务器不进行区分，比如PD分离模式：多少P节点，多少D节点，每个P节点的DP和TP数，每个D节点的DP和TP数。以及指定P D节点的服务器。
要求符合总使用卡数数不超过可用卡数。
检查所有服务器上的卡资源是否被占用，是否自己现在可用，如果被占用需要进行提示。
配置vllm/sglang的启动命令参数：
服务名称 --serve-model-name
按 P D 节点，配置P D节点的相关dp tp参数。
ucm的配置：
单机或多级混部模式下：
--kv-transfer-config \
'{
    "kv_connector": "UCMConnector",
    "kv_connector_module_path": "ucm.integration.vllm.ucm_connector",
    "kv_role": "kv_both",
    "kv_connector_extra_config": {"UCM_CONFIG_FILE": "/workspace/unified-cache-management/examples/ucm_config_example.yaml"}
}'
PD分离模式下：
    --kv-transfer-config \
    '{
        "kv_connector": "MultiConnector",
        "kv_role": "kv_producer",
        "kv_connector_extra_config": {
            "connectors": [
                {
                    "kv_connector": "MooncakeConnectorV1",
                    "kv_role": "kv_producer",
                    "kv_port": '$mooncake_port',
                    "kv_connector_extra_config": {
                        "prefill": {
                            "dp_size": '$dp_size',
                            "tp_size": '$tp_size'
                        },
                        "decode": {
                            "dp_size": '$dp_size',
                            "tp_size": '$tp_size'
                        }
                    }
                },
                {
                    "kv_connector": "UCMConnector",
                    "kv_role": "kv_both",
                    "kv_connector_module_path": "ucm.integration.vllm.ucm_connector",
                    "kv_connector_extra_config": {"UCM_CONFIG_FILE": "/vllm-workspace/unified-cache-management/examples/ucm_config_example.yaml"}
                }
            ]
        }
    }'
decode节点：
    --kv-transfer-config \
    '{
        "kv_connector": "MooncakeConnectorV1",
        "kv_role": "kv_consumer",
        "kv_port": '$mooncake_port',
        "kv_connector_extra_config": {
            "prefill": {
                "dp_size": '$dp_size',
                "tp_size": '$tp_size'
            },
            "decode": {
                "dp_size": '$dp_size',
                "tp_size": '$tp_size'
            }
        }
    }'
以上由程序自动生成。
其他参数由用户来编辑增加。
这些脚本生成后用户可见可编辑。

步骤5：
拉起服务。
拉起方式参考：
https://ucm.readthedocs.io/en/latest/getting-started/quickstart_vllm_ascend.html
https://docs.vllm.ai/projects/ascend/en/latest/tutorials/models/DeepSeek-V4.1-Flash.html#51-multi-node-colocated-deployment
https://ucm.readthedocs.io/en/latest/user-guide/pd-disaggregation/distributed_pd.html
https://ucm.readthedocs.io/en/latest/user-guide/pd-disaggregation/large_scale_ep.html

请设计一版这样的程序。
要求你进行设计和每个模块的自验证。我没有实际服务器给你使用。建议使用python代码实现，并且打包成exe可执行文件。
整个实现不要一个提交完成，最好分成几个阶段进行完成，提交相应的代码，用户文档内容。最终完成整体任务。

