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
| **Canvas 画布**     | 三栏布局的可视化工作区，支持 Widget 直调、增量更新 |
| **Inline Card**     | 对话流内嵌 HTML 卡片（摘要、选项、消歧选择）       |
| **异步任务**        | 后台长任务 + 外部事件推送，待办栏独立上下文        |
| **记忆扩展**        | 跨会话持续认知，异步固化 + 预加载                  |
| **文件附件**        | 文件上传、签名 URL、                               |
| **多 LLM Provider** | 支持多LLM Provider配置，用户级隔离                 |

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
│           │   ├── unibot/     # 系统内置 App
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
├── scripts/                    # 脚本工具
├── docs/                       # 文档
└── LICENSE                     # MIT License
```

## 快速开始

### 环境要求

- **Python 3.12+**
- **PostgreSQL** 数据库
- **uv** 包管理器（推荐）

### 安装依赖

```bash
cd backend
uv sync --extra dev
```

### 启动服务

```bash
uv run tianzhou-agent-platform
```

### 启动前端

另开终端运行：

```bash
cd frontend
npm ci
npm run dev
```

开发服务器默认打开 `http://127.0.0.1:5173`，并将 `/api` 代理到
`http://127.0.0.1:8000`。如需直连其他后端，可配置 `VITE_API_BASE_URL`。

MVP 前端包含真实流式对话、多轮会话管理、能力选择、高风险确认、Tool/Skill/AINA
注册管理、AINA 安装授权以及 Trace 运行中心。MSW 原型数据默认关闭，仅在显式设置
`VITE_ENABLE_MOCKS=true` 时启用。

模型配置可通过标准环境变量 `UNIBOT_LLM_BASE_URL`、`UNIBOT_LLM_API_KEY`、
`UNIBOT_LLM_MODEL` 提供。开发环境也兼容被 Git 忽略的 `backend/.venv` 文件：

```dotenv
base_url=https://your-openai-compatible-api.example/v1
api_key=your-api-key
model=your-model
```

### MVP 后端 API

当前后端不依赖前端即可运行，包含：

- `POST /chat`：多轮 Agent Loop；
- `POST /chat/stream`：SSE 文本与运行事件流；
- `/conversations`：会话创建、读取、重命名、软删除和恢复；
- `/tools`、`/skills`：Tool 和基础 Skill 注册管理；
- `/ainas`、`/installations`：远程 AINA 注册、探测、安装、授权和卸载；
- `/approvals/{id}/confirm|deny`：高风险调用确认；
- `/traces`、`/admin/summary`：调用链和基础管理数据。

最小对话请求：

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"你好，请介绍一下自己"}'
```

### 测试

```bash
cd backend
uv run pytest -q
```

真实 API 冒烟测试会通过 HTTP 覆盖直接对话、多轮上下文、远程 Tool、远程 AINA、
Trace 和 SSE。先启动后端，再运行：

```bash
cd backend
uv run python scripts/real_api_test.py
```

默认服务地址为 `http://127.0.0.1:8000`，可通过 `UNIBOT_API_URL` 覆盖。脚本会启动
一个仅监听本机随机端口的临时 Tool/AINA Runtime，并使用后端已配置的真实模型。

## 技术栈

| 层           | 技术                 |
| ------------ | -------------------- |
| **后端框架** | FastAPI              |
| **语言**     | Python 3.12          |
| **包管理**   | uv                   |
| **数据库**   | PostgreSQL           |
| **缓存**     | Redis                |
| **对象存储** | S3 兼容              |
| **协议**     | AINA (AI-Native App) |

#

## 内置扩展 AINA

| AINA        | 说明                                                                  |
| ----------- | --------------------------------------------------------------------- |
| **memory**  | 将对话知识结构化持久化，按需检索注入，使 Agent 具备跨会话持续认知能力 |
| **builder** | 提供 AINA 的创建、调试、发布能力，管理员按步骤构建新 AINA             |

## License

[MIT](LICENSE) © 2026 Tianzhou Agent
