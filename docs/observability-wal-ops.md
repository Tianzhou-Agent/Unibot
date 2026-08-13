# 可观测性可靠存储（OTel + WAL）运维手册

对应设计文档 `.docs/unibot-observability/ir-01-opentelemetry-wal-reliable-storage-design.md`。

## 1. 架构概览

```text
AgentRuntime / LLM / Tool
  -> ObservabilityAspect（OTel Span + 旧 ID 映射 + Trace Barrier）
  -> DurableWalSpanProcessor（span -> 不可变 ObsRecord）
  -> WalWriter（有界队列、批量 fsync、Segment 轮转、flush_through barrier）
  -> NAS WAL Segment -> ObsIngestWorker（幂等批量 UPSERT）
  -> unibot_obs_traces / unibot_obs_spans / unibot_obs_events（独立连接池）
  -> ObsQueryService -> /obs 与 /admin/obs 接口
完整模型/工具 IO（脱敏后 gzip）-> NAS raw/<tenant>/<user>/<trace>/<span>.json.gz
```

## 2. 配置

**MySQL 版本要求：≥ 8.0**（与 docker-compose `mysql:8.0` 一致；SQLAlchemy 2.0.50 会自动按服务器版本选择 UPSERT 语法，5.7 与 8.0.19- 回退 `VALUES()` 写法，8.0.20+ 使用 row alias，两种均兼容；不支持更早的 5.x）。

| 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| `UNIBOT_OBS_ENABLED` | `true` | 是否启用 OBS 管道（WAL/入库/查询） |
| `UNIBOT_OBS_WAL_ROOT` | `data/nas/observability/wal` | WAL 根目录（必须是持久卷） |
| `UNIBOT_OBS_WAL_MAX_BYTES` | 4 GiB | WAL 总空间告警上限 |
| `UNIBOT_OBS_RAW_ROOT` | `data/nas/observability/raw` | 原始 IO 根目录 |
| `UNIBOT_OBS_RETENTION_DAYS` | 90 | OBS 明细保留期（数据治理确认） |
| `TZ_STORAGE_MYSQL_DSN` | 默认本机 | OBS 表使用同一 DSN，独立连接池（pool_size=2, max_overflow=2） |

批量大小、轮转大小、刷新间隔为代码内常量（32 MiB / 30 s / 256 条），压测证明需要调整时再配置化。

## 3. WAL 目录结构

```text
<UNIBOT_OBS_WAL_ROOT>/
  <node_id>-<process_id>-<startup_uuid>/      # producer_instance_id
    000000000001.active                         # 本进程追加中的段
    000000000002.sealed                         # 已轮转，等待入库
    000000000003.corrupt                        # 校验失败被隔离，保留排查
    000000000004.ingesting                      # 崩溃残留，启动时恢复为 sealed
```

每个进程只写自己的 `.active` 文件；`.ingesting` 由原子 rename 认领，多实例不会重放同一段；数据库 UPSERT 幂等是最终正确性保障。

孤儿 `.active` 认领：扫描任务会 seal 并重放"非本实例目录、超过 60 秒未写入且双重 stat 确认未增长"的 `.active` 段（崩溃遗留）。注意：**多实例共享存储 + 极低流量**时，长时间空闲的活段理论上仍可能被其他实例认领（原实例 fd 继续写已 rename 的文件）；单实例部署受 `is_own_dir` 保护不受影响，多实例部署请保持轮转间隔内的写入频率或提高 `orphan_active_min_age_seconds`。

## 4. 启动 / 关闭顺序

启动（lifespan）：

1. 验证 NAS 根目录存在并可写
2. 创建 OBS 专用表
3. 初始化本实例 WAL 目录与 Active Segment（含恢复扫描）
4. 启动 WalWriter
5. 启动 ObsIngestWorker（后台扫描 sealed 段与孤儿段）
6. 初始化 OTel TracerProvider 与 DurableWalSpanProcessor
7. 开始接受请求（历史积压后台重放，不阻塞启动）

关闭：

1. 停止接收新 OBS 记录（WalWriter close）
2. 内存队列全部追加 + `fsync` + seal 当前段
3. IngestWorker 最后清扫 sealed 段
4. 关闭 OBS 独立连接池（最后）
5. 再关闭业务连接池

## 5. 故障处理

| 故障 | 行为 | 数据结果 |
| --- | --- | --- |
| MySQL 暂时不可用 | WAL 持续积累，扫描任务指数重试 | 已 fsync 数据不丢 |
| Backend 崩溃 | 重启扫描、校验并重放 WAL | 完整 Frame 恢复 |
| WAL 尾部半写 | 截断到最后有效 Frame | 最后半条不生效 |
| WAL 中部损坏 | 隔离为 `.corrupt` 并告警 | 不静默跳过 |
| MySQL 提交后崩溃 | 重放同一段 | UPSERT 不重复 |
| 原始 IO `.tmp` 残留 | 定期清理 | 不产生无效引用 |
| 队列满 | 短暂等待 -> 受控同步 append | 顺序保持单调 |
| NAS+MySQL 同时不可用 | 记录 `obs_telemetry_gap_count` 并告警 | 无法保证完整，需运维介入 |

空间水位：70% 告警、85% 提高重试频率、95% 尝试直写 MySQL、100% 记录缺口并保持业务优先。

## 6. 健康状态（`GET /health`）

```text
obs.enabled
obs.obs_queue_depth / obs_queue_capacity
obs.obs_wal_bytes / obs_wal_segment_count / obs_wal_oldest_age_seconds
obs.obs_wal_fsync_duration_ms
obs.obs_corrupt_segment_count / obs_wal_append_failure_count
obs.ingest.obs_ingest_last_success_at / obs_ingest_retry_count / obs_ingest_failure_count
obs.ingest.obs_segments_pending / obs_telemetry_gap_count
```

`obs_ingest_last_success_at` 持续过期 = MySQL 入库停滞，检查连接池与 MySQL 状态；
`obs_segments_pending` 持续增长 = 积压，检查磁盘与入库速度；
`obs_telemetry_gap_count` 增长 = 出现过双存储故障窗口，检查数据缺口。

## 7. 运维检查项

- NAS WAL 路径必须是持久卷（非容器临时文件系统），支持同文件系统原子 rename
- 定期检查 WAL 总大小、最老 Segment、最后入库成功时间
- 定期演练：MySQL 停止、Backend 强制退出、WAL 重放
- 定期清理孤儿 `.tmp` 与过期原始 IO（按保留策略 + 数据库引用状态）
- 反馈关联数据应用单独保留策略
- 数据库备份必须覆盖 OBS 明细表；WAL 只用于短期可靠传输，不替代长期备份

## 8. 迁移阶段状态（当前实现）

| 阶段 | 状态 | 说明 |
| --- | --- | --- |
| 一 基础设施 | ✅ 已完成 | WAL/表/UPSERT/RawIO + 单元与幂等测试 |
| 二 双写对账 | ✅ 已完成 | OTel+WAL 新链路与旧写入并存 |
| 三 查询切换 | ✅ 已完成 | /obs、admin、反馈上下文走新表，旧接口保留 |
| 四 停止旧写入 | ✅ 已完成 | Trace/LLMCall 不再经通用 Repository 持久化/启动加载 |
| 五 清理 | ✅ 已完成 | 健康状态、本文档、全量回归 |

回滚开关：`PersistentRepository(persist_observability=True)` 可临时恢复旧 Trace/LLMCall 持久化；
旧表保留只读回滚观察期；新版本回滚到旧版本前必须先 seal 当前 WAL（正常关闭即可）。
