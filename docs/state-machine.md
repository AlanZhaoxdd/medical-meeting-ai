# 文档状态机

## 会议核验状态

```text
PENDING → IN_PROGRESS → CONFIRMED
             ↑              │
             └── 编辑 ──────┘
```

两类问题（`cut_point`、`open_ended`）均至少存在一条后才能确认。确认后的问题编辑会
回到 `IN_PROGRESS`；确认成功先将分析状态置为 `READY`，提交分析后变为 `QUEUED`，问题写入锁定。
提交接口幂等且不派发任务。

会议上传入口使用独立的轻量状态机：

```text
UPLOADED → PARSING → EXTRACTING_METADATA → READY_FOR_REVIEW → CONFIRMED
```

处理可进入 `FAILED` 或 `CANCELLED`。原件永久保留；Worker 使用租约、attempt token
与周期对账恢复中断任务。解析完成并生成可编辑草稿后，系统立即为该草稿修订启动
`build_chunks → embed_chunks` 的预向量化任务，但此时仍不会创建正式 Meeting、知识项
或进入发布流程。

上传/解析阶段的持久化边界是固定的：先写对象存储中的原件、不可变
`DocumentBlock`、`DRAFT TranscriptRevision`/`TranscriptRevisionBlock` 和导入元数据；
草稿就绪后才允许创建仅执行切块和向量写入的 RAG `IngestionJob`。预向量任务以
`revision_id + revision_version` 唯一标识，完成后停在 `embed_chunks`，不会提前创建
`KnowledgeItem` 或 `Meeting`。

`READY_FOR_REVIEW` 阶段只编辑独立的 DRAFT 修订。正文版本变化后，旧向量状态变为
`STALE`；客户端通过幂等的向量化接口确保最新修订进入 `PENDING/RUNNING/SYNCED`。
确认事务只接受“当前 revision 精确版本已 `SYNCED`”的请求，然后冻结修订、幂等创建
Meeting，并把 Document 的 active revision 指向确认稿。确认后的后续入库任务直接从
`extract_knowledge` 开始，复用已有 Chunk 和向量，避免重复切块与 embedding；切点和
开放性问题同时进入独立异步任务。派发失败不会回滚已经成功创建的 Meeting、确认修订
或 active revision；入库任务保持 `QUEUED` 并由周期对账器自动补投。修订写入与同一
Document 的切块/向量发布共享文档级 advisory lock，旧版本任务不会与正文编辑交错修改
Chunk 或向量记录。

主路径：

```text
UPLOADED → PARSING → PARSED → CHUNKING → EMBEDDING
→ EXTRACTING → AWAITING_REVIEW → IN_REVIEW → PUBLISHED
```

任一处理节点可进入 `FAILED`；原件保留。失败任务从最近安全节点重试。
`AWAITING_REVIEW`、`IN_REVIEW`、`PUBLISHED` 可按权限重新解析或索引。软删除进入
`DELETED`。

文档只保留 `status` 一个生命周期字段：审核与发布都是同一状态机的阶段，不再并行维护
独立的 `review_status` / `publication_status`；`vector_sync_status` 仅为内部同步
标记（用于发布门禁），不对外展示为用户可见状态。

LangGraph 节点：

1. `validate_source`
2. `parse_document`
3. `normalize_blocks`
4. `build_chunks`
5. `embed_chunks`
6. `extract_knowledge`
7. `validate_evidence`
8. `save_draft`
9. `review_gate`（PostgreSQL Checkpoint，人工暂停）
10. `publish_document`
11. `finalize`

发布门禁要求：

- 文档为 `AWAITING_REVIEW` 或 `IN_REVIEW`。
- 不存在 `PENDING` / `NEEDS_CHANGES` 知识项。
- 每个批准知识项至少有一个真实 Block/Chunk 引用和原文 quote。
- 发布前向量同步为 `SYNCED`。
- 当前角色至少具备 reviewer 权限。

发布后批准项与 Chunk 进入 `PUBLISHED`；被拒绝项不进入正式检索。Milvus 更新通过 PostgreSQL Outbox 幂等完成。
