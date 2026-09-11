# 容器化部署

飞牛 fnOS 使用预构建镜像的专用方案见 [飞牛部署说明](../deploy/fnos/README.md)。

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

Compose 读取 `UNIBOT_SANDBOX_DRIVER`，默认 `local`，但默认拒绝本地代码执行。只有可信开发环境才可显式设置 `UNIBOT_SANDBOX_ALLOW_UNSAFE_LOCAL=true`：该驱动在后端容器的操作系统身份下执行脚本，用户目录划分不是安全隔离。运行不可信脚本时，使用现有 K3s + gVisor 方案并设置 `UNIBOT_SANDBOX_DRIVER=kubernetes`，详见 [用户沙箱平台](sandbox-platform.md)。选择 Kubernetes 还需在 Compose override 中传入对应 `UNIBOT_SANDBOX_KUBERNETES_*` 环境变量、挂载服务账户凭据/CA，并确认 API 连通性及工作区 PVC；只改驱动名称不会配置集群。不要把 Docker Socket 挂载到 Web 后端。

## 管理员与远程能力

`UNIBOT_ADMIN_IDENTITIES` 现在只接受不可变的用户 ID，不再接受邮箱或 GitHub 登录名。升级时先使用目标账户登录，从 `/auth/me` 读取 `user.id`，由部署管理员确认该账户后写入逗号分隔的配置并重启后端。注册同名邮箱不会获得管理员权限。

共享工具、技能及远程 AINA 的注册/删除要求平台管理员权限；普通用户仍可发现公开能力并管理自己的 AINA 安装。工具/技能的 `owner_user_id` 和 `tenant_id` 由服务器绑定。私有能力只对其所有者及管理员可见，租户能力还允许同租户用户；升级前没有归属信息的非公开记录对普通用户不可见，需由管理员重新登记。执行与发现使用同一权限规则。

设置 `UNIBOT_CAPABILITY_ALLOWED_ORIGINS` 后才能访问远程 Tools/AINA，例如 `https://connector.example.com,http://trusted-service:8080`。每项必须是完整的 HTTP(S) 来源（协议、主机、端口），不可包含凭据、查询或路径；默认空值拒绝全部目标，且没有通配符。注册时检查端点和健康检查，实际请求（包括重定向和 A2A Agent Card 发现的接口）再次检查目标。内部服务可以显式列入，但只应批准受控服务。此规则不做 DNS 固定或 IP 地址隔离：管理员还需保护获准域名的 DNS、限制出口网络，并避免批准能转发任意 URL 的代理服务。模型提供商、GitHub OAuth 等平台配置地址不属于此远程能力列表。
