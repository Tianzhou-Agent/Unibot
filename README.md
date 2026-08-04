# Unibot

> AI-Native 交互平台 — 以 Chatbot 为入口，用户通过自然语言对话完成各类任务。

## 产品定位

Unibot 由两层能力构成：

1. **Host 基础对话能力**：内置 Agent Loop、LLM 调用、上下文管理和工具路由，即使不安装任何外部 App 也能作为完整 Chatbot 使用。
2. **App 扩展生态**：通过 AINA 协议注册外部 App，动态扩展平台能力边界。

类比：Unibot 像 iPhone —— 开机就有系统级 Siri 能力；安装 App 后才有拍照、导航、支付等扩展能力。

## 核心特性

| 特性                | 说明                                               |
| ------------------- | -------------------------------------------------- |
| **通用对话**        | 多轮对话、Session 隔离、流式响应                   |
| **Canvas 画布**     | 对话 + AINA `main_widget` 双栏工作区               |
| **AINA Widget**     | 对话内应用列表、表单、Markdown 与导航 Widget       |
| **可恢复运行**        | 刷新或离开页面后 Agent 继续运行，返回会话后自动恢复状态 |
| **宿主 Widget**      | AINA 只声明 UI 类型、schema 和用法，由前端注册表动态渲染 |
| **统一能力入口**    | 模型一次判断直答、宿主 Tool、已发布 Tool 或可路由 AINA；选中 AINA 后收敛到其能力域 |
| **Inline Card**     | 对话流内嵌 HTML 卡片（摘要、选项、消歧选择）       |
| **异步任务**        | 后台长任务 + 外部事件推送，待办栏独立上下文        |
| **记忆扩展**        | 跨会话持续认知，异步固化 + 预加载                  |
| **文件附件**        | 文件上传、签名 URL、                               |
| **多 LLM Provider** | 支持多LLM Provider配置，用户级隔离                 |
| **用户与认证**      | 邮箱密码注册登录、HttpOnly 会话、GitHub OAuth、跨节点用户数据隔离 |
| **用户代码沙箱**    | Code Runner AINA、每用户持久工作区、本地/K3s 双驱动与 gVisor 隔离 |
| **图片目标检测**    | 图片识别 AINA、YOLO26m 容器推理、GPU 优先与 CPU 自动回退 |

## 项目结构

```
Unibot/
├── backend/                    # Python 后端 (FastAPI)
│   ├── pyproject.toml          # 项目配置与依赖
│   ├── .python-version         # Python 3.12
│   ├── uv.lock                 # uv 锁文件
│   └── src/
│       └── tianzhou_agent_platform/
│           ├── __init__.py
│           ├── main.py         # 应用入口
│           ├── api/            # API 路由层
│           ├── core/           # 核心业务逻辑
│           ├── aina/           # AINA 协议实现
│           │   ├── tool/       # 工具注册与路由
│           │   ├── memory/     # 记忆扩展
│           │   ├── skill/      # Skill 编排
│           │   ├── builder/    # AINA 构建器
│           │   ├── config/     # 配置管理
│           │   ├── knowledge/  # 知识管理
│           │   ├── ops/        # 运维工具
│           │   └── security/   # 安全与鉴权
│           └── store/          # 数据存储层
│               ├── database/   # PostgreSQL
│               ├── redis/      # Redis 缓存
│               ├── s3/         # 对象存储
│               └── nas/        # 文件存储
├── frontend/                   # React/Vite MVP 前端
├── vision-service/             # YOLO26m 独立推理服务
├── deploy/helm/unibot-vision/  # 生产 Kubernetes Helm Chart
├── scripts/                    # 脚本工具
├── docs/                       # 文档
└── LICENSE                     # AGPL-3.0 License
```

## 快速开始

### 环境要求

- **Python 3.12+**
- **Docker Desktop**（本地 MySQL 与 Redis）
- **uv** 包管理器（推荐）

### 安装依赖

```bash
cd backend
uv sync --extra dev
```

### 启动服务

```bash
docker compose -f docker-compose.storage.yml up -d
uv run tianzhou-agent-platform
```

后端启动时会自动创建 MySQL 业务表，并对 MySQL、Redis 和 NAS 执行读写探测。MySQL 是业务数据的
权威存储，Redis 用于缓存和会话运行锁，NAS 默认位于仓库根目录的 `data/nas/`。

图片识别 AINA 还需要启动独立 YOLO26m 推理容器。在仓库根目录运行：

```powershell
.\scripts\start-vision.cmd
```

脚本会构建同一套本地/生产镜像，自动探测 Docker GPU；GPU 可用时优先使用 CUDA，否则直接以 CPU
模式运行。服务默认只监听 `127.0.0.1:18081`，详细部署与配置见
[`docs/image-recognition.md`](docs/image-recognition.md)。

### 启动前端

另开终端运行：

```bash
cd frontend
npm ci
npm run dev
```

开发服务器默认打开 `http://127.0.0.1:5173`，并将 `/api` 代理到
`http://127.0.0.1:8000`。如需直连其他后端，可配置 `VITE_API_BASE_URL`。

首次打开业务页面会进入登录页。系统支持邮箱密码注册登录；GitHub 登录在配置 OAuth App 后自动显示。
生产环境必须设置唯一的 `UNIBOT_AUTH_SECRET`，所有 Unibot 后端节点使用相同值，并开启
`UNIBOT_AUTH_COOKIE_SECURE=true`。GitHub OAuth App 的本地回调地址为
`http://127.0.0.1:5173/api/auth/github/callback`。完整配置和安全说明见
[`docs/authentication.md`](docs/authentication.md)。

MVP 前端包含真实流式对话、多轮会话管理、Markdown 消息、AINA Widget、`main_widget`
Canvas、能力选择、高风险确认、Tool/Skill/AINA 注册管理、AINA 安装授权以及 Trace
运行中心。MSW 原型数据默认关闭，仅在显式设置 `VITE_ENABLE_MOCKS=true` 时启用。

模型配置可通过标准环境变量 `UNIBOT_LLM_BASE_URL`、`UNIBOT_LLM_API_KEY`、
`UNIBOT_LLM_MODEL` 提供。开发环境也兼容被 Git 忽略的 `backend/.venv` 文件：

```dotenv
base_url=https://your-openai-compatible-api.example/v1
api_key=your-api-key
model=your-model

TZ_STORAGE_MYSQL_DSN=mysql+aiomysql://unibot:unibot@127.0.0.1:13306/unibot
TZ_STORAGE_REDIS_DSN=redis://127.0.0.1:16379/0
TZ_STORAGE_NAS_ROOT_PATH=../data/nas
```

### MVP 后端 API

当前后端不依赖前端即可运行，包含：

- `/auth/register`、`/auth/login`、`/auth/logout`、`/auth/me`：本地账户、HttpOnly 会话和当前用户；
- `/auth/github`、`/auth/github/callback`：GitHub OAuth 授权码登录；
- `POST /chat`：多轮 Agent Loop；
- `POST /chat/stream`：SSE 文本与运行事件流；
- `/conversations`：会话创建、读取、重命名、软删除和恢复；
- 会话支持分类筛选与可恢复的 `run_status`，SSE 断开不会取消后台 Agent 运行；
- `/tools`、`/skills`：Tool 和基础 Skill 注册管理；
- `/ainas`、`/installations`：远程 AINA 注册、探测、安装、授权和卸载；
- `/aina-projects`：生成、校验、按用户导入、列出、下载和删除 AINA Project 1.0 源码包；导入项目保持待部署状态，格式见 [`docs/aina-project-format.md`](docs/aina-project-format.md)，最小示例见 [`examples/text-stats-aina`](examples/text-stats-aina)；
- `POST /ainas/{id}/open`：调用宿主内置 `open_aina` Tool 并返回 Canvas 路由与 `main_widget`；
- 宿主内置 `list_app`、`describe_aina`、`open_aina` Tool：负责 AINA 发现、详情查询与 Canvas 导航，不注册为 AINA；
- 宿主内置 `request_clarification` Tool：需求模糊时生成 Form Widget，支持模型预填和用户提交；
- 首次模型调用统一暴露上述宿主 Tool、已发布独立 Tool 与可路由 AINA 入口；内置 AINA 使用无参数入口激活宿主能力域，远程 AINA 才携带完整任务通过 Gateway 调用；
- 所有 capability 调用在审批和执行前统一校验 input schema，非法参数作为结构化 Tool 错误返回模型修正；
- 内置 `unibot-memory`：通过对话或 Memory Widget 管理事实、偏好、目标和指令，并向相关后续对话注入安全围栏内的记忆；
- `/memories`：记忆新增、搜索、筛选、修改、删除与分类统计；
- 内置 `unibot-documents`：通过文件名或正文关键词搜索文档，并支持创建、读取、按章节编辑、追加、重命名和删除 Markdown 文档，也可创建异步修改任务并在检视草稿后合并；不提供全文覆盖能力；
- `/documents`：Markdown 文档列表、章节更新与文件管理，文件按租户和用户隔离并持久化到 `data/nas/documents/`；
- `/approvals/{id}/confirm|deny`：高风险调用确认；
- `/traces`、`/admin/summary`：调用链和基础管理数据。
- 内置 `unibot-code-runner`：在每用户独立沙箱中执行 Python、Bash 和 Node.js，查看输入输出与历史，并停止或重置环境；
- `/sandboxes`：沙箱初始化、脚本执行、执行历史、停止和重置；完整部署说明见 [`docs/sandbox-platform.md`](docs/sandbox-platform.md)。
- 内置 `unibot-image-recognition`：粘贴、拖入或选择图片，使用 YOLO26m 展示目标框、类别、数量和置信度；
- `/vision/health`、`/vision/detect`：查询实际推理设备并代理图片目标检测，原图不会持久化保存。

最小对话请求：

```bash
curl -c cookies.txt -X POST http://127.0.0.1:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"your-password"}'

curl -b cookies.txt -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"你好，请介绍一下自己"}'
```

### 测试

```bash
cd backend
uv run pytest -q
```

真实 API 冒烟测试会通过 HTTP 覆盖直接对话、真实模型驱动的记忆写入/召回、多轮上下文、远程 Tool、统一能力
入口与 AINA 能力域激活、远程 Widget 输出、`list_app`、`open_aina` Canvas、Trace 和 SSE。先启动后端，再运行：

```bash
cd backend
uv run python scripts/real_api_test.py
```

DeepEval Agent 流程评测会调用真实后端和模型，覆盖普通直答、应用列表、打开 AINA、澄清表单、多轮上下文、
记忆写入/召回/审批删除、远程 Tool 和远程 AINA 路由，并在完成后清理会话、记忆和临时能力。每条成功路径均验证
Tool Correctness、Task Completion、Step Efficiency 和语义正确性。完整覆盖映射见
[`backend/tests/TEST_MATRIX.md`](backend/tests/TEST_MATRIX.md)。先启动后端，再显式指定评测地址：

```powershell
cd backend
$env:UV_PROJECT_ENVIRONMENT=".test-deps"
uv sync --extra dev
$env:UNIBOT_EVAL_BASE_URL="http://127.0.0.1:8000"
$env:PYTHONUTF8="1"
$env:DEEPEVAL_TELEMETRY_OPT_OUT="true"
uv run --extra dev deepeval test run tests/evals -v
```

默认复用 `backend/.env` 中的模型作为 Judge。可通过 `DEEPEVAL_JUDGE_BASE_URL`、`DEEPEVAL_JUDGE_API_KEY`
和 `DEEPEVAL_JUDGE_MODEL` 使用独立 Judge 模型。未设置 `UNIBOT_EVAL_BASE_URL` 时，普通 `pytest` 会跳过真实 Agent 评测。

默认服务地址为 `http://127.0.0.1:8000`，可通过 `UNIBOT_API_URL` 覆盖。脚本会启动
一个仅监听本机随机端口的临时 Tool/AINA Runtime，并使用后端已配置的真实模型。

## 技术栈

| 层           | 技术                 |
| ------------ | -------------------- |
| **后端框架** | FastAPI              |
| **语言**     | Python 3.12          |
| **包管理**   | uv                   |
| **数据库**   | MySQL 8              |
| **缓存**     | Redis                |
| **对象存储** | S3 兼容              |
| **协议**     | AINA (AI-Native App) |
| **前端**     | React 18 + Vite 5    |
| **目标检测** | Ultralytics YOLO26m  |

#

## 内置扩展 AINA

| AINA        | 说明                                                                  |
| ----------- | --------------------------------------------------------------------- |
| **unibot-memory**  | 管理事实、偏好、目标与指令，按需检索注入，使 Agent 具备进程生命周期内的跨会话认知能力 |
| **unibot-documents** | NAS 持久化 Markdown 文档编辑器，支持章节编辑与任务草稿两种模式，并可在对话和 Canvas 中使用 |
| **unibot-image-recognition** | 粘贴或选择图片，通过独立 YOLO26m 服务完成纯目标检测并叠加识别框 |
| **builder** | 提供 AINA 的创建、调试、发布能力，管理员按步骤构建新 AINA             |

## License

[GNU Affero General Public License v3.0](LICENSE) © 2026 Tianzhou Agent

本项目选择完全公开发布，以满足 Ultralytics YOLO 的 AGPL-3.0 开源要求。通过网络向用户提供修改版
服务时，也必须向这些用户提供相应的完整源代码。第三方组件仍分别遵循其自身许可证。
