# 图片识别 AINA

`unibot.image-recognition` 是 `examples/image-recognition-aina/` 中的可选 AINA 2.0 示例。用户可以在 Canvas 中粘贴、
拖入或选择 JPEG、PNG、WebP 图片，Canvas 会自动调用 YOLO26m，并显示目标框、中英文类别、置信度、目标数量、
推理耗时和实际运行设备。Superbot 默认不注册或使用它；用户必须手动添加包并安装后才能使用，卸载后即失去访问权限。安装步骤见 [示例说明](../examples/image-recognition-aina/README.md)。

## 架构

```text
Browser
  └─ Canvas（沙箱 iframe，aina-ui 桥接）
       └─ callTool("detect", {image_base64, content_type, filename})
            └─ Superbot FastAPI  POST /ainas/unibot.image-recognition/app-tools/detect
                 └─ 独立 MCP 服务器  examples/image-recognition-aina/server/main.py
                      └─ POST /v1/detect (multipart)
                           └─ unibot/vision-service
                                └─ Ultralytics YOLO26m
                                     ├─ CUDA GPU（可用时优先）
                                     └─ CPU（自动回退）
```

Canvas 运行在不能访问网络的沙箱 iframe 中，只能通过宿主调用插件的 `detect` 和 `vision_status` 工具；两者都是
只读工具，仅供 Canvas 使用，不暴露给模型。MCP 服务器限制图片类型和大小，推理服务还会校验图片格式和最大像素数。
图片仅保存在请求内存中用于本次推理，不写入数据库、NAS 或对象存储。

| 位置 | 内容 |
| --- | --- |
| `examples/image-recognition-aina/package/` | `aina.json` 与构建好的 Canvas |
| `examples/image-recognition-aina/server/main.py` | MCP 服务器（只依赖 MCP SDK，不导入平台代码） |
| `examples/image-recognition-aina/canvas/` | Canvas 的 React 源码与中英文文案 |

修改 Canvas 源码后，在 `frontend/` 执行 `npm run build:canvases` 重新构建，并提交 `package/canvas/` 下的产物。

## 本地启动

要求 Docker Desktop 已启动。在仓库根目录执行：

```powershell
.\scripts\start-vision.cmd
```

脚本先通过轻量容器探测 Docker GPU，再构建 `unibot/vision-service:local` 并启动：

- Docker 能访问 NVIDIA GPU：组合加载 `backend/docker-compose.vision.gpu.yml`，使用 CUDA 基础镜像；
- GPU 不可用：仅加载 `backend/docker-compose.vision.yml`，使用更小的 CPU 基础镜像。

两种镜像由同一 Dockerfile 构建，使用完全相同的服务代码、API 和配置。镜像内部仍会通过
`YOLO_DEVICE=auto` 复核运行设备；显式请求 CUDA 但运行时不可用时会安全回退到 CPU。

检查状态：

```powershell
docker compose -f backend/docker-compose.vision.yml ps
Invoke-RestMethod http://127.0.0.1:18081/healthz
```

插件的 MCP 服务器默认通过 `http://127.0.0.1:18081` 访问服务，并从独立服务的环境变量读取以下设置：

```dotenv
UNIBOT_VISION_BASE_URL=http://127.0.0.1:18081
UNIBOT_VISION_TIMEOUT_SECONDS=60
UNIBOT_VISION_MAX_IMAGE_BYTES=10485760
```

容器侧设置：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `YOLO_DEVICE` | `auto` | `auto`、`cpu`、`cuda` 或 `cuda:<index>` |
| `YOLO_IMAGE_SIZE` | `640` | 推理输入尺寸 |
| `YOLO_MAX_CONCURRENCY` | `1` | 单实例并发推理数 |
| `YOLO_MAX_IMAGE_BYTES` | `10485760` | 请求体图片上限 |
| `YOLO_MAX_IMAGE_PIXELS` | `40000000` | 解码后图片像素上限 |

## 生产部署

本地和生产共用同一 Dockerfile、同一服务协议和同一环境变量，无需迁移代码。构建并推送镜像后：

```bash
docker build vision-service \
  --build-arg ULTRALYTICS_IMAGE=ultralytics/ultralytics:8.4.105 \
  -t <registry>/unibot/vision-service:<immutable-tag>
docker push <registry>/unibot/vision-service:<immutable-tag>

helm upgrade --install unibot-vision deploy/helm/unibot-vision \
  -f deploy/helm/unibot-vision/values-production.yaml \
  --set image.repository=<registry>/unibot/vision-service \
  --set image.tag=<immutable-tag>
```

GPU 集群需要 NVIDIA device plugin；`gpu.enabled=true` 会请求 `nvidia.com/gpu`。CPU 集群应把
构建参数改为 `ultralytics/ultralytics:8.4.105-cpu` 并使用默认 `values.yaml`。生产环境应把
`UNIBOT_VISION_BASE_URL` 指向集群内 Service，例如 `http://unibot-vision:8080`。

## 开源许可证

项目以及推理服务均按 [AGPL-3.0](../LICENSE) 完全公开。部署者需要公开对应版本的应用源码、容器构建
文件、配置和修改；如果未来不能满足这些公开义务，应在部署前获得覆盖该项目的 Ultralytics
Enterprise 授权。
