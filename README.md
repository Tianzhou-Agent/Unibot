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
├── frontend/                   # 前端（规划中）
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
uv sync
```

### 启动服务

```bash
uv run tianzhou-agent-platform
```

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
