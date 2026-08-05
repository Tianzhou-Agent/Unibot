# 容器化部署

根目录的 Docker Compose 会启动完整的 Unibot Web 平台：

- `frontend`：React 静态页面与 `/api` 反向代理；
- `backend`：FastAPI、AINA 调度器与开发模式代码运行器；
- `mysql`：业务数据；
- `redis`：分布式锁、缓存和运行状态；
- `vision`：YOLO26m 图片目标检测。

MySQL、Redis、NAS 文件和代码运行器工作区都使用 Docker 持久卷。重新创建容器不会删除这些数据。

## 启动

先复制配置模板并修改所有 `CHANGE_ME`：

```powershell
Copy-Item .env.docker.example .env
docker compose up -d --build
```

启动完成后访问：

- Web：<http://127.0.0.1:8080>
- 后端文档：<http://127.0.0.1:8000/docs>

检查状态和查看日志：

```powershell
docker compose ps
docker compose logs -f backend frontend
```

停止服务但保留数据：

```powershell
docker compose down
```

`docker compose down -v` 会永久删除数据库、NAS 和用户代码工作区，仅应在明确需要清空全部数据时使用。

## GPU 图片识别

默认编排使用 CPU 镜像，可在所有 Docker 环境运行。安装 NVIDIA Container Toolkit 且 Docker 可访问 GPU 后，使用 GPU 覆盖文件：

```powershell
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

YOLO 服务仍使用 `YOLO_DEVICE=auto`，能访问 CUDA 时选择 GPU，否则选择 CPU。

## 生产配置

生产环境继续使用同一组镜像和环境变量，并至少完成以下设置：

1. 更换 MySQL 密码和 `UNIBOT_AUTH_SECRET`；
2. 使用 HTTPS，并设置 `UNIBOT_AUTH_COOKIE_SECURE=true`；
3. 将 `UNIBOT_FRONTEND_BASE_URL` 和 GitHub OAuth 回调改为正式域名；
4. 不向公网开放后端的 `8000` 端口，只保留前端/网关入口；
5. 多后端节点使用同一个 MySQL、Redis、NAS 和 `UNIBOT_AUTH_SECRET`。

Compose 中的 `local` 沙箱驱动适合可信开发环境，它在后端容器中执行脚本并按用户隔离持久目录。运行不可信脚本时，使用现有 K3s + gVisor 方案并设置 `UNIBOT_SANDBOX_DRIVER=kubernetes`，详见 [用户沙箱平台](sandbox-platform.md)。不要把 Docker Socket 挂载到 Web 后端。
