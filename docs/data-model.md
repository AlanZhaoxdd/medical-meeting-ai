# PostgreSQL 数据结构

PostgreSQL 是唯一权威数据库。

| 领域 | 表 |
|---|---|
| 身份与权限 | `users`, `organizations`, `organization_memberships`, `refresh_tokens` |
| 项目与模板 | `knowledge_bases`, `extraction_templates`, `extraction_template_versions` |
| 文档 | `documents`, `meeting_imports`, `document_blocks`, `transcript_revisions`, `transcript_revision_blocks`, `batch_replace_operations`, `chunks` |
| 知识与审核 | `knowledge_items`, `review_events` |
| 编排与一致性 | `ingestion_jobs`, `node_executions`, LangGraph `checkpoint_*` 表 |
| 审计与检索 | `audit_events`, `retrieval_logs`, `outbox_events` |
| 既有会议 | `meetings`（新增可空 `knowledge_base_id` 外键） |
| 会议核验 | `meeting_questions` |

结构化扩展字段、来源引用和定位信息使用 JSONB，但租户边界、外键、状态、版本和常用过滤字段保持类型化列并建立索引。

关键约束：

- 所有 KB 资源均保存 `organization_id` 与 `knowledge_base_id`；会议新增可空
  `organization_id` 外键用于租户隔离。历史会议由知识库回填；仅在组织表恰好一行时，
  才将无知识库会议安全回填到该组织，否则保持 NULL。
- 文档版本不可覆盖；`previous_version_id` 形成版本链。
- Block 的 `(document_id, block_id/order)`、Chunk 的 `(document_id, chunk_index)` 唯一。
- 原始 `document_blocks` 永不由校对接口更新；草稿和确认稿保存在独立修订与修订 Block 表。
- `meetings.meeting_info` 保存确认后的会议目的、讨论题目、原始会议日期、顾问选择标准、顾问姓名、内部参会人与原因及记录人；会议名称继续使用 `meetings.title`。
- `meetings.verification_status`、`verification_version`、确认人/时间和 `analysis_requested_at`
  记录核验状态与乐观锁版本。`meeting_questions` 软删除，按会议、类型和 trim 后内容
  建立 PostgreSQL partial unique index。
- 修订使用 `version` 做乐观并发控制；批量替换保存整批快照和操作 ID，支持事务撤销。
- `documents.active_transcript_revision_id` 只指向确认稿；`meeting_imports.meeting_id` 保证重复确认返回同一正式会议。
- 模板 `(template_id, version)` 唯一，任务冻结实际模板版本。
- Outbox 和 Graph 节点均有唯一幂等键。
- MinIO 仅保存原件；Milvus 仅保存向量与过滤字段。
