# 可观测性可靠存储（OTel + Redis Streams）运维手册

文件名为兼容已有链接而保留。当前链路不再写入自研文件 WAL；旧目录仅在升级后作为一次性迁移源读取。

## 1. 架构

```text
AgentRuntime / LLM / Tool
  -> ObservabilityAspect + OpenTelemetry
  -> RedisObsBuffer（有界内存队列、XADD、WAITAOF、flush_through）
  -> Redis Stream / Consumer Group
  -> RedisObsIngestWorker（MySQL 提交后 XACK + XDEL）
  -> unibot_obs_traces / spans / events
  -> /obs 与 /admin/obs

脱敏后的完整模型/工具 IO -> NAS raw/<tenant>/<user>/<trace>/<span>.json.gz
旧 WAL 文件 -> 兼容读取器 -> MySQL（迁移完成后不再产生新文件）
```

交付语义是至少一次：Redis 消息可能因进程在 MySQL 提交后、确认前退出而重放，OBS 表以
`record_id` 和版本条件 UPSERT 保证幂等。

## 2. 基础设施要求

- MySQL 8.0+。
- Redis 7.2+，必须开启 AOF；默认 compose 固定在 BSD-3-Clause 的 Redis 7.2 系列，使用
  `appendonly yes`、`appendfsync everysec`，并挂载 `redis-data` 持久卷。
- Redis 淘汰策略必须为 `noeviction`。不要给 OBS 专用实例配置会淘汰 Stream 的内存策略。
- 单实例默认等待本机 AOF 确认。生产 Redis 有副本时，设置 `UNIBOT_OBS_REDIS_WAIT_REPLICAS`，使
  `WAITAOF` 同时等待指定数量的副本落盘。

应用启动会检查 Redis 版本和 AOF 状态，不满足可靠性约束时直接失败，不会伪装成可靠缓冲。

## 3. 配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `UNIBOT_OBS_ENABLED` | `true` | 启用采集、缓冲、入库和查询 |
| `TZ_STORAGE_OBS_REDIS_DSN` | 空 | OBS 专用 Redis；为空时复用 `TZ_STORAGE_REDIS_DSN` |
| `UNIBOT_OBS_REDIS_STREAM_KEY` | `unibot:obs:records:v1` | 主 Stream |
| `UNIBOT_OBS_REDIS_GROUP` | `unibot-obs-mysql-v1` | MySQL 投影消费者组 |
| `UNIBOT_OBS_REDIS_DLQ_KEY` | `unibot:obs:records:dlq:v1` | 无法解析记录的死信 Stream |
| `UNIBOT_OBS_REDIS_PRODUCERS_KEY` | `unibot:obs:producers:v1` | 生产者心跳 ZSET |
| `UNIBOT_OBS_REDIS_WAIT_REPLICAS` | `0` | 要求完成 AOF 的 Redis 副本数 |
| `UNIBOT_OBS_REDIS_DURABILITY_TIMEOUT_MS` | `10000` | `WAITAOF` 超时 |
| `UNIBOT_OBS_REDIS_CLAIM_IDLE_MS` | `60000` | 接管其他消费者未确认消息的空闲阈值 |
| `UNIBOT_OBS_REDIS_PRODUCER_STALE_SECONDS` | `120` | 判定异常退出生产者的心跳阈值 |
| `UNIBOT_OBS_RAW_ROOT` | `data/nas/observability/raw` | 原始 IO 目录 |
| `UNIBOT_OBS_RETENTION_DAYS` | `90` | OBS 明细与原始 IO 保留期 |
| `UNIBOT_OBS_WAL_ROOT` | `data/nas/observability/wal` | 仅用于读取升级前遗留 WAL |

同一套 MySQL 投影只能使用一个消费者组名；修改组名会从头建立另一套消费进度，依赖 UPSERT 幂等，
但会造成额外重放负载。

## 4. 启停与确认顺序

启动顺序：创建 OBS 表和回填数据，验证 Redis/AOF，创建 Consumer Group，启动 Redis producer 和
consumer；如果旧 WAL 根目录存在，再启动只读迁移 worker。

正常请求结束前，终态记录会等待 `XADD + WAITAOF` 完成，但不等待 MySQL。关闭时先停止新增记录并
刷新 producer，再停止 consumer，最后关闭 OBS MySQL 连接池。

## 5. 故障语义

| 故障 | 行为 | 数据结果 |
| --- | --- | --- |
| MySQL 暂时不可用 | 消息留在 Pending Entries List，恢复后由 `XAUTOCLAIM` 重试 | 已进 Redis 的数据不丢 |
| Backend/consumer 崩溃 | 其他实例接管超过 idle 阈值的 pending 消息 | 至少一次、MySQL 幂等 |
| Redis 短暂不可用 | producer 保留当前批次并重试；终态 barrier 超时后走 MySQL 直写兜底 | 业务优先，健康指标记录失败 |
| 记录无法解析 | 先把原文和原因 `WAITAOF` 到 DLQ，再确认并删除源消息 | 不静默丢弃 |
| Redis AOF 关闭或版本过低 | 应用启动失败并给出明确错误 | 不降级成不可靠模式 |
| MySQL 提交后、XACK 前退出 | 消息重放 | UPSERT 不重复 |

## 6. 健康检查

`GET /health` 的 `obs` 节点包含：

- `obs_queue_depth`、`obs_buffer_sequence_no`、`obs_buffer_durable_through`
- `obs_stream_publish_failure_count`、`obs_stream_durability_failure_count`
- `ingest.obs_stream_length`、`obs_stream_pending`、`obs_stream_consumer_lag`
- `ingest.obs_ingest_last_success_at`、`obs_ingest_retry_count`、`obs_ingest_failure_count`
- `ingest.obs_stream_reclaimed_records`、`obs_stream_invalid_records`、`obs_stream_dlq_records`

持续增长的 pending/lag 表示 MySQL 投影停滞；publish 或 durability failure 表示 Redis/AOF 不可用；
DLQ 增长需要检查版本兼容或损坏数据。

常用检查：

```bash
redis-cli INFO persistence
redis-cli XINFO GROUPS unibot:obs:records:v1
redis-cli XPENDING unibot:obs:records:v1 unibot-obs-mysql-v1
redis-cli XLEN unibot:obs:records:dlq:v1
```

## 7. 升级与回滚

升级前不要删除 `UNIBOT_OBS_WAL_ROOT`。新版本发现该目录时会继续读取 `.sealed`、崩溃遗留的
`.ingesting`，以及达到安全年龄的孤儿 `.active` 文件；成功写入 MySQL 后按旧规则回收。确认目录无
待处理 segment 后可以归档目录。新链路不会再创建 WAL 文件。

回滚到仍写文件 WAL 的旧版本前，先正常关闭新版本，并确保 Redis Stream 的 pending 和 lag 均为 0；
否则旧版本无法消费仍留在 Redis 中的记录。
