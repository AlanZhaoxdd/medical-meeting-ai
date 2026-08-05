# API

所有业务接口使用 `/api/v1`。错误响应包含稳定的 `error_code`（同时保留兼容字段 `code`）、可读 `message`、`details` 和 `request_id`。

## 认证

- `POST /auth/register`
- `POST /auth/login`
- `POST /auth/refresh`
- `POST /auth/logout`
- `GET /auth/me`

Access Token 为短期 JWT；Refresh Token 只以 SHA-256 摘要保存，刷新时轮换并撤销旧 Token。密码使用 Argon2。

## 知识库与模板

- `GET|POST /knowledge-bases`
- `GET|PATCH|DELETE /knowledge-bases/{kb_id}`
- `GET|POST /knowledge-bases/{kb_id}/templates`
- `GET /knowledge-bases/{kb_id}/templates/{template_id}`

`DELETE ...?purge=true` 仅允许 owner/admin。模板任务绑定 ID 与版本，后续更新不改变历史任务。

## 组织成员

- `GET|POST /organizations/current/members`
- `PATCH|DELETE /organizations/current/members/{user_id}`

owner/admin 可管理成员；只有 owner 可变更 owner 角色，且 owner 不能被直接移除。

## 文档与任务

- `POST|GET /knowledge-bases/{kb_id}/documents`
- `GET|DELETE /knowledge-bases/{kb_id}/documents/{document_id}`
- `POST .../{document_id}/retry`
- `POST .../{document_id}/reindex`
- `GET .../{document_id}/blocks`
- `GET .../{document_id}/chunks`
- `GET /jobs/{job_id}`
- `WS /ws/jobs/{job_id}?token={access_token}`

上传使用 `multipart/form-data`：`file`、可选 `meeting_id`、可选 `template_id`、`force_new_version`。成功返回 `202`、`document` 和 `job_id`；重复哈希返回现有文档并标记 `duplicate=true`。

## 会议导入

- `GET /meeting-imports/config`
- `POST /meeting-imports`
- `GET /meeting-imports/{import_id}`
- `POST /meeting-imports/{import_id}/retry`
- `POST /meeting-imports/{import_id}/cancel`
- `GET /meeting-imports/{import_id}/review`
- `GET /meeting-imports/{import_id}/revisions/{revision_id}`
- `PATCH /meeting-imports/{import_id}/revisions/{revision_id}`
- `POST /meeting-imports/{import_id}/find`
- `POST /meeting-imports/{import_id}/replace`
- `POST /meeting-imports/{import_id}/replace/{operation_id}/undo`
- `PATCH /meeting-imports/{import_id}/metadata`
- `GET /meeting-imports/{import_id}/vectorization`
- `POST /meeting-imports/{import_id}/vectorize`
- `POST /meeting-imports/{import_id}/confirm`

会议导入保持单文件模式。创建请求使用 `multipart/form-data`，包含
`knowledge_base_id` 以及 `file` 或 `document_id`；重复 SHA 返回 `409` 和可关联的
`existing_document_id`。接口仅允许 owner/admin/editor 创建、重试或取消，并按当前
JWT 组织限定知识库、Document 和导入任务。该流程先保存不可变原件、原始
`DocumentBlock`、`DRAFT TranscriptRevision`/`TranscriptRevisionBlock` 与预览元数据；
到达 `READY_FOR_REVIEW` 后立即为当前草稿启动只执行切块和向量写入的后台任务，但仍不
创建正式 Meeting 或 KnowledgeItem。

校对接口将不可变 `document_blocks` 复制为 `DRAFT transcript_revision`。正文 PATCH、
替换与撤销均携带 `expected_version`；版本过期返回 `409`，不会覆盖服务器内容。
全文替换在单个数据库事务内执行并返回可整批撤销的 `operation_id`。元数据保存使用
独立版本号，并保留 AI 建议、置信度、真实来源和用户修改标记。

标准会议纪要的第一页会议信息表提取会议名称、会议目的、讨论题目、会议日期、顾问
选择标准、参会顾问姓名、内部参会人及原因和记录人。第三列政策指引不进入结构化字段或
纪要正文；正文从“具体讨论内容”等分界标题之后开始。确认后，会议名称写入
`Meeting.title`，其余字段写入 `Meeting.meeting_info` 并由会议读取接口返回。
DOCX 使用 python-docx 保留合并单元格结构，同时继续使用 Docling 解析正文与其他文档
类型；表格的 Docling 子段落不会重复进入正文。

`GET .../review` 的 `vectorization` 返回任务、节点、进度、错误、当前修订版本和已同步
版本；轮询时使用轻量的 `GET .../vectorization`，不会重复传输整份纪要。`POST
.../vectorize` 携带 `expected_version`，幂等确保该精确草稿版本已排队；版本过期返回
`409`。正文修改后旧向量显示为 `STALE`。持久化为 `QUEUED` 但因 Broker 短暂故障未成功
派发的入库任务会由周期对账器自动补投。

`POST .../confirm` 必须携带 `Idempotency-Key`，并且当前草稿的精确版本必须已经
`SYNCED`。确认会冻结当前修订、创建并返回唯一 Meeting、更新 Document 的 active
revision，并创建一次从 `extract_knowledge` 开始的后续入库任务，复用预先生成的 Chunk
和向量。重复确认返回同一 Meeting；任务派发失败不会回滚 Meeting、确认修订或 active
revision，而会保留带派发诊断的 `QUEUED` 状态，交由周期对账器自动补投。知识抽取按有界批次执行；模型单批输出仍触及长度
限制时返回稳定错误码 `llm_completion_limit`，不再退化为 `ingestion_unexpected_error`。

## 审核、发布与检索

### 会议核验（3.0）

带 access token 的会议接口按 token 组织隔离；核验接口拒绝 `organization_id` 为空或
不匹配的会议。未携带 token 时保留旧版会议接口的全局兼容行为。

- `GET /meetings/{meeting_id}/verification`
- `POST /meetings/{meeting_id}/questions`
- `PATCH /meetings/{meeting_id}/questions/{question_id}`
- `DELETE /meetings/{meeting_id}/questions/{question_id}?expected_version={n}`
- `POST /meetings/{meeting_id}/verification/confirm`
- `POST /meetings/{meeting_id}/analysis-submissions`

GET 返回 `meeting`、按 `cut_point_questions`/`open_ended_questions` 分组的问题、
`verification_version` 及 `eligibility`。每类问题至少一条才可确认。问题内容会去除首尾
空白，同会议同类型不允许重复；手工问题固定 `source=manual`、`confidence=null`。
Editor 及以上可写，viewer/reviewer 只读。PATCH/DELETE/确认/分析提交携带
`expected_version`，冲突返回 409。确认后编辑会将状态退回 `in_progress` 和分析状态
`not_ready`；分析提交后状态为 `confirmed`/`queued` 并锁定核验内容。分析提交是幂等的，
只更新会议状态，不创建任务或调用模型。

- `GET /knowledge-bases/{kb_id}/knowledge-items`
- `PATCH /knowledge-bases/{kb_id}/knowledge-items/{item_id}`
- `POST /knowledge-bases/{kb_id}/knowledge-items/{item_id}/review`
- `POST /knowledge-bases/{kb_id}/documents/{document_id}/publish`
- `POST /knowledge-bases/{kb_id}/search`

viewer 只能看到已发布知识。`include_drafts=true` 由后端再次校验角色。检索不生成 LLM 答案，返回 Dense、Sparse、RRF 融合、Rerank 分数及页码/幻灯片/发言时间证据定位。

交互式请求/响应 Schema 以运行时 `/docs` 为准。
