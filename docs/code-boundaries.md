# Unibot 代码边界与关系图

基于 2026-09-30 当前工作区源码。本文描述已实现的结构，而非目标架构；省略数据模型和辅助函数的逐项依赖。

读图约定：实线表示调用、依赖或数据传递，边上的文字说明具体语义；虚线表示组装、接口实现或控制关系。只有明确标注的进程、HTTP、存储边界代表运行时隔离，其余 subgraph 是同一进程内的代码模块边界。后端路径均相对于 `backend/src/tianzhou_agent_platform/`。

## 1. 系统与进程边界

```mermaid
flowchart LR
    subgraph Browser["浏览器进程 · frontend/src"]
        UI["React App<br/>Chat / Canvas / Settings / Admin"]
        Client["lib/api.ts<br/>REST + fetch SSE"]
        UI --> Client
    end

    Proxy["Vite 开发代理 / Nginx<br/>剥离 /api 前缀"]

    subgraph Backend["后端进程 · FastAPI"]
        Root["main.py · create_app<br/>依赖组装与 lifespan"]
        API["api/router.py + api/*<br/>认证上下文 / 路由 / DTO"]
        Chat["ChatService + AgentRunner<br/>聊天工作流与 Agent 适配"]
        Domains["领域服务<br/>会话 / 模型配置 / 任务 / 文档 / AINA"]
        Background["同进程后台任务<br/>AinaScheduler / DocumentEditWorker<br/>OBS ingest / 流式聊天 producer"]
        IO["Gateway / VisionClient<br/>SandboxDriver / store/*"]
        Root -.-> API
        Root -.-> Background
        API --> Chat
        API --> Domains
        Chat --> Domains
        Chat --> IO
        Domains --> IO
        Background --> IO
    end

    LLM["外部 LLM Provider"]
    Remote["远程 Tool / AINA / A2A 服务"]
    Vision["独立进程 · vision-service/app.py<br/>YOLO 推理"]
    Sand["本地子进程 或 Kubernetes 沙箱"]
    Data[("MySQL / Redis / NAS")]
    Eval["独立评测客户端 · agent-eval<br/>UnibotEvalClient"]

    Client -->|"HTTP + Cookie / SSE"| Proxy
    Proxy --> API
    Eval -->|"REST / SSE"| API
    Chat -->|"模型网络请求"| LLM
    IO -->|"HTTP / A2A"| Remote
    IO -->|"HTTP"| Vision
    IO --> Sand
    IO --> Data
```

- `main.py` 是组合根，主要对象通过 `app.state` 注入；图中的模块不等于独立微服务。
- 默认持久化实现使用 **MySQL**。README 目录树中的 PostgreSQL 标注与当前实现不一致；`store/s3/` 也未被 `create_storage_stores()` 接入。
- `agent-eval` 通过公开 HTTP 接口访问后端，属于外部测试客户端。

依据：[main.py](../backend/src/tianzhou_agent_platform/main.py)、[store/lifecycle.py](../backend/src/tianzhou_agent_platform/store/lifecycle.py)、[nginx.conf](../frontend/nginx.conf)、[vite.config.ts](../frontend/vite.config.ts)、[评测客户端](../agent-eval/unibot_eval/client.py)。

## 2. 前端代码边界与 UI 协议

```mermaid
flowchart TB
    subgraph Bootstrap["启动与路由 · frontend/src"]
        Main["main.tsx<br/>BrowserRouter / AuthProvider / LanguageBoundary"]
        App["App.tsx<br/>RequireAuth / AdminRoute"]
        Shell["AppShell.tsx<br/>WorkspaceProvider / MockSessionProvider / DebugModeProvider"]
        Main --> App --> Shell
    end

    subgraph Pages["页面层 · pages/*"]
        Chat["ChatModePage / CanvasModePage<br/>本地交互状态 / SSE 事件处理 / 会话轮询"]
        Other["Settings / Files / Apps / Schedules<br/>Debug / Feedback / Operations"]
    end
    subgraph Render["渲染层 · components/*"]
        Msg["MessageBubble / LiveTurn<br/>ToolCallCard / ApprovalCard"]
        Widgets["SessionWidgetRenderer / MainWidgetRenderer<br/>按 ainaId 或 widget.kind 分派"]
        Specialized["Document / Memory / CodeRunner<br/>ImageRecognition / Schedule Widgets"]
    end
    subgraph Transport["前端网络适配 · lib/*"]
        Obs["obsApi / operationsApi"]
        API["api.ts<br/>api / streamChat / streamResume<br/>credentials: include"]
    end
    Server["HTTP 边界 · 后端 API"]

    Shell --> Chat
    Shell --> Other
    Chat --> Msg
    Chat --> Widgets
    Widgets --> Specialized
    Chat --> API
    Other --> API
    Other --> Obs --> API
    Specialized --> API
    API -->|"REST / SSE"| Server
    Server -->|"消息 / 事件 / WidgetDefinition JSON"| API
    API -->|"页面消费响应"| Chat
```

`WidgetDefinition` 是前后端之间的声明式数据契约。实际组件由宿主前端提供，不是从 AINA 下载 React 代码。部分专用 Widget 直接调用领域 API，例如图片识别走 `/vision/detect`，不必经过 Agent。`MockSessionProvider` 的名字不代表所有请求都走 mock；MSW 仅在开发模式且显式开启 `VITE_ENABLE_MOCKS` 时启动。

依据：[main.tsx](../frontend/src/main.tsx)、[App.tsx](../frontend/src/App.tsx)、[ChatModePage.tsx](../frontend/src/pages/ChatModePage.tsx)、[MainWidgetRenderer.tsx](../frontend/src/components/widgets/MainWidgetRenderer.tsx)、[api.ts](../frontend/src/lib/api.ts)、[widgets.py](../backend/src/tianzhou_agent_platform/aina/protocol/widgets.py)。

## 3. 后端聊天：应用工作流、业务适配与原生运行时

```mermaid
flowchart TB
    subgraph HTTP["HTTP / 身份边界 · api/*"]
        Actor["main.py 认证中间件<br/>Cookie → RequestActor"]
        Route["api/chat.py + dependencies.py<br/>bind_actor / REST / SSE"]
        Actor --> Route
    end

    subgraph Application["应用工作流 · services/chat.py"]
        Chat["ChatService<br/>run_turn / confirm / deny / stop / resume"]
        Conv["ConversationService<br/>归属校验 / 归档 / run lease / 状态"]
        Model["ModelProviderService<br/>用户模型运行配置"]
        Stops["RunStops<br/>跨节点停止信号"]
        Chat --> Conv
        Chat --> Model
        Chat --> Stops
    end

    subgraph Integration["Unibot 业务适配 · services/agent_integration/*"]
        Runner["AgentRunner<br/>业务输入 → 原生消息 / 执行 / 业务输出"]
        Catalog["CapabilityCatalog + SystemPrompts<br/>发现可用能力与提示词"]
        Policies["scope / approvals / observation / model_calls<br/>能力域、审批规则、事件记录"]
        Exec["CapabilityExecutor<br/>能力 → StructuredTool → 领域调用"]
        History["TranscriptArchiver / StreamRelay<br/>归档与流式事件转换"]
        Runner --> Catalog
        Runner --> Policies
        Runner --> Exec
        Runner --> History
    end

    subgraph Runtime["原生运行时封装 · core/agent_runtime/*"]
        Factory["factory.build_agent<br/>封装 langchain.agents.create_agent"]
        MW["middleware/*<br/>预算 / 摘要 / 顺序去重 / 输出与 Provider 策略"]
        Graph["LangChain / LangGraph Agent<br/>模型 ↔ 工具循环 / interrupt / checkpoint"]
        Factory --> Graph
        MW -.->|"由 Runner 组装"| Graph
    end

    Checkpoint["MySqlCheckpointSaver<br/>store/checkpoint.py"]
    Provider["BaseChatModel<br/>model_providers/factory.py"]
    Domains["宿主 Tool / 内置 AINA / Gateway"]
    Events["RunEventPublisher → ObservabilityAspect"]

    Route --> Chat --> Runner
    Runner --> Factory
    Policies -.->|"注入 middleware"| Graph
    Exec -.->|"注入 StructuredTool"| Graph
    Graph -->|"执行工具 coroutine"| Exec
    Exec --> Domains
    Graph --> Provider
    Graph --> Checkpoint
    History --> Conv
    History -->|"事件 sink"| Route
    Chat --> Events
    Runner --> Events
```

- `ChatService` 负责一次业务运行的生命周期；`AgentRunner` 将业务语义适配到原生 Agent。
- `core/agent_runtime/factory.py` 接收原生模型、工具、middleware、checkpointer，不接收 `ChatRequest`，不读取会话，也不处理 HTTP SSE。
- 图中是职责边界，并非严格依赖倒置架构：部分 `api/*` 和 AINA 代码直接依赖 repository；`CapabilityExecutor` 也直接持有 repository、gateway 和领域服务。
- `ConversationService` 通过 `ConversationRepository` 等 Protocol 访问存储；生产组装注入 `PersistentRepository`。这种接口边界没有覆盖全项目。

依据：[ChatService](../backend/src/tianzhou_agent_platform/services/chat.py)、[AgentRunner](../backend/src/tianzhou_agent_platform/services/agent_integration/runner.py)、[运行时工厂](../backend/src/tianzhou_agent_platform/core/agent_runtime/factory.py)、[CapabilityExecutor](../backend/src/tianzhou_agent_platform/services/agent_integration/executor.py)、[会话存储协议](../backend/src/tianzhou_agent_platform/conversations/repository.py)。

## 4. 能力执行边界：内置、远程、托管与沙箱

```mermaid
flowchart LR
    subgraph Host["后端进程 · 宿主能力"]
        Exec["CapabilityExecutor.execute"]
        Scope["activate_scope<br/>收敛能力与提示词"]
        Platform["services/platform_tools.py<br/>应用发现 / 打开 / 澄清 / 任务工具"]
        Builtin["aina/builtin.py · invoke_builtin<br/>Memory / Documents / Code Runner"]
        Domain["Repository / DocumentService<br/>DocumentEditTaskService / TaskService"]
        Gateway["aina/gateway.py<br/>RemoteCapabilityGateway"]
        Managed["aina/managed.py<br/>ManagedAinaRuntime"]
        Projects["AinaProjectService<br/>源码包与部署元数据"]
        Sandbox["sandbox/service.py<br/>SandboxService"]
        Driver["sandbox/drivers.py<br/>SandboxDriver 接口"]
        Schedule["AinaScheduler"]
        VisionAPI["api/vision.py → VisionClient"]

        Exec -->|"内置 AINA 入口"| Scope
        Exec -->|"宿主 Tool"| Platform --> Domain
        Exec -->|"内置领域 Tool"| Builtin
        Builtin --> Domain
        Builtin -->|"Code Runner"| Sandbox
        Exec -->|"远程 Tool / AINA"| Gateway
        Schedule -->|"定时 AINA 调用"| Gateway
        Gateway -->|"runtime=managed"| Managed
        Managed --> Projects
        Managed --> Sandbox --> Driver
    end

    External["进程外 · 远程服务<br/>Tool HTTP / AINA HTTP / A2A"]
    Vision["进程外 · vision-service/app.py"]
    Local["LocalProcessSandboxDriver<br/>本地子进程 + 工作区目录"]
    KDriver["KubernetesSandboxDriver"]
    subgraph Cluster["Kubernetes 控制面与用户沙箱"]
        CR["UserSandbox CRD"]
        Operator["sandbox/operator/main.py<br/>reconcile"]
        Pod["用户 Pod / Service / PVC<br/>sandbox/sandboxd/app.py"]
        CR -.->|"watch"| Operator
        Operator -.->|"创建与协调"| Pod
    end

    Gateway -->|"网络协议与授权边界"| External
    VisionAPI -->|"HTTP 图片上传"| Vision
    Driver -.->|"local 实现"| Local
    Driver -.->|"kubernetes 实现"| KDriver
    KDriver -->|"K8s API：创建与查询"| CR
    KDriver -->|"HTTP：exec / files"| Pod
```

- 内置 AINA 入口调用 `activate_scope()`，之后由同一个宿主 Agent 调用该能力域的工具；不会通过 Gateway 调用内置 AINA，也不等同于打开 Canvas。
- Gateway 按 `runtime.type` 区分远程与托管。托管 AINA 将执行交给 SandboxService；定时调度也直接调用 Gateway。
- 本地驱动只是子进程边界，不能当作容器或 gVisor 隔离。启用认证时，本地执行须显式允许；Kubernetes 驱动可指定 RuntimeClass，实际隔离由部署配置决定。
- Operator 负责生命周期控制；执行请求由后端驱动直接发给 `sandboxd`，不经 Operator 转发。
- 图片识别当前走独立 `/vision` API。虽然注册为内置 AINA，其推理调用不在 `invoke_builtin()` 的工具分派中。

依据：[executor.py](../backend/src/tianzhou_agent_platform/services/agent_integration/executor.py)、[gateway.py](../backend/src/tianzhou_agent_platform/aina/gateway.py)、[builtin.py](../backend/src/tianzhou_agent_platform/aina/builtin.py)、[managed.py](../backend/src/tianzhou_agent_platform/aina/managed.py)、[drivers.py](../backend/src/tianzhou_agent_platform/sandbox/drivers.py)、[factory.py](../backend/src/tianzhou_agent_platform/sandbox/factory.py)、[Operator](../sandbox/operator/main.py)、[Vision API](../backend/src/tianzhou_agent_platform/api/vision.py)。

## 5. 状态归属与持久化边界

```mermaid
flowchart TB
    subgraph Business["业务状态"]
        Features["会话 / 认证 / 模型配置 / AINA / 记忆等"]
        Repo["PersistentRepository<br/>继承 InMemoryRepository"]
        Tasks["TaskService → MySqlSessionTaskStore"]
        Files["DocumentService<br/>NasAinaProjectArtifactStore"]
        Features --> Repo
    end
    subgraph Working["Agent 工作状态"]
        Graph["LangGraph Agent"]
        CP["MySqlCheckpointSaver<br/>lc-v2:conversation_id"]
        Stop["RunStops"]
        Graph --> CP
    end
    subgraph Obs["观测写入与查询"]
        Events["RunEventPublisher / ModelCallRecorder"]
        Aspect["ObservabilityAspect"]
        OTel["OTel SpanProcessor / LogHandler"]
        Buffer["RedisObsBuffer"]
        Worker["RedisObsIngestWorker"]
        Store["ObservabilityStore<br/>独立数据库连接池"]
        Raw["RawIoWriter"]
        Query["ObsQueryService / OperationsAnalyticsService"]
        Events --> Aspect
        Aspect --> Buffer
        OTel --> Buffer
        Aspect --> Raw
        Worker --> Store
        Aspect -.->|"持久化屏障失败时终态兜底"| Store
        Query --> Store
    end

    MySQL[("MySQL<br/>业务表 / checkpoint 表 / OBS 表")]
    Redis[("Redis<br/>缓存 / lease / 停止信号 / task revision")]
    Stream[("Redis Streams<br/>OBS 持久缓冲 / AOF")]
    NAS[("NAS 文件<br/>文档 / AINA 源码包")]
    RawFS[("obs_raw_root 文件系统<br/>原始模型输入输出")]

    Repo -->|"权威业务记录"| MySQL
    Repo -->|"缓存与运行 lease"| Redis
    Tasks --> MySQL
    Tasks -->|"revision 缓存"| Redis
    CP --> MySQL
    Stop --> Redis
    Files --> NAS
    Buffer -->|"追加"| Stream
    Stream -->|"消费 / 重放"| Worker
    Store --> MySQL
    Raw --> RawFS
    Query -->|"详情读取"| RawFS
```

- 会话消息归档、Agent checkpoint、观测数据是三份不同职责的数据。checkpoint 是工作状态，不能等同于完整历史归档；Runner 在调用后裁剪旧 checkpoint。
- OBS 启用时走 Redis Streams → ingest worker → MySQL。
- OBS 原始输入输出写入 `obs_raw_root`，不经过 `NasStore`，不应默认画成同一块业务 NAS。
- 不提供持久化配置时，`create_app()` 可组装 InMemoryRepository / InMemorySaver / InMemorySessionTaskStore；正式模块入口传入 StorageSettings。

依据：[main.py](../backend/src/tianzhou_agent_platform/main.py)、[repository.py](../backend/src/tianzhou_agent_platform/store/repository.py)、[checkpoint.py](../backend/src/tianzhou_agent_platform/store/checkpoint.py)、[tasks/store.py](../backend/src/tianzhou_agent_platform/tasks/store.py)、[observability/service.py](../backend/src/tianzhou_agent_platform/observability/service.py)、[observability/stream.py](../backend/src/tianzhou_agent_platform/observability/stream.py)。

## 6. 聊天、审批与断线恢复时序

```mermaid
sequenceDiagram
    participant UI as Chat / Canvas 页面
    participant API as api/chat.py
    participant Chat as ChatService
    participant Conv as ConversationService
    participant Runner as AgentRunner
    participant Graph as 原生 Agent + checkpoint
    participant Tool as CapabilityExecutor

    UI->>API: POST /chat/stream + Cookie
    Note over API: 中间件识别用户，bind_actor 绑定可信身份
    API->>API: 创建独立 asyncio producer 与事件 queue
    API->>Chat: run_turn(payload, event_sink)
    Chat->>Conv: 归属校验 + start_run / lease
    Chat->>Runner: run_turn + 用户模型上下文
    Runner->>Graph: 载入 checkpoint，提交本轮新消息
    Note over Runner,Graph: 无 checkpoint 时从会话归档构造初始消息
    loop 模型与工具循环
        Graph->>Graph: 模型调用 + middleware
        opt 已获准的工具调用
            Graph->>Tool: StructuredTool coroutine
            Tool-->>Graph: 领域结果 / Widget 数据
        end
        Graph-->>Runner: 原生流事件与状态
        Runner-->>API: StreamRelay → event_sink → queue
        API-->>UI: SSE 消息与运行事件
    end
    alt 高风险调用触发 interrupt
        Graph-->>Runner: 审批中断 + checkpoint
        Runner-->>Chat: approval_required
        Chat->>Conv: 保存暂停状态并释放 run lease
        UI->>Chat: 确认 / 拒绝（经审批 API）
        Chat->>Runner: open_approval
        Runner->>Graph: Command(resume=decisions)
        Note over Runner,Graph: 拒绝关闭暂停批次；确认后继续执行
    else 正常完成
        Runner-->>Chat: ChatResponse
        Chat->>Conv: finish_run，状态回到 idle
        Chat-->>API: 响应
        API-->>UI: message.completed
    end
    Note over UI,API: 若任意时刻 SSE 断开，producer 不随连接取消
    UI->>Conv: 返回页面后读取 / 轮询会话（经会话 API）
    Conv-->>UI: 已归档消息 + run_status
    Note over UI,Graph: 显式 stop 通过 RunStops；显式 resume 从 checkpoint 继续
```

连接恢复、审批续跑、停止后续跑是三种不同机制。后台 producer 仍在 FastAPI 进程中，SSE 断开不取消执行不等于进程崩溃后任务自动继续。

依据：[api/chat.py](../backend/src/tianzhou_agent_platform/api/chat.py)、[services/chat.py](../backend/src/tianzhou_agent_platform/services/chat.py)、[runner.py](../backend/src/tianzhou_agent_platform/services/agent_integration/runner.py)、[run_stops.py](../backend/src/tianzhou_agent_platform/services/run_stops.py)、[ChatModePage.tsx](../frontend/src/pages/ChatModePage.tsx)。
