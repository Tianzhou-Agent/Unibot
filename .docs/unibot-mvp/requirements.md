# 天舟 Agent 框架需求分析文档

**文档版本：** V0.1  
**文档状态：** 需求初稿  
**目标读者：** 产品经理、架构师、后端工程师、前端工程师、AI 工程师、安全与运维人员

---

## 1. 项目概述

### 1.1 项目背景

本项目拟建设一个在线 Agent 框架，为用户提供统一的智能交互与能力运行环境。

Agent 除基础对话能力外，还需要支持以下三类扩展能力：

1. **Skill**
   - 面向特定任务的智能行为或工作流能力。
   - 主要描述 Agent “如何完成一类任务”。
   - 可以组合 Prompt、上下文处理规则、模型调用策略和 Tool 调用逻辑。

2. **Tool**
   - 可被 Agent 或 Skill 调用的原子执行能力。
   - 主要描述系统“可以执行什么操作”。
   - 例如搜索、数据库查询、代码执行、文件读取、发送邮件、调用第三方 API。

3. **AINA**
   - AINA 全称暂定义为 **AI-Native Application**。
   - AINA 是面向用户场景的可安装、可注册、可运行的 AI 原生应用。
   - 一个 AINA 可以集成一个或多个 Skill、Tool、提示模板、数据源和交互界面。
   - 第三方开发者或普通用户可依据统一的 **AINA Protocol** 自主设计 AINA，并注册到 Agent 框架中运行。

Agent 框架不直接约束每个 AINA 的内部实现，而是通过 AINA Protocol 约定其元数据、能力声明、运行接口、权限、安全策略和生命周期。

---

## 2. 产品目标

### 2.1 核心目标

1. 提供统一的在线 Agent 对话入口。
2. 建立标准化的 Skill、Tool 和 AINA 能力模型。
3. 支持用户自主创建、注册、安装和使用 AINA。
4. 通过 AINA Protocol 解耦 Agent 框架与具体应用实现。
5. 支持 Agent 根据用户意图自动发现、选择和调用合适的能力。
6. 确保第三方能力运行过程可控、可观测、可审计。
7. 支持框架能力持续扩展，而无需修改 Agent 核心逻辑。

### 2.2 非目标

初期版本不包含以下目标：

1. 不建立完整的第三方应用商业结算平台。
2. 不提供通用低代码应用开发平台。
3. 不要求所有 AINA 使用同一种编程语言或技术栈。
4. 不允许 AINA 无约束地访问宿主系统资源。
5. 不要求 Agent 完全自主执行高风险操作。
6. 不在首期实现复杂的多 Agent 社会化协作网络。

---

## 3. 核心概念定义

### 3.1 Agent

Agent 是面向用户的智能交互主体，负责：

- 理解用户意图；
- 管理会话上下文；
- 生成自然语言响应；
- 发现和选择 Skill、Tool 或 AINA；
- 编排能力调用；
- 处理调用结果；
- 执行权限确认；
- 管理任务状态；
- 输出最终结果。

### 3.2 Skill

Skill 是一种面向任务的可复用智能能力定义。

Skill 通常包含：

- Skill 名称和描述；
- 适用场景；
- 输入输出约束；
- Prompt 或行为指令；
- 工作流定义；
- 所依赖的 Tool；
- 模型配置；
- 错误处理规则；
- 权限需求。

Skill 可以是：

- 纯提示型 Skill；
- 单 Tool 调用型 Skill；
- 多步骤工作流 Skill；
- 条件分支型 Skill；
- 人机协同型 Skill；
- 由 AINA 私有管理的内部 Skill。

### 3.3 Tool

Tool 是可以被 Agent、Skill 或 AINA 调用的原子执行单元。

Tool 应具有明确的：

- Tool 标识；
- 功能描述；
- 输入参数 Schema；
- 输出结果 Schema；
- 权限声明；
- 副作用等级；
- 超时与重试策略；
- 调用成本；
- 可用状态。

典型 Tool 包括：

- Web 搜索；
- HTTP API 调用；
- 数据库查询；
- 文件操作；
- 代码执行；
- 邮件发送；
- 日历操作；
- 企业内部系统操作。

### 3.4 AINA

AINA 是独立注册到 Agent 框架中的 AI 原生应用单元。

AINA 与 Skill 的核心区别如下：

| 维度     | Skill                     | AINA                            |
| -------- | ------------------------- | ------------------------------- |
| 定位     | 单项任务能力              | 完整业务应用                    |
| 复杂度   | 低至中                    | 中至高                          |
| 组成     | Prompt、工作流、Tool      | Skill、Tool、数据、状态、界面   |
| 生命周期 | 通常随 Agent 或 AINA 加载 | 独立注册、安装、升级和卸载      |
| 状态管理 | 通常无状态或弱状态        | 可维护独立业务状态              |
| 用户入口 | 通常由 Agent 自动调用     | 可由用户显式进入或由 Agent 调用 |
| 权限范围 | 通常较小                  | 可能拥有独立权限集合            |
| 交互方式 | 以对话调用为主            | 对话、表单、卡片、页面或任务流  |

### 3.5 AINA Protocol

AINA Protocol 是 Agent 框架与 AINA 之间的标准契约，用于约定：

- 应用身份；
- 元数据；
- 能力声明；
- Skill 和 Tool 暴露方式；
- 输入输出格式；
- 生命周期；
- 权限；
- 身份认证；
- 状态管理；
- 事件通信；
- UI 描述；
- 错误处理；
- 版本兼容；
- 监控与审计。

AINA Protocol 应与具体编程语言和运行环境解耦。

---

## 4. 目标用户与角色

### 4.1 终端用户

终端用户通过对话或应用入口使用 Agent 和 AINA。

主要需求：

- 与 Agent 进行自然语言对话；
- 查找和安装 AINA；
- 授权 AINA 使用必要资源；
- 在会话中调用 AINA；
- 查看任务执行进度；
- 管理个人数据和授权；
- 停止或撤销任务。

### 4.2 AINA 开发者

AINA 开发者依据 AINA Protocol 创建应用。

主要需求：

- 创建和配置 AINA；
- 声明 Skill、Tool 和权限；
- 调试 Protocol 接口；
- 提交应用注册；
- 查看运行日志和错误；
- 发布新版本；
- 管理兼容性；
- 撤回或停用应用。

### 4.3 Tool 开发者

Tool 开发者负责提供原子执行能力。

主要需求：

- 定义 Tool Schema；
- 配置认证方式；
- 配置超时和限流；
- 查看调用记录；
- 管理 Tool 版本；
- 控制 Tool 可见范围。

### 4.4 平台管理员

平台管理员负责平台治理。

主要需求：

- 审核 AINA 和 Tool；
- 设置安全策略；
- 管理租户和用户；
- 禁用异常应用；
- 查看审计记录；
- 处理配额、限流和资源使用；
- 配置模型和运行环境。

---

## 5. 总体业务流程

### 5.1 基础对话流程

1. 用户向 Agent 发送消息。
2. Agent 获取会话上下文。
3. Agent 判断是否可以直接回答。
4. 若无需外部能力，Agent 直接生成响应。
5. 若需要扩展能力，进入能力发现与调用流程。
6. Agent 汇总结果并回复用户。
7. 系统保存会话记录和调用轨迹。

### 5.2 能力发现流程

1. Agent 解析用户意图。
2. 系统从已启用的 Skill、Tool 和 AINA 中检索候选能力。
3. 系统根据描述、输入条件、权限、可用性和成本进行筛选。
4. Agent 选择最合适的能力。
5. 若候选能力存在歧义，Agent可以：
   - 请求用户补充信息；
   - 展示可选能力；
   - 根据上下文自动选择。
6. 进入能力调用流程。

### 5.3 AINA 注册流程

1. 开发者创建 AINA Manifest。
2. 开发者配置 Protocol Endpoint 或上传可运行制品。
3. 平台校验 Protocol 版本和 Manifest 格式。
4. 平台执行能力探测。
5. 平台执行权限和安全检查。
6. AINA 进入草稿、测试或待审核状态。
7. 审核通过后发布。
8. AINA 可被用户安装或由管理员预装。

### 5.4 AINA 运行流程

1. 用户显式调用 AINA，或 Agent 根据意图选择 AINA。
2. 平台检查 AINA 状态、版本和用户授权。
3. 平台初始化运行上下文。
4. Agent 向 AINA 发送标准请求。
5. AINA 返回：
   - 最终结果；
   - 结构化 UI；
   - Tool 调用请求；
   - 追加信息请求；
   - 异步任务信息；
   - 错误信息。
6. Agent 根据结果继续编排或向用户响应。
7. 平台记录完整调用链路和资源消耗。

---

## 6. 功能需求

## 6.1 Agent 基础能力

### FR-AGENT-001 会话管理

系统必须支持：

- 创建会话；
- 获取会话；
- 重命名会话；
- 删除会话；
- 恢复历史会话；
- 多轮上下文；
- 会话级配置；
- 会话级应用启用状态。

### FR-AGENT-002 消息处理

系统必须支持以下消息类型：

- 用户文本；
- Agent 文本；
- 系统消息；
- Tool 请求和结果；
- AINA 请求和结果；
- 图片；
- 文件；
- 结构化数据；
- UI 卡片；
- 状态事件；
- 错误消息。

### FR-AGENT-003 上下文管理

系统应支持：

- 会话窗口管理；
- 长对话摘要；
- 重要信息提取；
- 用户级记忆；
- AINA 私有上下文；
- 敏感信息隔离；
- 上下文裁剪策略。

### FR-AGENT-004 能力路由

Agent 必须能够：

- 检索可用能力；
- 判断直接回答或调用能力；
- 选择 Skill、Tool 或 AINA；
- 处理能力之间的依赖；
- 执行多步骤调用；
- 防止无效循环调用；
- 在能力失败后执行降级策略。

---

## 6.2 Skill 管理

### FR-SKILL-001 Skill 定义

Skill 至少应包含：

- 唯一标识；
- 名称；
- 描述；
- 版本；
- 输入 Schema；
- 输出 Schema；
- 行为指令；
- 依赖的 Tool；
- 权限声明；
- 发布者；
- 可见范围；
- 状态。

### FR-SKILL-002 Skill 类型

系统至少支持：

- Prompt Skill；
- Tool Wrapper Skill；
- Workflow Skill；
- AINA Internal Skill。

### FR-SKILL-003 Skill 生命周期

Skill 应支持以下状态：

- Draft；
- Testing；
- Published；
- Deprecated；
- Disabled；
- Archived。

### FR-SKILL-004 Skill 调试

开发者应能够：

- 输入测试数据；
- 查看 Prompt；
- 查看模型输入输出；
- 查看 Tool 调用；
- 查看 Token 消耗；
- 查看错误堆栈；
- 保存测试用例。

---

## 6.3 Tool 管理

### FR-TOOL-001 Tool 注册

Tool 注册信息至少包括：

- Tool ID；
- 名称；
- 描述；
- 版本；
- 输入 JSON Schema；
- 输出 JSON Schema；
- 执行 Endpoint；
- 认证方式；
- 超时时间；
- 副作用等级；
- 权限范围；
- 限流配置；
- 可见范围。

### FR-TOOL-002 Tool 调用

Tool Runtime 必须支持：

- 参数校验；
- 身份认证；
- 权限校验；
- 超时；
- 重试；
- 幂等性；
- 请求追踪；
- 响应校验；
- 错误标准化；
- 调用审计。

### FR-TOOL-003 副作用分类

Tool 应声明以下副作用等级之一：

- `none`：只读操作；
- `low`：可撤销或低风险修改；
- `medium`：重要数据变更；
- `high`：支付、删除、发布、发送等高风险操作。

对于 `medium` 和 `high` 等级的操作，平台必须支持用户确认机制。

---

## 6.4 AINA 管理

### FR-AINA-001 AINA 创建

开发者应能够创建 AINA，并配置：

- 基本信息；
- 图标；
- 描述；
- 分类；
- Protocol 版本；
- Runtime Endpoint；
- Skill 列表；
- Tool 列表；
- 权限；
- UI 能力；
- 数据存储方式；
- 支持的模型；
- 版本兼容范围。

### FR-AINA-002 AINA 注册

平台必须：

- 校验 Manifest；
- 校验 Protocol 版本；
- 探测 Endpoint；
- 校验 Skill 和 Tool 声明；
- 校验权限合理性；
- 验证签名；
- 生成应用唯一标识；
- 保存注册记录。

### FR-AINA-003 AINA 安装

用户安装 AINA 时，平台应：

- 展示应用信息；
- 展示发布者；
- 展示权限清单；
- 展示数据使用说明；
- 获取用户授权；
- 创建用户与 AINA 的安装关系；
- 初始化应用配置。

### FR-AINA-004 AINA 卸载

用户卸载 AINA 时，平台应：

- 取消应用调用权限；
- 撤销授权令牌；
- 停止未完成任务或提示用户处理；
- 根据数据策略删除或保留应用数据；
- 记录卸载审计事件。

### FR-AINA-005 AINA 调用方式

系统至少应支持：

- 用户通过名称显式调用；
- 用户从应用列表进入；
- Agent 自动路由调用；
- Skill 内部调用；
- 其他 AINA 在授权后调用；
- 系统事件触发。

### FR-AINA-006 AINA 版本管理

系统必须支持：

- 语义化版本；
- 多版本并存；
- 灰度发布；
- 回滚；
- 版本弃用；
- Protocol 兼容检查；
- 用户版本迁移；
- 数据 Schema 迁移。

### FR-AINA-007 AINA 状态管理

AINA 应支持以下状态：

- Draft；
- Testing；
- Reviewing；
- Published；
- Suspended；
- Deprecated；
- Archived。

---

## 7. AINA Protocol 需求

## 7.1 Protocol 设计目标

AINA Protocol 应满足以下要求：

1. 与编程语言无关。
2. 与具体大模型厂商无关。
3. 支持远程服务和平台托管运行两种模式。
4. 支持同步和异步任务。
5. 支持流式输出。
6. 支持多模态输入输出。
7. 支持结构化 UI。
8. 支持能力动态发现。
9. 支持细粒度权限控制。
10. 支持版本协商和向后兼容。
11. 支持完整可观测性。
12. 支持安全隔离。

---

## 7.2 AINA Manifest

每个 AINA 必须提供 Manifest。

建议的基础结构如下：

```json
{
  "protocol_version": "1.0",
  "aina": {
    "id": "com.example.research-assistant",
    "name": "Research Assistant",
    "version": "1.2.0",
    "description": "用于资料检索、分析和报告生成的 AI 原生应用",
    "publisher": {
      "id": "developer-id",
      "name": "Example Developer"
    }
  },
  "runtime": {
    "type": "remote",
    "endpoint": "https://example.com/aina",
    "streaming": true,
    "async_tasks": true
  },
  "capabilities": {
    "skills": [],
    "tools": [],
    "ui": [],
    "events": []
  },
  "permissions": [],
  "authentication": {
    "type": "oauth2"
  }
}
```

### Manifest 必填字段

- Protocol 版本；
- AINA ID；
- 名称；
- 版本；
- 描述；
- 发布者；
- Runtime 类型；
- 能力声明；
- 权限声明；
- 认证方式。

### Manifest 可选字段

- 图标；
- 多语言信息；
- 分类；
- 定价信息；
- 隐私策略；
- 服务条款；
- 支持地址；
- 模型要求；
- 地域限制；
- 数据驻留要求；
- UI 扩展；
- Webhook；
- 事件订阅；
- 健康检查地址。

---

## 7.3 Runtime 类型

AINA Protocol 应至少支持两种 Runtime：

### 远程 Runtime

AINA 以独立服务形式运行，平台通过标准网络协议调用。

优点：

- 技术栈自由；
- 独立部署；
- 独立扩缩容；
- 适合企业系统集成。

约束：

- 需要公网或专用网络 Endpoint；
- 必须满足平台认证和签名要求；
- 必须提供健康检查。

### 托管 Runtime

AINA 制品上传至平台，由平台提供隔离运行环境。

优点：

- 部署简单；
- 统一资源管理；
- 统一日志和安全策略。

约束：

- 必须遵守平台支持的运行时规范；
- 文件、网络和计算资源受到限制；
- 不允许访问未声明资源。

---

## 7.4 Protocol 标准接口

AINA 至少应实现以下逻辑接口。

### `describe`

返回 AINA 的实时能力信息。

用途：

- 能力发现；
- 版本协商；
- 健康状态检查；
- 动态配置获取。

### `invoke`

执行一次 AINA 请求。

请求应包括：

- Request ID；
- User ID 或匿名主体；
- Tenant ID；
- Session ID；
- Conversation ID；
- 输入内容；
- 上下文；
- 授权信息；
- Locale；
- Timezone；
- Trace 信息；
- 可用 Tool 列表。

### `stream`

以流式事件返回运行结果。

事件类型至少包括：

- `message.delta`；
- `message.completed`；
- `tool.requested`；
- `tool.completed`；
- `input.required`；
- `approval.required`；
- `task.progress`；
- `task.completed`；
- `error`。

### `task.get`

获取异步任务状态。

### `task.cancel`

取消正在运行的任务。

### `health`

返回运行状态、版本和依赖检查结果。

---

## 7.5 标准调用响应

AINA 响应应支持以下一种或多种内容：

- 文本；
- Markdown；
- JSON 数据；
- 文件；
- 图片；
- 音频；
- 视频；
- UI Schema；
- Tool 调用请求；
- 用户输入请求；
- 授权确认请求；
- 异步任务引用；
- 错误对象。

建议统一响应结构：

```json
{
  "request_id": "req_123",
  "status": "completed",
  "outputs": [
    {
      "type": "text",
      "content": "任务已经完成"
    }
  ],
  "usage": {
    "input_tokens": 100,
    "output_tokens": 50
  },
  "trace_id": "trace_123"
}
```

---

## 7.6 错误协议

AINA 应返回标准化错误对象。

错误至少包括：

- 错误码；
- 错误消息；
- 是否可重试；
- 错误来源；
- 用户可见信息；
- 调试信息；
- Trace ID。

建议错误分类：

- `INVALID_REQUEST`；
- `AUTHENTICATION_FAILED`；
- `PERMISSION_DENIED`；
- `RESOURCE_NOT_FOUND`；
- `RATE_LIMITED`；
- `DEPENDENCY_FAILED`；
- `TIMEOUT`；
- `CONFLICT`；
- `UNSUPPORTED_PROTOCOL`；
- `INTERNAL_ERROR`。

平台不得直接向终端用户暴露敏感堆栈信息。

---

## 7.7 Protocol 版本兼容

Protocol 应采用显式版本号。

建议规则：

- 主版本变化表示不兼容变更；
- 次版本变化表示向后兼容能力增加；
- 修订版本变化表示兼容性修复；
- 平台和 AINA 在调用前完成版本协商；
- 平台应定义每个 Protocol 版本的支持周期；
- AINA 应声明最低和最高兼容版本。

---

## 8. 权限与安全需求

### 8.1 权限模型

权限至少分为：

- 用户数据权限；
- 会话权限；
- 文件权限；
- 网络访问权限；
- Tool 调用权限；
- 外部账户权限；
- AINA 间调用权限；
- 后台任务权限；
- 通知权限。

权限必须遵循：

- 最小权限原则；
- 显式声明；
- 用户可见；
- 用户可撤销；
- 调用时校验；
- 全程审计。

### 8.2 用户确认

以下操作默认应要求用户确认：

- 发送消息或邮件；
- 发布公开内容；
- 删除数据；
- 修改关键业务数据；
- 支付；
- 创建订单；
- 执行不可逆操作；
- 向第三方传输敏感信息。

### 8.3 AINA 隔离

平台必须保证：

- 不同租户数据隔离；
- 不同 AINA 数据隔离；
- 不同用户数据隔离；
- Secret 不进入模型上下文；
- Token 按需签发；
- 网络访问受策略控制；
- 文件访问限定在授权范围；
- 托管 Runtime 使用沙箱运行。

### 8.4 Prompt Injection 防护

系统应对来自网页、文件、Tool 和 AINA 的内容进行信任分级。

系统不得将外部内容中的指令自动视为系统指令。

平台应支持：

- 指令来源标记；
- 上下文信任边界；
- Tool 参数二次校验；
- 敏感操作确认；
- 输出内容安全检测；
- 数据外泄检测。

### 8.5 供应链安全

AINA 发布流程应支持：

- 发布者身份验证；
- Manifest 签名；
- 制品哈希；
- 依赖扫描；
- 漏洞扫描；
- 恶意行为检测；
- 版本不可变记录；
- 快速下架机制。

---

## 9. 数据模型需求

核心实体建议包括：

### User

- User ID；
- 基本信息；
- Tenant ID；
- 偏好设置；
- 授权信息；
- 配额。

### Conversation

- Conversation ID；
- User ID；
- Agent ID；
- 标题；
- 摘要；
- 创建时间；
- 更新时间；
- 状态。

### Message

- Message ID；
- Conversation ID；
- Role；
- Content；
- Content Type；
- Parent Message ID；
- Trace ID；
- 创建时间。

### Skill

- Skill ID；
- Version；
- Manifest；
- Publisher；
- Visibility；
- Status。

### Tool

- Tool ID；
- Version；
- Schema；
- Endpoint；
- Authentication；
- Side Effect Level；
- Status。

### AINA

- AINA ID；
- Version；
- Manifest；
- Publisher；
- Protocol Version；
- Runtime Type；
- Status。

### AINAInstallation

- User ID；
- AINA ID；
- Installed Version；
- Granted Permissions；
- Configuration；
- Status。

### Invocation

- Invocation ID；
- Caller；
- Target Type；
- Target ID；
- Input；
- Output；
- Status；
- Duration；
- Token Usage；
- Cost；
- Trace ID。

### Task

- Task ID；
- Invocation ID；
- Status；
- Progress；
- Result；
- Created At；
- Completed At。

### AuthorizationGrant

- Grant ID；
- User ID；
- AINA ID；
- Scope；
- Expire Time；
- Revoked At。

---

## 10. 非功能需求

### 10.1 性能

初期建议指标：

- 普通会话请求首 Token 延迟 P95 不高于 3 秒；
- 能力检索延迟 P95 不高于 500 毫秒；
- Tool 路由开销 P95 不高于 300 毫秒；
- 平台内部 Protocol 转发开销 P95 不高于 200 毫秒；
- 支持流式响应；
- 长任务不得阻塞会话服务线程。

以上指标不包含外部模型和第三方 AINA 自身执行时间。

### 10.2 可用性

- 核心 Agent 服务目标可用性不低于 99.9%；
- 单个 AINA 故障不得影响其他 AINA；
- 单个 Tool 故障不得导致会话服务整体不可用；
- 支持熔断、限流和降级；
- 支持失败重试和任务恢复。

### 10.3 扩展性

系统应支持：

- 水平扩容；
- 多模型适配；
- 多租户；
- 多区域部署；
- 新 Protocol 版本；
- 新消息类型；
- 新 UI 组件；
- 新权限类型。

### 10.4 可观测性

每次 Agent 调用应生成统一 Trace。

链路至少包含：

- 用户请求；
- 意图识别；
- 能力检索；
- 模型调用；
- Skill 执行；
- Tool 调用；
- AINA 调用；
- 用户确认；
- 最终响应。

平台应提供：

- 日志；
- 指标；
- Trace；
- Token 统计；
- 成本统计；
- 错误率；
- 延迟分布；
- AINA 健康状态；
- Tool 成功率。

### 10.5 可审计性

审计日志应记录：

- 操作主体；
- 操作时间；
- 操作对象；
- 请求参数摘要；
- 权限判断；
- 用户确认；
- 执行结果；
- 数据访问范围；
- Trace ID。

安全审计日志不得由普通开发者修改或删除。

---

## 11. 管理后台需求

管理后台至少包含以下模块：

1. 用户与租户管理；
2. Agent 配置；
3. 模型配置；
4. Skill 管理；
5. Tool 管理；
6. AINA 管理；
7. AINA 审核；
8. 权限策略；
9. 运行监控；
10. 调用日志；
11. 审计日志；
12. 配额和限流；
13. 应用下架和应急控制。

---

## 12. 开发者中心需求

开发者中心应提供：

- AINA 创建向导；
- Manifest 编辑器；
- JSON Schema 校验；
- Protocol 调试器；
- 请求模拟器；
- 流式事件查看器；
- Tool 调试；
- 权限检查；
- 测试用户；
- 测试会话；
- 日志查询；
- 版本发布；
- 灰度配置；
- 文档和 SDK。

建议提供以下 SDK：

- TypeScript SDK；
- Python SDK；
- Java SDK；
- Go SDK。

SDK 不是 Protocol 的必要条件，AINA 仍可直接实现标准接口。

---

## 13. AINA 用户界面扩展

AINA 可选择只返回对话内容，也可以声明 UI 能力。

首期建议支持受控的声明式 UI，而不是允许 AINA 注入任意 JavaScript。

支持的组件可包括：

- Text；
- Markdown；
- Image；
- Button；
- Form；
- Input；
- Select；
- Table；
- List；
- Card；
- Progress；
- File；
- Confirmation；
- Error。

UI 事件应转换为标准 Protocol Event，由 Agent 框架转发给 AINA。

平台必须：

- 对 UI Schema 进行校验；
- 限制可执行行为；
- 禁止任意脚本；
- 统一处理权限确认；
- 统一适配 Web 和移动端。

---

## 14. 能力选择与冲突处理

当多个 Skill 或 AINA 均可处理同一请求时，系统应综合考虑：

- 用户显式指定；
- 用户已安装状态；
- 能力描述匹配度；
- 历史使用偏好；
- 权限是否满足；
- 当前可用性；
- 执行成本；
- 响应延迟；
- 数据安全等级；
- 管理员策略；
- 用户评分。

系统不得仅依据应用名称选择能力。

对于高风险任务，即使路由结果置信度较高，也必须执行权限和用户确认流程。

---

## 15. MVP 范围建议

### 15.1 MVP 必须实现

1. 基础多轮对话；
2. 会话管理；
3. Tool 注册和调用；
4. 基础 Skill 定义；
5. AINA Manifest；
6. 远程 AINA Runtime；
7. AINA 注册、安装和卸载；
8. Agent 自动发现和调用 AINA；
9. 同步调用；
10. 流式文本输出；
11. 标准错误协议；
12. 用户权限授权；
13. 高风险操作确认；
14. 调用日志和 Trace；
15. 基础管理后台；
16. TypeScript 或 Python SDK。

### 15.2 MVP 暂不实现

1. 托管 Runtime；
2. AINA 商业市场；
3. 自动计费结算；
4. AINA 间自由调用；
5. 复杂声明式 UI；
6. 跨会话长期任务；
7. 多 Agent 自治协作；
8. 自动化收益分成；
9. 完整移动端 SDK。

---

## 16. 验收标准

### AC-001 基础对话

用户能够创建会话并完成连续多轮对话，系统能够正确保存和恢复上下文。

### AC-002 Tool 调用

开发者注册符合规范的 Tool 后，Agent 能够根据用户意图调用 Tool，并将结果返回给用户。

### AC-003 AINA 注册

开发者提交符合 AINA Protocol 的 Manifest 和 Endpoint 后，平台能够完成校验并生成 AINA 注册记录。

### AC-004 AINA 安装

用户能够查看 AINA 权限并完成安装，未安装或未授权的 AINA 不得访问用户数据。

### AC-005 AINA 调用

用户显式指定 AINA 时，Agent 能够将标准请求发送给 AINA，并正确处理其响应。

### AC-006 自动路由

用户未指定 AINA 时，Agent 能够依据能力描述选择合适的已安装 AINA。

### AC-007 权限控制

AINA 请求未授权权限时，平台必须拒绝调用或请求用户授权。

### AC-008 高风险确认

涉及删除、发送、支付或发布等高风险操作时，平台必须在执行前获得用户确认。

### AC-009 故障隔离

某个 AINA 超时或返回异常时，不得导致 Agent 主服务不可用。

### AC-010 调用追踪

管理员和开发者能够依据 Trace ID 查询一次请求经过的模型、Skill、Tool 和 AINA 调用链路。

### AC-011 版本兼容

AINA 使用不受支持的 Protocol 版本时，平台能够拒绝注册或返回明确的兼容性错误。

### AC-012 数据隔离

一个 AINA 不得读取其他 AINA 的私有数据，除非存在显式授权和标准接口。

---

## 17. 主要风险

### 17.1 概念边界风险

Skill 与 AINA 如果定义不清晰，可能出现：

- 同一能力重复注册；
- 路由系统无法正确选择；
- 生命周期和权限模型混乱。

应将 Skill 定位为“能力”，AINA 定位为“应用”。

### 17.2 Protocol 过度设计风险

首期 Protocol 如果包含过多能力，将显著增加开发者接入成本。

应优先稳定：

- Manifest；
- Describe；
- Invoke；
- Stream；
- Error；
- Permission。

其他能力通过后续版本增加。

### 17.3 第三方应用安全风险

第三方 AINA 可能导致：

- 数据泄露；
- 权限滥用；
- Prompt Injection；
- 恶意 Tool 调用；
- 供应链攻击。

必须建立权限、审核、隔离、签名和审计机制。

### 17.4 自动路由不可控风险

模型可能选择错误的 AINA，或者向 AINA 传递错误参数。

需要：

- Schema 强校验；
- 路由置信度；
- 用户显式选择优先；
- 高风险操作二次确认；
- 调用前策略检查。

### 17.5 长任务可靠性风险

AINA 可能执行耗时较长的任务。

后续需引入：

- Task ID；
- 状态机；
- 进度事件；
- 断点恢复；
- 取消机制；
- 回调或事件通知。

---

## 18. 待决策事项

以下问题需要在产品和架构评审阶段明确：

1. Skill 是否允许用户直接创建，还是仅允许开发者创建。
2. AINA 是否必须安装后才能由 Agent 调用。
3. 系统内置 AINA 是否绕过安装流程。
4. AINA 是否允许直接调用其他 AINA。
5. AINA 是否可以声明和注册新的 Tool。
6. Tool 由平台执行还是由 AINA 自行执行。
7. AINA 的状态数据由平台托管还是由应用自行管理。
8. 是否支持匿名 AINA。
9. 是否允许未审核 AINA 在私人范围内运行。
10. 首期是否支持托管 Runtime。
11. 是否允许 AINA 自定义模型。
12. 用户是否可以限制 Agent 自动调用某个 AINA。
13. AINA 是否拥有独立的会话空间。
14. Skill 是否需要独立版本管理。
15. Protocol 的底层传输采用 HTTP、SSE、WebSocket，还是多种方式并存。
16. UI Protocol 是否纳入 AINA Protocol 1.0。
17. AINA 是否支持定时任务和事件触发。
18. AINA 的调用成本由平台、开发者还是用户承担。
19. 企业租户是否允许维护私有 AINA Registry。
20. 是否允许用户导出 AINA 配置和数据。

---

## 19. 推荐的首期架构边界

建议将系统拆分为以下逻辑模块：

- **Conversation Service**：会话和消息管理；
- **Agent Runtime**：模型调用、推理和编排；
- **Capability Registry**：Skill、Tool、AINA 注册与发现；
- **AINA Gateway**：Protocol 校验、认证和请求转发；
- **Tool Runtime**：Tool 调用与副作用控制；
- **Permission Service**：授权和策略判断；
- **Task Service**：异步任务管理；
- **Context Service**：上下文和记忆管理；
- **Observability Service**：日志、指标和 Trace；
- **Developer Portal**：应用创建、调试和发布；
- **Admin Console**：审核、安全和平台治理。

Agent Runtime 不应直接依赖某个具体 AINA，而应只依赖 Capability Registry 和 AINA Gateway。

---

## 20. 总结

天舟 Agent 框架的核心不是简单增加插件机制，而是建立一个统一的 AI 原生能力运行平台。

三个核心对象应保持清晰边界：

- **Tool 是原子执行能力；**
- **Skill 是任务完成能力；**
- **AINA 是面向完整用户场景的应用。**

AINA Protocol 是框架长期可扩展性的关键。首期应优先解决能力描述、标准调用、流式响应、权限、安全、版本和可观测性问题，避免过早将商业市场、复杂 UI 和多 Agent 协作纳入核心协议。
