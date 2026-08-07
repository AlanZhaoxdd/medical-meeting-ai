# 医药会议智能分析平台 · 权威技术参考文档

> 版本：v1.0（对应当前代码库 HEAD `43f85d4`）  
> 适用岗位：AI 应用开发 / RAG 工程 / 后端 AI 应用  
> 用途：本项目**唯一**技术参考文档，覆盖总体架构、每个模块的技术栈、设计决策、底层编排与面试高频问题；后续未开发功能以「演进规划」章节预留占位。

---

## 目录

0. [文档说明与使用约定](#0-文档说明与使用约定)
1. [项目定位与产品目标](#1-项目定位与产品目标)
2. [总体架构](#2-总体架构)
3. [技术栈总览与选型理由](#3-技术栈总览与选型理由)
4. [后端分层与代码组织](#4-后端分层与代码组织)
5. [认证与权限体系](#5-认证与权限体系)
6. [会议管理模块](#6-会议管理模块)
7. [会议导入与纪要约校模块](#7-会议导入与纪要约校模块)
8. [文档解析模块](#8-文档解析模块)
9. [语义切块模块](#9-语义切块模块)
10. [向量化与模型服务模块](#10-向量化与模型服务模块)
11. [向量存储与混合检索模块](#11-向量存储与混合检索模块)
12. [权威数据层设计（PostgreSQL）](#12-权威数据层设计postgresql)
13. [LangGraph 入库编排](#13-langgraph-入库编排)
14. [事务性 Outbox 与幂等发布](#14-事务性-outbox-与幂等发布)
15. [异步任务可靠性设计](#15-异步任务可靠性设计)
16. [结构化知识提取模块](#16-结构化知识提取模块)
17. [会议核验与问题生成模块](#17-会议核验与问题生成模块)
18. [检索 API 与审计](#18-检索-api-与审计)
19. [评测与基准模块](#19-评测与基准模块)
20. [可观测性](#20-可观测性)
21. [前端架构](#21-前端架构)
22. [部署与运维](#22-部署与运维)
23. [测试与质量保障](#23-测试与质量保障)
24. [关键设计决策与权衡（面试问答）](#24-关键设计决策与权衡面试问答)
25. [各模块高频面试问题清单](#25-各模块高频面试问题清单)
26. [演进规划与预留占位（Roadmap）](#26-演进规划与预留占位roadmap)
27. [代码地图（关键文件索引）](#27-代码地图关键文件索引)
28. [附录 A：环境变量速查](#28-附录-a环境变量速查)
29. [附录 B：API 一览](#29-附录-bapi-一览)
30. [附录 C：状态机速查](#30-附录-c状态机速查)
31. [附录 D：核心数据表速查](#31-附录-d核心数据表速查)

---

## 0. 文档说明与使用约定

- 本文档是项目唯一技术参考。所有数据以代码库为准；若发现代码与文档不一致，以代码为准并更新本文档。
- 标注「预留 / Roadmap」的章节代表功能尚未开发或仅完成占位，面试时建议明确区分「已实现」与「规划中」。
- 每个模块统一按：**技术栈 → 设计 → 底层编排 → 面试点** 的格式展开，便于直接背诵与追问。
- 项目当前处于第一阶段（v0.1.0）：**医药会议资料的可审核知识入库与证据检索工作台**。

---

## 1. 项目定位与产品目标

### 1.1 要解决的问题

医药企业召开大量学术会议，每次会议产生 PDF / PPT / Word 纪要等原始材料。这些材料包含重要医学观点、临床数据、共识与行动项，但存在三个痛点：

1. **非结构化**：观点散落在不同页面、段落、表格中，靠人工整理慢且易错；
2. **不可溯源**：后续检索「某个观点是哪次会议、哪一页、谁说的」非常困难；
3. **缺乏审核闭环**：AI 直接产出内容有幻觉与合规风险，医药行业要求可审计、可追溯、人工把关。

### 1.2 产品定位

面向医药会议的**可审核知识库（KB）+ 证据检索（RAG）工作台**：

- 把原始会议材料变成「结构化知识项 + 可溯源证据」；
- 所有入库内容必须经过**证据校验 + 人工审核**后才能发布；
- 发布后的内容支持**混合检索（dense + sparse + RRF + rerank）**，结果可一路追溯到原文、页码、发言人与时间轴。

### 1.3 当前阶段与里程碑

| 阶段 | 内容 | 状态 |
|---|---|---|
| 第一阶段（当前） | 知识入库流水线、纪要约校、证据检索、问题生成核验、评测基准 | 已实现 |
| 第二阶段 | 最终问答生成、会议资料分析中心 | 占位（`AnalysisPlaceholderView`、`submit_analysis` 中的 TODO） |
| 第三阶段 | 实时 ASR / 音频流、PPT/图表/结构化纪要生成、GraphRAG | 未开发 |
| 长期 | 任意自定义 Schema/Prompt 编辑器、组织公共 KB、跨 KB 检索 | 未开发 |

---

## 2. 总体架构

### 2.1 架构总览

```text
┌──────────────────────────────────────────────────────────────────┐
│                          前端 Vue 3 + TS                          │
│          Element Plus / Pinia / Vue Router / axios / WebSocket     │
└───────────────┬──────────────────────────────┬───────────────────┘
                │ HTTP /api/v1 (JWT)           │ WS /ws/jobs/{id}  进度
┌───────────────▼──────────────────────────────▼───────────────────┐
│                        API 服务 (FastAPI)                         │
│   认证 / 会议 / 导入 / 文档 / 知识项 / 检索 / 评测 / 组织 / 任务     │
└───────┬───────────────┬──────────────────┬──────────────┬────────┘
        │               │                  │              │
┌───────▼──────┐ ┌──────▼───────┐ ┌───────▼────────┐ ┌────▼──────────┐
│ PostgreSQL   │ │ MinIO        │ │ Milvus 2.6     │ │ Redis         │
│ 唯一权威事实  │ │ 原始文件      │ │ 向量+过滤字段   │ │ Celery broker │
│ + Outbox     │ │ 对象存储      │ │ (etcd + MinIO) │ │ / result /    │
│ + LangGraph  │ │              │ │                │ │ 进度 Stream   │
│  checkpoint  │ │              │ │                │ │               │
└───────▲──────┘ └──────▲───────┘ └───────▲────────┘ └────▲──────────┘
        │               │                  │               │
┌───────┴────────────────┴──────────────────┴───────────────┴─────────┐
│               Worker + Beat (Celery, 异步编排层)                       │
│  解析 / 切块 / 向量化 / LangGraph 入库图 / 问题生成图 / Outbox 对账      │
└──────────────┬───────────────────────────────┬──────────────────────┘
               │                               │
     ┌─────────▼─────────┐            ┌────────▼─────────┐
     │ 模型服务 bge-models│            │ LLM (OpenAI 兼容)│
     │ BGE-M3 / Reranker │            │ DeepSeek 等      │
     │ FastAPI :8100     │            │ 结构化输出        │
     └───────────────────┘            └──────────────────┘
```

### 2.2 四条核心设计原则

1. **PostgreSQL 是唯一权威，其他存储都是缓存/索引**
   - MinIO 只存原始文件；Milvus 只存向量与过滤字段；正文永远从 PostgreSQL 回填，并在回填时再次校验组织、知识库、发布状态与最新版本。
   - 即使向量库被清空，也能从 PostgreSQL 重建，绝不把业务事实放在非权威存储中。
2. **每一步幂等、可重试**
   - 上传、切块、向量化、发布、AI 任务全部带唯一幂等键与版本号；Celery Beat 周期性对账补投。
3. **AI 永远在人的审核闭环里**
   - AI 只产出候选；知识项必须证据校验 + 人工审核才能发布；检索结果可溯源到原文片段。
4. **模型与业务解耦**
   - BGE 模型单独成服务，API / Worker 不加载大模型；LLM 只通过 OpenAI 兼容接口调用，不绑定具体厂商。

### 2.3 部署拓扑（docker-compose）

服务清单（`docker-compose.yml`）：

| 服务 | 镜像/版本 | 端口 | 职责 |
|---|---|---|---|
| `postgres` | postgres:16.9-alpine | 5432 | 唯一权威数据库 |
| `redis` | redis:7.4.5-alpine（AOF 开启） | 6379 | Celery broker/result + 进度 Stream |
| `minio` + `minio-init` | minio RELEASE.2025-07-23 + mc | 9000/9001 | 原始文件对象存储；init 负责建桶并设为私有 |
| `etcd` | quay.io/coreos/etcd:v3.5.18 | 2379 | Milvus 元数据 |
| `milvus` | milvusdb/milvus:v2.6.0 standalone | 19530/9091 | 向量检索 |
| `api` | backend 镜像（uvicorn） | 8000 | FastAPI 应用 |
| `worker` | backend 镜像（celery worker） | - | 异步任务 |
| `beat` | backend 镜像（celery beat） | - | 周期对账调度 |
| `frontend` | node 构建 → nginx:1.28-alpine | 5173→80 | SPA 静态托管 + 反向代理 |
| `bge-models` | model-service 镜像（docker-compose.models.yml / .gpu.yml） | 8100 | BGE-M3 嵌入 + 重排推理 |

GPU 模式通过 `docker compose -f docker-compose.yml -f docker-compose.models.yml -f docker-compose.gpu.yml up` 启动：`docker-compose.models.yml` 提供模型服务公共定义（CPU 默认），`docker-compose.gpu.yml` overlay 将 `BGE_DEVICE=cuda`、申请 1 张 NVIDIA GPU，并用 CUDA 版 torch 重新构建模型服务镜像（`TORCH_INDEX_URL` 默认 cu126，可用环境变量覆盖）。

### 2.4 端到端主流程（一句话）

```text
保存原件 → 解析 → 标准化 Block → 语义切块 → Dense/Sparse 向量化
→ 结构化知识提取 → 证据校验 → 人工审核 → 发布 → 混合检索
```

---

## 3. 技术栈总览与选型理由

### 3.1 总览表

| 层 | 技术 | 版本约束 | 职责 |
|---|---|---|---|
| 前端框架 | Vue 3 + TypeScript | vue ^3.5.13, ts ~5.7 | SPA |
| UI 组件 | Element Plus + @element-plus/icons-vue | ^2.9.7 | 后台管理 UI |
| 状态/路由 | Pinia + Vue Router | ^3.0.1 / ^4.5.0 | 全局状态、路由守卫 |
| 构建/请求 | Vite + axios + dayjs | ^6.2.2 / ^1.8.4 | 开发服务器、HTTP、日期 |
| 后端框架 | FastAPI + Uvicorn | >=0.115 / >=0.32 | REST API、WebSocket |
| ORM/迁移 | SQLAlchemy 2.0 (async) + Alembic | >=2.0.36 / >=1.14 | 数据访问、11 个迁移 |
| 数据库 | PostgreSQL 16 | postgres:16.9-alpine | 唯一权威事实 |
| 异步任务 | Celery + Redis | >=5.4 / >=5.2 | 长任务、周期对账 |
| 对象存储 | MinIO | >=7.2 (SDK) | 原始文件 |
| 向量库 | Milvus 2.6 + etcd | pymilvus>=2.5 | 向量检索（HNSW + 稀疏） |
| 文档解析 | Docling + python-docx | docling>=2.48 | PDF/DOCX/PPTX 解析 |
| 向量模型 | BGE-M3 + BGE-reranker-v2-m3（FlagEmbedding） | 1.3.5 | 稠密/稀疏嵌入、重排 |
| LLM 编排 | LangChain + LangGraph | langchain-core/openai>=0.3; langgraph>=0.4 | 结构化输出、状态图、人工中断 |
| 检查点 | langgraph-checkpoint-postgres | >=2.0 | 图执行持久化/恢复 |
| 安全 | PyJWT + Argon2 | pyjwt>=2.10 / argon2-cffi | JWT、密码哈希 |
| 可观测 | Langfuse | >=4.0 | LLM 链路追踪 |
| 评测 | 自研脚本（backend/scripts） | - | 黄金集、Recall/MRR、延迟/吞吐 |
| 测试 | pytest + pytest-asyncio + testcontainers；Vitest | - | 后端/前端单测 |

### 3.2 选型理由（面试常问）

- **PostgreSQL 而非全量向量库**：业务事实（会议、用户、审核、任务、版本）需要强一致事务与约束；向量只是"派生索引"，可从正文重建。
- **Milvus 而非 FAISS/pgvector**：需要多向量字段（dense + sparse）混合检索、标量过滤、主键 upsert、独立水平扩展；Milvus standalone 在 Docker 内即可提供生产级能力。
- **BGE-M3 单独成服务**：模型加载耗内存（CPU 上加载 2 个模型约数 GB），若打进 API 进程会拖垮请求吞吐；独立服务可用 batch、GPU/CPU 切换、横向扩展，Worker 通过 HTTP 调用。
- **BGE-M3 而非纯 dense embedding**：M3 同时产出 dense + sparse（词法权重）向量，天然支持混合检索，对中文医药专有名词更鲁棒；并支持长文本（8192 token）。
- **LangGraph 而非自写流水线**：内置 checkpoint（PostgreSQL 持久化）、`interrupt` 人工闸门、条件路由与状态回放，天然支持"失败从安全节点重试"与"人工审核暂停/恢复"。
- **Redis Stream 而非普通 Pub/Sub 做进度**：Stream 可持久化、可追尾（`xread` last-id）、可设 maxlen，适合断线重连后补发进度。
- **Celery Beat 对账**：消息队列投递不是 100% 可靠，周期对账保证最终一致（Outbox 事件、QUEUED 任务、过期租约）。

---

## 4. 后端分层与代码组织

### 4.1 分层结构

```text
backend/app
├── api/            HTTP 层：路由、请求/响应、鉴权依赖、WebSocket
│   └── v1/         auth / meetings / meeting_imports / meeting_verification /
│                   question_generation / knowledge_bases / documents /
│                   knowledge_items / search / jobs / organizations / benchmarks
├── core/           配置(Settings)、安全(security/auth)、异常体系
├── db/             SQLAlchemy 引擎与 Session
├── models/         ORM 模型（kb.py、meeting.py）
├── schemas/        Pydantic 请求/响应模型与枚举
├── repositories/   Repository 层（meeting）
├── services/       业务服务：meeting、meeting_review、question_generation、
│                   vector_store、model_client、storage、benchmark、observability…
├── ingestion/      纯函数：chunking、state（状态机）、validation（上传校验）
├── worker/         Celery 任务、LangGraph 图（graph.py、question_graph.py）、
│                   parser、extraction、meeting_import、progress、celery_app
└── main.py         FastAPI 组装（异常处理器、request_id 中间件）
```

### 4.2 分层设计要点

- **API 薄、服务厚**：路由只做参数校验、鉴权、调用 Service；业务规则在 Service 层；纯算法（切块、RRF）放在可单测的纯函数模块。
- **Repository 模式（会议）**：`MeetingRepository` 封装查询条件，Service 组合业务规则，方便替换数据源与测试。
- **依赖注入**：`SessionDependency = Annotated[AsyncSession, Depends(get_session)]`；认证依赖 `CurrentUserDependency` 产出 `AuthContext`（user_id、organization_id、role、token_version）。
- **统一异常体系**：`AppException` 携带 `status_code / code / message / details`；全局异常处理器统一包装为 `{code, error_code, message, details, request_id}`。
- **统一中间件**：为每个请求生成/透传 `x-request-id`，贯穿日志与错误响应，方便排障。
- **配置**：`pydantic-settings` 读取 `backend/.env` 与仓库根 `.env`；`get_settings()` 带 `lru_cache`；敏感变量（JWT_SECRET_KEY、MINIO_ROOT_PASSWORD）在 compose 中 `:?` 强制校验。

### 4.3 请求-响应约定

- 业务接口统一前缀 `/api/v1`。
- 上传接口返回 202（异步处理）；错误码稳定（如 `meeting_import_not_found`、`vectorization_required`），前端据此做分支。
- 分页统一 `page / page_size`；时间筛选强制带时区。

---

## 5. 认证与权限体系

**文件**：`app/core/security.py`、`app/core/auth.py`、`app/api/v1/auth.py`

### 5.1 技术栈

- JWT（PyJWT，HS256，`JWT_SECRET_KEY` 强制 ≥32 字符）
- Argon2 密码哈希（argon2-cffi）
- HTTPBearer 依赖 + 每次请求查库校验

### 5.2 令牌设计

| 令牌 | 有效期 | 内容 | 存储 |
|---|---|---|---|
| Access Token | 15 分钟（`ACCESS_TOKEN_MINUTES`） | `sub`(user_id)、`org`(organization_id)、`ver`(token_version)、`type=access`、`jti` | 前端 localStorage（仅会话态） |
| Refresh Token | 30 天（`REFRESH_TOKEN_DAYS`） | 随机 48 字节 URL-safe 字符串 | 数据库只存 **SHA-256 摘要** |

- 登录/注册签发双令牌；刷新时**轮换**：旧 refresh 立即撤销（`revoked_at`），签发新对，防重放。
- `token_version`（用户级版本号）写入 access token，改密/登出可整体吊销：每次鉴权比对库中 `users.token_version` 与 JWT `ver`，不一致即 401。
- `jti` 唯一标识单条 token，便于审计追踪。

### 5.3 鉴权实现（每次请求都查库）

`_authenticate()` 每次用 `user_id + organization_id` 联查 `users JOIN organization_memberships`，要求：

- 用户 `status == active`；
- 成员关系 `status == active` 且属于 token 中的组织；
- `user.token_version == payload["ver"]`。

这保证"禁用用户 / 移除组织成员 / 改密"立即生效（无状态 JWT + 有状态校验的组合）。

### 5.4 RBAC 角色模型

```text
viewer(10) < reviewer(20) < editor(30) < admin(40) < owner(50)
```

- `require_role(Role.EDITOR)` 依赖工厂按 `ROLE_LEVEL` 比较，产出 `ForbiddenError`。
- `require_kb_access()`：知识库必须属于当前组织且未删除，否则 403（不泄露存在性）。
- 关键门禁：editor 才能上传/编辑/确认；reviewer 才能审核/发布；owner/admin 才能 `purge=true` 彻底删除；viewer 不能检索暂存内容。
- 租户隔离：所有查询都带 `organization_id` 条件（服务层与仓储层强制执行，非仅前端隐藏）。

### 5.5 面试点

- 为什么 access 短、refresh 长？降低泄露窗口；refresh 走数据库摘要 + 轮换 + 撤销，兼顾可用与安全。
- 为什么每次请求查库而不是纯无状态 JWT？因为需要实时感知禁用/成员移除/版本吊销，代价是可接受的一次索引查询。
- 密码为何用 Argon2？抗 GPU 暴力破解，比 bcrypt 更强的现代默认选择。

---

## 6. 会议管理模块

**文件**：`app/models/meeting.py`、`app/api/v1/meetings.py`、`app/services/meeting.py`、`app/repositories/meeting.py`

### 6.1 状态机

会议业务状态 `MeetingStatus`：

```text
draft → published → in_progress → completed → archived
   │         │            │
   └─────────┴──→ cancelled（终态）
```

`AnalysisStatus`（分析状态）：`not_ready → ready → queued → processing → succeeded/failed/cancelled`；取消会议时若分析在 queued/processing 自动置为 cancelled。

### 6.2 设计要点

- CRUD 全部软删除（`deleted_at`）；终态（cancelled/archived）不允许编辑。
- `meeting_info` JSONB 保存确认后的会议信封（会议目的、讨论题目、原始会议日期、顾问选择标准、顾问姓名、内部参会人及原因、记录人）。
- 会议与知识库通过可空 `knowledge_base_id` 关联；历史会议在组织表恰好一行时安全回填 `organization_id`，否则保持 NULL（迁移兼容策略）。
- 列表查询支持：状态、分析状态、关键字（标题/主办方 ilike）、时间范围、分页。

### 6.3 面试点

- 为什么状态用显式状态机而非自由字段？非法流转在服务层统一拦截（`_ALLOWED_TRANSITIONS`），避免到处散落 if 判断。
- 软删除 vs 物理删除：保留审计与引用完整性，`purge=true` 才物理清理。

---

## 7. 会议导入与纪要约校模块

**文件**：`app/api/v1/meeting_imports.py`（1751 行，API 层最重）、`app/worker/meeting_import.py`、`app/services/meeting_review.py`

这是目前业务最复杂的模块，面试重点。

### 7.1 轻量状态机（导入侧）

```text
UPLOADED → PARSING → EXTRACTING_METADATA → READY_FOR_REVIEW → CONFIRMED
              │ 失败/取消
              └──→ FAILED / CANCELLED
```

与文档主状态机（UPLOADED→…→PUBLISHED）**刻意分离**：会议导入在确定性元数据提取后即止，不提前创建 Meeting / 知识项 / 进入发布流程。

### 7.2 上传与去重

- `multipart/form-data`：`file` 或 `document_id`（关联已有文档）+ `knowledge_base_id` + `confirm_duplicate` + `associate_existing`。
- **SHA-256 去重**：同文件重复上传返回 409 并带 `existing_document_id`；前端引导"关联已有文档继续"或"查看已有任务"。
- 上传配置（扩展名白名单、50MB 上限）由 `/meeting-imports/config` 统一下发，前端与后端双重校验；MIME 与扩展名必须匹配（如 `.pdf → application/pdf`）。
- 持久化边界固定：先写对象存储原件 → 不可变 `DocumentBlock` → `DRAFT TranscriptRevision` / `TranscriptRevisionBlock` → 导入元数据，同一事务提交。

### 7.3 确定性元数据提取（无 LLM）

`extract_deterministic_metadata()` 纯规则实现，保证可测试、可复现：

- 从**第一页会议信息表**按「标签→值列」规则提取：会议名称、会议目的、讨论题目、会议日期、顾问选择标准、参会顾问姓名、内部参会人及原因、记录人；
- 通过归一化别名表（`_MEETING_INFO_ALIASES`，含"诺和诺德内部参会人及参会原因"等变体）匹配标签；
- **第三列"政策指引"不进入结构化字段也不进入纪要正文**；
- 正文起点：跳过"具体讨论内容"等分界标题（`_find_transcript_start`）；
- 每个字段带 `_source`（Block 定位）与 `_confidence_label`（高置信度 / 建议确认 / 无法可靠识别），供人工校对参考；
- 兼容纯文本 `标签：值` 格式的旧导入。

### 7.4 修订与校对（Transcript Review）

- 原始 `DocumentBlock` **永不被校对接口修改**；校对在 DRAFT 修订副本上进行。
- 修订 `version` 做乐观并发控制：正文 PATCH、查找替换、撤销、元数据保存全部要求 `expected_version`，过期返回 409。
- 全文/块级查找替换：单事务执行，返回 `operation_id`，保存**整批快照**支持事务撤销（`undo_replace`）。
- 元数据使用**独立版本号**（与正文修订分离），保留 AI 建议、置信度、真实来源与用户修改标记。
- 表格保留：DOCX 用 python-docx 无损读取合并单元格（`_docx_table_rows`），渲染为规范 Markdown 表格。

### 7.5 预向量化（vector_only 模式）

- 草稿就绪（READY_FOR_REVIEW）后，同事务创建 revision 级 RAG 任务：`job_id = meeting-vector-{import_id}-{revision_id}-v{version}`，`mode=vector_only`；
- 该任务只走 `build_chunks → embed_chunks`，**不创建 KnowledgeItem / Meeting / 不进入发布**；
- 以 `revision_id + revision_version` 唯一标识；正文版本更新后旧向量状态变 `STALE`，客户端通过幂等 `/vectorize` 接口重新排队；
- 确认事务只接受"当前 revision 精确版本已 SYNCED"的请求（`vector_sync_status == SYNCED`）。

### 7.6 确认（Confirm）事务

`POST /meeting-imports/{import_id}/confirm`（幂等）：

1. 幂等键（`Idempotency-Key` 或 `meeting-import-confirm:{id}`）唯一且不能跨导入复用；
2. 校验 `expected_version`（修订）+ `expected_metadata_version`（元数据）+ 完整会议信封（标题、时间、两类问题等由后续核验模块把关）；
3. 校验向量已 SYNCED；
4. 创建 `Meeting`（title 写入 `meetings.title`，其余写 `meeting_info`）→ 修订冻结为 CONFIRMED → `documents.active_transcript_revision_id` 指向确认稿 → `chunks.meeting_id` 回填；
5. 创建入库任务（从 `extract_knowledge` 开始，复用已有 Chunk/向量，**不重复切块与 embedding**）+ `AiTask(QUESTION_GENERATION)` + Outbox 事件（`question_generation.requested`）；
6. 提交后派发；**派发失败不回滚**已创建的 Meeting / 确认修订 / active revision，任务保持 QUEUED 由对账器补投。

### 7.7 文档级 Advisory Lock

`document_lock_key(document_id)` 用 SHA-256 前 8 字节转**有符号 int64**（与 PostgreSQL `pg_try_advisory_lock(bigint)` 匹配）。修订写入与同一文档的切块/向量发布共享该锁，杜绝"旧版本任务与正文编辑交错修改 Chunk/向量"的竞态。

### 7.8 面试点

- 为什么会议导入的元数据提取不用 LLM？确定性优先：可单测、零成本、结果稳定；复杂内容留给人工校对环节。
- 为什么确认失败不整体回滚？因为幂等确认的边界是"确认动作本身"；一旦事务提交，业务事实已成立，异步派发失败只是时序问题，靠重试解决。
- 为什么拆成独立修订表而不是直接改 block？不可变原件是审计根基，任何编辑都可追溯、可撤销。

---

## 8. 文档解析模块

**文件**：`app/worker/parser.py`

### 8.1 技术栈

- Docling（DocumentConverter）解析 PDF / DOCX / PPTX，输出结构化文档树；
- python-docx 无损读取 Word 表格（合并单元格、多行单元格），弥补 Docling Markdown 展开合并单元格的问题；
- 确定性解析器：TXT/Markdown 逐行解析（维护 `#` 标题层级 path）、逐字稿 JSON（`segments` 数组：text/speaker/start_ms/end_ms）。

### 8.2 Block 统一模型

所有解析器输出统一 `Block` 字典：`block_id / block_type / order / heading_path / text / table_markdown / page_number / slide_number / speaker / start_ms / end_ms / bbox / content_hash`。

- `block_type`：heading / paragraph / table / list / speech；
- `heading_path` 保留层级标题，供切块与检索上下文；
- 逐字稿块带 `speaker + start_ms + end_ms`，支撑时间轴溯源；
- 每个块有 `content_hash`（SHA-256），用于幂等与变更检测。

### 8.3 文本清洗（parser artefacts）

- 统一换行（CRLF→LF）、去 `<br>`、去 HTML 注释、去不可见 Unicode（零宽字符/BOM）、NBSP 归一化；
- 表格 Markdown 规范化（去冗余分隔符、保留表头行）；
- Docling 的表格子段落不重复进入正文（`table_descendant_level` 跳过）。

### 8.4 执行位置与错误处理

- 解析在 **Worker** 中执行（不阻塞 API）；阻塞式 Docling 调用用 `anyio.to_thread.run_sync` 转线程池；
- 解析失败映射为稳定错误码（`invalid_transcript_json`、`invalid_text_encoding` 等），文档进入 FAILED 并可重试；原件永久保留。

### 8.5 面试点

- Docling 与 pdfplumber/PyMuPDF 的区别：Docling 输出带布局/层级/表格结构的文档模型（DoclingDocument），可直接迭代 items 与 prov（页码、bbox），更适合知识库结构化入库。
- 为什么 PPTX 的 slide_number 用 page_no？Docling prov 的页码语义即幻灯片序号。

---

## 9. 语义切块模块

**文件**：`app/ingestion/chunking.py`（纯函数，无 IO）

### 9.1 版本与参数

- `CHUNKER_VERSION = "semantic-v3"`，写入每个 Chunk，支撑切块器升级后的重索引决策；
- 参数：`CHUNK_TARGET_TOKENS=700`、`CHUNK_MAX_TOKENS=1000`、`CHUNK_OVERLAP_TOKENS=100`、`CHUNK_SIMILARITY_THRESHOLD=0.65`。

### 9.2 Token 估算

`estimate_tokens()`：中文按**字符数**计（`[\u4e00-\u9fff]`），英文/数字按单词与符号计数。不调用 tokenizer，纯规则、确定性、零依赖。

### 9.3 语义单元（semantic units）

`prepare_semantic_units()` 先把 Block 拆成稳定的最小单元：

- 超长文本块用**二分查找**定位 max_tokens 边界，再在最近的中文/英文句末标点（`。！？!?；;` 与换行）处切断，避免从中间截断句子；
- 超长表格：保留表头 + 按行分组分片（`_split_table`）；
- 单元是后续 embedding 的最小粒度，且**边界在两种策略（single_pass_pool / two_pass）下一致**。

### 9.4 切块算法（build_chunks）

按序扫描语义单元，四类边界触发 flush：

1. **结构边界**：heading / table 起始、heading_path 变化、speaker 切换；
2. **语义边界**：相邻单元 dense 向量余弦相似度 < 0.65（且当前组已超过 min_semantic_tokens ≈ 350）；
3. **token 预算**：超 max_tokens 时 flush 并保留 overlap（尾部队列带回）；
4. **表格独立成块**，overlap 不跨越表格。

overlap 策略：flush 时从尾部逆序挑选不超过 overlap_tokens 的**同段落、同 heading、同 speaker** 单元作为下块开头，且不把 heading/table 拖入 overlap。

### 9.5 Chunk 唯一性与稳定性

```text
chunk_id = uuid5(NAMESPACE_URL, f"{document_id}:{index}:{sha256(content)}")
```

同一文档、同一内容重试时 chunk_id 与 chunk_index **完全稳定**，支撑幂等写入（先删后插）与检索结果的确定性。

### 9.6 溯源信息

每个 Chunk 携带 `source_block_ids` 与 `source_locator`：`{block_ids, page_number/slide_number/speaker, time_range:{start_ms,end_ms}}`，检索结果可一路回跳原文。

### 9.7 面试点

- 为什么不用固定窗口滑窗？医药纪要里语义单元是"段落/主题/发言"，固定窗口会切断事实与证据的完整性；结构 + 语义双边界更贴近人读逻辑。
- overlap 会不会重复计数？overlap 只用于保持上下文连续，证据引用以 chunk 为单位，重复内容通过 source_block_ids 可解释。
- 为什么 token 估算不用真实 tokenizer？入库时 batch 调用 embedding 服务前需要快速分桶，纯规则可并行、可单测；误差由 max_tokens 裕量吸收。

---

## 10. 向量化与模型服务模块

**文件**：`model-service/app.py`、`app/services/model_client.py`、`app/worker/graph.py`（_build_chunks/_embed_chunks/pool_*）

### 10.1 模型服务（独立容器）

- 加载 `BAAI/bge-m3`（BGEM3FlagModel，dense 1024 维 + sparse 词法权重）与 `BAAI/bge-reranker-v2-m3`（FlagReranker）；
- API：`POST /v1/embeddings`（texts 1~128 条，`include_sparse`）、`POST /v1/rerank`（query + documents + top_k）、`GET /health`；
- 关键工程细节：
  - **单个 `asyncio.Lock` 串行化推理**（torch 推理本身是线程安全的，但 CPU/显存带宽竞争下串行吞吐更稳）；
  - 阻塞推理放 `anyio.to_thread.run_sync`；
  - 默认启动即加载（`lazy_load` 可选）；`BGE_MAX_INPUT_CHARACTERS=32000` 上限，超长返回 413；
  - CPU 镜像锁 torch CPU wheel（uv lock）；GPU overlay 用 `--reinstall-package torch --upgrade` 强制换 CUDA wheel（`TORCH_INDEX_URL` 指向 cu126 镜像）。
- 推理超时 300s（`MODEL_SERVICE_TIMEOUT_SECONDS`），适配大表 batch 的合法长耗时。

### 10.2 嵌入策略：single_pass_pool vs two_pass

| 策略 | 流程 | 优点 | 代价 |
|---|---|---|---|
| `two_pass`（旧） | 单元先算 dense 用于找边界 → 最终 chunk 再算一次 dense+sparse 入库 | 边界向量精确 | 两次完整编码，慢 ~2x |
| `single_pass_pool`（默认） | 语义单元只算一次 dense+sparse；chunk 向量 = 单元 dense **平均**（pooled_dense）+ 单元 sparse **权值并集求和**（pooled_sparse） | 快一倍、chunk 边界一致、省 token | chunk 向量是池化近似 |

- `embedding_identity = f"{embedding_version}@{model}:{strategy}"`，写入 Chunk 表与 Milvus 每条记录；
- 策略/模型切换后**旧向量与新向量不混用**，已入库文档需重索引（向量库版本隔离的工程实现）。

### 10.3 批量写入

- embedding 按 `BGE_BATCH_SIZE`（默认 8，上限 128）分批；
- 向量写入 Milvus 前**先 `delete_document` 再批量 upsert**，`upsert_records_batched` 以 256 条/批，控制单次 RPC 载荷；
- `vector_sync_status`：PENDING → SYNCED，作为发布/确认门禁的内部技术标记（不对用户展示为生命周期状态）。

### 10.4 面试点

- BGE-M3 的 sparse 向量是什么？基于词法权重的稀疏向量（lexical weights），近似 BM25 效果但与 dense 同模型产出，无需额外 BM25 索引。
- 为什么池化而不是直接嵌入 chunk？单次编码 + 平均池化把成本砍半，且边界与 two_pass 完全一致；质量损耗通过 rerank 环节补偿。
- 为什么把模型服务独立？加载 ~几 GB 权重；与业务进程隔离后，API/Worker 可无 GPU 跑，模型服务单独扩缩容。

---

## 11. 向量存储与混合检索模块

**文件**：`app/services/vector_store.py`、`app/api/v1/search.py`

### 11.1 Collection 设计（Milvus）

Collection：`medical_kb_records`，**动态字段关闭**（schema 严格）：

| 字段 | 类型 | 说明 |
|---|---|---|
| record_id | VARCHAR(100) PK | chunk_id |
| organization_id / knowledge_base_id / meeting_id / document_id | VARCHAR(36) | 租户与过滤 |
| document_version | INT64 | 版本过滤/校验 |
| publication_status | VARCHAR(32) | PUBLISHED / DRAFT |
| content_type | VARCHAR(32) | 检索过滤 |
| dense_vector | FLOAT_VECTOR(1024) | HNSW, COSINE, M=32, efConstruction=200 |
| sparse_vector | SPARSE_FLOAT_VECTOR | SPARSE_INVERTED_INDEX, IP |
| embedding_version | VARCHAR(100) | 向量版本隔离 |

### 11.2 混合检索（Hybrid Search）

生产检索链路（`hybrid_search`）：

1. **dense 检索**：query 嵌入 dense，COSINE 度量，`ef=128`，取 50；
2. **sparse 检索**：query 稀疏向量，IP 度量，取 50；
3. **RRF 融合**：`reciprocal_rank_fusion(dense_ids, sparse_ids, k=60)`，`score = Σ 1/(k+rank)`，取 fusion_top_k=15；
4. **PG 权威回填**：按 chunk_id 回 PostgreSQL 联表，再次校验 org / kb / publication_status / 文档最新版本（`latest_versions` 子查询剔除旧版本文档），**Milvus 结果绝不直接当正文**；
5. **重排**：`bge-reranker-v2-m3` 对候选正文打分，取 `RERANK_TOP_K=5`；
6. 返回带 dense/sparse/fused/rerank 四套分数的结果，便于调参与评测对比。

### 11.3 过滤表达式

`_milvus_filter()` 拼装：`organization_id == ... and knowledge_base_id == ... and publication_status == "PUBLISHED"`（+ content_type/meeting_id/document_id 的 `in` 过滤）；字符串统一转义防注入。

### 11.4 面试点

- 为什么 RRF 而不是加权和？RRF 无需调权重、对尺度不敏感、鲁棒性好，是混合检索的事实标准。
- 为什么要有 PG 回填这步？向量库只是"候选生成器"：发布状态、版本、租户都可能已变化，权威校验必须发生在返回之前。
- 为什么检索结果记录在 `retrieval_logs`？审计 + 评测留痕；draft 检索额外写 audit（合规）。

---

## 12. 权威数据层设计（PostgreSQL）

**文件**：`app/models/kb.py`、`app/models/meeting.py`、`alembic/versions/*`（11 个迁移，20260727_0001 → 20260805_0011）

### 12.1 领域表分组

| 领域 | 表 |
|---|---|
| 身份与权限 | users、organizations、organization_memberships、refresh_tokens |
| 项目与模板 | knowledge_bases、extraction_templates、extraction_template_versions |
| 文档 | documents、meeting_imports、document_blocks、transcript_revisions、transcript_revision_blocks、batch_replace_operations、chunks |
| 知识与审核 | knowledge_items、review_events |
| 编排与一致性 | ingestion_jobs、node_executions、LangGraph checkpoint_* |
| 审计与检索 | audit_events、retrieval_logs、outbox_events |
| 会议与核验 | meetings、ai_tasks、meeting_questions、question_evidences |

### 12.2 类型化 vs JSONB 的边界

- **类型化列 + 索引**：租户边界（organization_id/knowledge_base_id）、外键、状态、版本、常用过滤字段（status、meeting_status、question_type、sha256）；
- **JSONB**：结构化扩展字段（meeting_info、metadata_json、structured_data）、来源引用与定位（source_refs、source_locator）、快照（batch_replace_operations.snapshots）。

### 12.3 关键约束（面试必背）

- 所有 KB 资源均带 `organization_id + knowledge_base_id`；会议 `organization_id` 可空用于历史兼容。
- 文档版本不可覆盖：`previous_version_id` 形成版本链；`(document_id, chunk_index)`、`(document_id, block_id/order)` 唯一。
- 原文 Block 永不被校对接口修改；校对只改 DRAFT 修订（`transcript_revision_blocks`）。
- 模板 `(template_id, version)` 唯一；任务**冻结实际模板版本**，模板更新不影响历史任务。
- 幂等键唯一：`outbox_events.idempotency_key`、`node_executions.idempotency_key`、`meeting_imports.confirmation_idempotency_key` 均 unique；
- `ai_tasks` 唯一约束 `(meeting_id, task_type, source_version)`：同一确认版纪要只生成一次问题任务；
- `meeting_questions` 部分唯一索引：`(meeting_id, question_type, lower(btrim(content))) WHERE deleted_at IS NULL` 防重复问题；
- 部分唯一索引实践：`uq_active_kb_org_name`（同一组织内活跃 KB 名称唯一）、`uq_active_meeting_import_document`（同一文档同时只有一个活跃导入）。

### 12.4 LangGraph Checkpoint 表

`langgraph-checkpoint-postgres` 的 `AsyncPostgresSaver` 将图执行状态（checkpoints、writes、blobs）持久化到 PostgreSQL，thread_id = job_id。**图状态只存 ID、状态与小结，不存正文**，避免 checkpoint 膨胀。

### 12.5 面试点

- 为什么 JSONB 不是万能？查询过滤、外键、约束、索引都需要类型化列；JSONB 只用于"扩展结构不参与复杂查询"的数据。
- 迁移如何保证历史数据兼容？Alembic 逐版本演进（如 0007 为历史会议回填 meeting_info、0011 合并 review/publication 为单一 status）。
- Outbox 与 node_executions 幂等键为什么必须 unique？因为"至少一次投递 + 幂等消费"才能同时保证不丢消息与不重复执行。

---

## 13. LangGraph 入库编排

**文件**：`app/worker/graph.py`（901 行，全项目最核心的编排代码）

### 13.1 状态定义

```python
class IngestionState(TypedDict, total=False):
    job_id: str
    document_id: str
    start_node: str        # 断点续跑入口
    input_version: str     # 幂等版本
    status: str
    summary: dict
    revision_id: str
    revision_version: int
    vector_only: bool
```

### 13.2 节点与进度

| 节点 | 进度 | 职责 |
|---|---|---|
| validate_source | 8 | 校验文档状态与来源 |
| parse_document | 20 | 拉取 MinIO 原件 → Docling/确定性解析 → 写 DocumentBlock |
| normalize_blocks | 32 | 校验 Block 非空 |
| build_chunks | 45 | 语义切块；single_pass_pool 时同时完成向量写入 |
| embed_chunks | 60 | 向量化（two_pass）或收尾；置 vector_sync_status=SYNCED |
| extract_knowledge | 75 | LLM 结构化知识提取 |
| validate_evidence | 84 | 校验每个知识项有真实 chunk/block 引用 + quote |
| save_draft | 90 | 置 AWAITING_REVIEW |
| review_gate | 92 | **interrupt 人工闸门** |
| publish_document | 97 | 校验已 PUBLISHED（由 API 完成实际发布） |
| finalize | 100 | 结束 |

### 13.3 底层编排细节

**a) 断点续跑**：`START` 条件路由到 `state["start_node"]`，失败任务从最近安全节点重入；已完成节点由 `NodeExecution` 幂等表跳过。

**b) 节点幂等**：

```text
idempotency_key = f"{job_id}:{node_name}:{input_version}"
```

`input_version` 是 `sha256(sha256 内容哈希 + template_id + template_version + embedding_version + chunker_version + chunker_config + revision_id + revision_version)`——任何输入变化都会产生新版本，重复投递不重复执行。

**c) 人工审核暂停/恢复**：`review_gate` 节点调用 `interrupt({"job_id":…, "status":"WAITING_REVIEW"})`，图执行被持久化挂起（PostgreSQL checkpoint）；审核通过后 API 触发 `resume_ingestion`，以 `Command(resume={"published": True})` 从断点恢复。

**d) 文档级互斥**：`pg_try_advisory_lock(document_lock_key)` 轮询获取（0.25s 间隔），获取后提交隐式事务（避免阻塞 checkpoint 的 CREATE INDEX CONCURRENTLY），结束时 `pg_advisory_unlock`；获取锁前后都复查"向量修订是否已被更新"，杜绝旧版本任务写脏数据。

**e) vector_only 短路**：`embed_chunks` 后条件路由到 END（不提取知识、不建 Meeting），用于草稿预向量化。

**f) 失败语义**：`_mark_failed` 将文档置 FAILED + 写错误码，Redis Stream 发 terminal 事件；`run_ingestion` 对 5xx/未知错误指数退避重试（`min(300, 2^(retries+1))`，最多 5 次），业务冲突（4xx）不重试。

### 13.4 面试点

- LangGraph interrupt 与普通"任务挂起"的区别：interrupt 依赖 checkpoint 保存完整执行状态，恢复时从挂起点精确继续，而不是整个重跑。
- 为什么图状态不存正文？checkpoint 只存 ID/状态/小结，避免每次执行状态膨胀拖慢序列化与 DB 写入。
- 为什么用 advisory lock 而不是 DB 行锁？跨会话、跨连接持锁（worker 长任务），行锁不适合"整个文档的向量化与编辑互斥"这种粗粒度场景。
- 幂等键为什么用 SHA-256 摘要而不是拼接原文？`node_executions.idempotency_key` 是 VARCHAR(255)，拼接 64 位内容哈希 + UUID 会超长；摘要保证定长且确定性。

---

## 14. 事务性 Outbox 与幂等发布

### 14.1 为什么需要 Outbox

「数据库状态变更」与「外部副作用（写 Milvus / 发任务）」天然不能同事务。直接发消息会出现：DB 提交成功但消息丢失 → 状态不一致。**事务性 Outbox**：业务变更与 Outbox 事件写入**同一事务**，后台对账器消费事件执行副作用。

### 14.2 事件表与消费

```text
outbox_events: idempotency_key(unique) / event_type / aggregate_id / payload / status / attempts
```

事件类型：

| 事件 | 触发点 | 消费动作 |
|---|---|---|
| vector.upsert_document | 向量化完成 | 直接置 PROCESSED（向量已写，仅为留痕） |
| vector.publish_document | 文档发布 | 重新嵌入所有 chunk，upsert `publication_status=PUBLISHED` |
| vector.delete_document | 文档删除 | Milvus delete_document |
| question_generation.requested | 确认会议 | 派发 run_question_generation |

消费由 `sync_outbox` 任务执行：**每 30 秒**（Celery Beat）取 PENDING/FAILED 事件，`FOR UPDATE SKIP_LOCKED` 防并发重复消费，`attempts+1`，失败置 FAILED 下次再试。

### 14.3 发布门禁（publish_document API）

1. 文档状态 ∈ {AWAITING_REVIEW, IN_REVIEW}；
2. 不存在 PENDING / NEEDS_CHANGES 知识项；
3. 每个批准知识项至少有真实 Block/Chunk 引用与原文 quote（`_validate_approved_evidence`）；
4. `vector_sync_status == SYNCED`；
5. 角色 ≥ reviewer；

通过后：文档 → PUBLISHED、chunks → PUBLISHED、批准知识项 → PUBLISHED，写入 Outbox `vector.publish_document`，立即派发 `sync_outbox` 与 `resume_ingestion`。

### 14.4 面试点

- Outbox vs CDC（Debezium）：Outbox 在应用层显式控制事件契约，不依赖 binlog 权限与解析，适合中小规模；CDC 适合大规模异构消费。
- 为什么不直接在同一请求里写 Milvus？发布接口要求快速响应且 Milvus 不可用不能阻塞发布；异步最终一致 + 幂等键保证至少一次。

---

## 15. 异步任务可靠性设计

**文件**：`app/worker/celery_app.py`、`app/worker/tasks.py`、`app/worker/meeting_import.py`

### 15.1 Celery 配置

```text
task_acks_late=True          # 任务执行完才 ack，崩溃不丢任务
task_reject_on_worker_lost=True
worker_prefetch_multiplier=1 # 每个 worker 一次只取一个任务，避免长任务堆积
task_serializer=json
```

Bat 调度（`beat_schedule`）：

| 任务 | 周期 | 作用 |
|---|---|---|
| sync_outbox | 30s | 消费 Outbox 事件 |
| reconcile_ingestion_jobs | 30s | 重投 QUEUED >60s 的入库任务（限 100） |
| reconcile_meeting_imports | 60s | 恢复过期租约/滞留导入 |

### 15.2 租约 + 心跳 + 对账（任务认领）

适用：会议导入、AI 问题生成任务（AiTask）。三者配合解决「worker 崩溃后任务卡死」：

1. **claim**：`claim_task` 用条件 UPDATE（status ∈ QUEUED/RETRYING 或 RUNNING 且租约过期）原子抢占，写入 `attempt_token`（随机 UUID）+ `lease_expires_at`；rowcount≠1 说明被他人持有；
2. **heartbeat**：执行期间每 60s（导入为 lease/3）续租，带 attempt_token 条件防旧 worker 续新租约；
3. **对账**：Beat 周期扫描租约过期/滞留任务，重置为可认领状态并重新派发。

导入侧同理：`_claim_import` + `_heartbeat_import` + `reconcile_stale_imports`；GET 接口也充当安全恢复点（过期即重置）。

### 15.3 派发失败容忍

所有"提交成功后派发"的调用都 try/except 吞掉：任务保持 QUEUED 持久化状态，对账器自动补投——**派发只是加速器，对账才是兜底**。

### 15.4 面试点

- 为什么 `acks_late + prefetch=1`？长任务场景下宁可让消息在队列里等，也不让 worker 死掉时丢任务。
- 为什么租约而非无限期锁？任何长期持锁的进程都可能崩溃，租约 + 心跳 + 对账把"进程死亡"转化为"租约过期"，最终一致。

---

## 16. 结构化知识提取模块

**文件**：`app/worker/extraction.py`、`app/services/model_client.py`（LLM 调用边界）

### 16.1 技术栈

- `langchain-openai.ChatOpenAI.with_structured_output(KnowledgeExtraction, method=..., include_raw=True)`；
- Pydantic Schema 驱动输出（9 种 item_type：meeting_metadata / participant / topic / insight / consensus / disagreement / evidence_claim / evidence_gap / action_item）；
- temperature=0、超时 60s、重试 2 次；
- Langfuse observation（`knowledge.extract`，generation 类型）。

### 16.2 Provider 兼容层（面试亮点）

- **DeepSeek 特殊处理**：LangChain 默认强制 tool_choice，而 DeepSeek V4 思考模式下拒绝该组合 → `extra_body={"thinking": {"type": "disabled"}}` 关闭思考，并改用 `json_mode`；
- **输出解析兜底**：`_parse_knowledge_extraction` 依次尝试 parsed → 去 ```json 围栏 → 提取首个 JSON 对象；`_raw_structured_value` 兼容 tool_calls/function arguments/多模态 content 数组；
- **长度错误映射**：识别 provider 的 context length / max tokens / finish_reason=length 错误 → 批次二分递归，最终映射为稳定的 413 `llm_completion_limit`，而不是笼统的 500。

### 16.3 批处理与合并

- 按 30k 字符上限分批（每块 ≥4k），每批最多 40 项；
- `_merge_extractions` 以 `(item_type, title, normalized_content)` 去重，引用取并集、置信度取 max——**确定性合并**，与模型输出顺序无关。

### 16.4 提取后的证据校验

`validate_evidence` 节点：每个知识项必须有 `source_refs`，且 chunk_id/block_id 真实存在、quote 非空。无效知识项 → 422 `knowledge_evidence_invalid`，阻止进入人工审核。

### 16.5 面试点

- 为什么强 schema 输出（structured output）而不是让 LLM 自由 JSON？字段级校验、引用完整性、后续版本比对都依赖稳定 schema。
- 为什么要 include_raw + 多重解析？不同厂商的 structured output 实现（function calling vs json mode）返回结构不同，兜底解析保证厂商可替换。
- temperature=0 是否保证确定性？不保证，但配合确定性合并 + 幂等重试，业务结果可复现、可审计。

---

## 17. 会议核验与问题生成模块

**文件**：`app/worker/question_graph.py`（836 行）、`app/services/question_generation.py`、`app/services/question_model_client.py`、`app/api/v1/meeting_verification.py`

### 17.1 核验状态机（会议侧）

```text
PENDING → IN_PROGRESS → CONFIRMED
             ↑              │
             └── 编辑 ──────┘
```

- 两类问题（`cut_point` 切点、`open_ended` 开放）**至少各一条**才能确认；
- 确认后编辑问题回到 IN_PROGRESS；确认成功先置分析状态 READY，提交分析后置 QUEUED 并锁定问题；
- 乐观锁：`verification_version` 每次编辑/确认递增，客户端提交 `expected_version`；
- `submit_analysis` **幂等且不派发任务**（当前为占位：TODO 3.1 才接入真正的分析任务）。

### 17.2 问题生成 LangGraph（QuestionGenerationState）

节点流：

```text
load_meeting_context(5)
→ build_retrieval_plan(15)
→ retrieve_cutpoint_docs + retrieve_open_docs（并行, 35）
→ rerank_cutpoint/open（并行, 50）
→ generate_cutpoints(65) + generate_open_questions(75)
→ merge(78)
→ validate_questions(85)
→ 条件路由：persist_questions(100) / refine_retrieval_plan（重试）/ mark_failed
```

### 17.3 关键设计

**a) 检索计划与 grounding 校验**：LLM 先生成 `RetrievalPlan`（医学实体、研究名、药品名、两类查询）；`validate_plan_grounding` 要求实体必须出现在会议上下文/确认纪要中（含 2 字中文锚点子串规则），防"检索计划编造会议中不存在的实体"。

**b) 双路证据源**：

- **权威知识库**：Milvus 候选 → PG 回填，要求 PUBLISHED + 最新版本；
- **确认版纪要自身**：单独的 DRAFT 检索路径，过滤到"该会议确认修订的 document_id"，并要求 import/revision/document/meeting/版本全部匹配，`source_type=confirmed_transcript`；
- 每次调模型前 **rehydrate**（重新从 PG 取正文并校验授权），杜绝用缓存正文喂模型。

**c) 三层质量把关**：

1. **规则校验**（validate_candidate_questions）：schema、重复（归一化文本）、开放题不允许"是否/有没有/多少/何时/哪个"开头、证据 quote 必须在 chunk 正文中；
2. **语义去重**：BGE-M3 嵌入后余弦 >0.92 判重（与已有问题 + 候选之间）；
3. **LLM 质量评审**：pass/reject/revise 逐题判定，拒绝记录原因；
- 最终必须两类问题均通过，否则 refine_retrieval_plan 重试（retry_count < max_retries=2）或失败。

**d) 持久化**：`persist_questions` 不覆盖人工编辑行；按 `(meeting, type, lower(trim(content)))` 去重；证据行 `question_evidences` 记录 chunk_id/document_id/block_id/quote/四类分数/source_type；`AiTask` → PENDING_REVIEW 等待人工核验。

**e) 任务唯一性**：`(meeting_id, task_type, source_version)` 唯一；thread_id = `meeting:{id}:question-generation:v{version}`；租约心跳 60s。

### 17.4 面试点

- 为什么"切点问题"不能问定性结论的具体数值？切点问题要求答案能从所引 quote 直接读出，这是防"模型从定性描述推断数字"的硬规则（prompt 明确禁止）。
- 为什么问题生成要检索"确认版纪要 + 知识库"双源？问题必须锚定本次会议（纪要），同时可用权威知识库补足事实上下文；双源可回答性由证据校验兜底。
- 为什么校验失败要重试"检索计划"而不是重试"生成"？错在检索范围/证据不足，换一批问题也无意义；重规划 → 重新检索 → 重新生成是闭环。

---

## 18. 检索 API 与审计

**文件**：`app/api/v1/search.py`

### 18.1 接口

`POST /api/v1/knowledge-bases/{kb_id}/search`

请求体：`query`（≤2000 字）、`top_k`（≤50）、`content_types[]`、`meeting_ids[]`、`document_ids[]`、`include_drafts`。

### 18.2 执行链路

```text
鉴权(require_kb_access) → 嵌入 query(dense+sparse)
→ Milvus 混合检索(50+50→RRF15, 过滤 org/kb/发布状态/类型/会议/文档)
→ PG 权威回填(最新版本, publication 校验)
→ rerank 取 top_k(≤5) → 组装 SearchResult(含 source_locator)
→ 写 retrieval_logs + (draft 检索时) audit_events
```

### 18.3 安全与审计

- `include_drafts=true` 要求角色 ≥ editor，且每次检索写审计事件（合规要求）；
- `query_hash`（SHA-256）记录在 retrieval_logs，支持去重统计与分析；
- 结果带 `dense_score / sparse_score / fused_score / rerank_score` 四类分数，支撑检索质量调优。

### 18.4 前端工作台

`SearchWorkbenchTab`：输入 query、top_k、类型/文档过滤、是否含草稿；用 `Intl.Segmenter('zh-CN')` 做中文分词高亮（带 stop-words 过滤与降级正则），展示 elapsed 与证据定位（页码/发言人/时间段）。

---

## 19. 评测与基准模块

**文件**：`backend/scripts/*`、`app/services/benchmark.py`、`app/api/v1/benchmarks.py`

### 19.1 四个脚本

| 脚本 | 用途 | 产出 |
|---|---|---|
| build_eval_set.py | 从已入库 chunk 采样，用 LLM 生成"仅该 chunk 可回答"的黄金问题 | eval_set.json（query + expected_chunk_id/document_id） |
| eval_retrieval.py | 同批 query 跑 4 种检索变体对比 | report.json：hit@1/3/5/10、mrr@10 |
| bench_search_latency.py | 镜像生产链路分阶段测延迟 | p50/p95/p99、均值、QPS |
| bench_embedding.py | 不同 batch_size 嵌入吞吐对比 | texts/sec |

四种检索变体：`dense_only / sparse_only / hybrid / hybrid_rerank`——直接量化"混合检索 + 重排"的增量价值，是最有说服力的简历/面试素材。

### 19.2 管理端在线评测

`benchmark_runs` 表 + Celery `run_benchmark`：admin 可触发 `embedding_throughput / search_latency / retrieval_quality` 三类评测，实时进度写入表，报告含 `environment`（device/model/strategy）便于控制变量。

### 19.3 面试点

- 为什么自建黄金集而不是公开数据集？领域（医药会议）没有现成评测集；自建黄金集针对本项目检索目标（Recall@k/MRR）。
- 如何避免"刷分"？黄金集人工抽检、跨 chunk 可答的问题剔除、优化前后同集同机同脚本对比。

---

## 20. 可观测性

**文件**：`app/services/observability.py`、`app/main.py`

### 20.1 请求级

- 每个 HTTP 请求生成/透传 `x-request-id`（响应头 + 错误响应体），贯穿日志与前端错误提示；
- 全局统一异常响应格式（code/error_code/message/details/request_id）。

### 20.2 LLM 链路（Langfuse）

- `observe()` 上下文管理器：未配置 Langfuse 时降级为 no-op（零成本）；
- 观测点：`knowledge.extract`（generation）、`retrieval.embed_query / retrieval.hybrid_search / retrieval.rerank`（spans）、`ingestion.{node}`；
- 记录输入字符数、候选数、排名与输出项数，trace_id 回写知识项表（`knowledge_items.langfuse_trace_id`）。

### 20.3 任务进度

- Redis Stream `job:{job_id}:events`（maxlen≈500）承载进度事件，`publish_progress` 双写（DB 任务表 + Stream）；
- WebSocket `/ws/jobs/{job_id}?token=` 推送进度，断线可重连追尾（last_id），terminal 事件结束连接。

### 20.4 面试点

- 为什么 Langfuse 用 lazy no-op？可观测不能成为可用性依赖；配置缺失/初始化失败都不影响主链路。

---

## 21. 前端架构

**文件**：`frontend/src/*`

### 21.1 技术栈

Vue 3.5（Composition API `<script setup>`）+ TypeScript 5.7 + Vite 6 + Pinia + Vue Router 4 + Element Plus + axios + dayjs + Vitest。

### 21.2 分层

- `api/`：按领域拆分的 API 模块（auth/kb/meetings/meetingImports/meetingVerification/benchmarks），统一 axios 实例；
- `stores/`：auth store（token 生命周期 + 用户信息）、app store；
- `composables/`：useMeetings；
- `types/`：与后端 Pydantic schema 对齐的 TS 类型；
- `utils/`：领域工具（kb 权限、meeting 状态、审核逻辑、高亮、表格解析、错误映射）；
- `views/`：Auth / 会议导入 / 纪要约校（大型编辑器）/ 核验列表与详情 / KB 列表与详情（5 个 tab）/ 评测 / 占位页；
- `components/`：MeetingForm、StatusTag、QuestionList、ReviewActionBar 等。

### 21.3 关键工程点

- **路由守卫**：`beforeEach` 先 `auth.initialize()`（有 token 则拉 /auth/me），未登录跳 /auth；登录后跳回 redirect；
- **axios 拦截器**：请求自动带 Bearer；401 时用 refresh token 刷新一次并重放原请求（`_retried` 标记防死循环），失败清 token；
- **WebSocket 进度 + 轮询兜底**：DocumentsTab 用 WS 收任务进度，socket 断开回退轮询；MeetingImportReview 用轮询（指数退避 1800ms 起）查向量化状态；
- **纪要约校编辑器**：脏块追踪（dirtyBlocks Map）、防抖保存、保存链串行化（saveChain）、乐观版本冲突提示、查找/替换/撤销（lastOperationId + 快照）、表格编辑、元数据表单独立版本；
- **中文高亮**：Intl.Segmenter 分词 + 停用词 + 正则转义降级；
- **构建**：vue-tsc 严格类型检查 + vite build；nginx 托管 SPA（try_files fallback）+ `/api` 反向代理（含 WS upgrade 头）。

### 21.4 面试点

- 为什么 axios 401 刷新要 `_retried`？防止 refresh 接口本身 401 触发无限递归。
- 为什么保存用"保存链 + 防抖"？连续编辑产生大量 PATCH，串行化避免并发写导致版本冲突风暴，同时防抖减少请求数。

---

## 22. 部署与运维

### 22.1 镜像与构建

- **backend**：python:3.11-slim + uv；apt 装 poppler-utils、tesseract-ocr(+chi_sim)、fonts-noto-cjk、libgl1（Docling 依赖）；入口先 `alembic upgrade head` 再起 uvicorn；
- **model-service**：python:3.11-slim + uv 锁 CPU torch；GPU 构建通过 `TORCH_INDEX_URL` + `--reinstall-package torch` 强制换 CUDA wheel；HF_HOME 挂卷 `huggingface_models` 缓存权重；
- **frontend**：node:22 构建 → nginx:1.28-alpine 托管；`client_max_body_size 60m` 匹配 50MB 上传。

### 22.2 健康检查与依赖顺序

- postgres（pg_isready）/ redis（redis-cli ping）/ minio（mc ready）/ etcd（endpoint health）/ milvus（9091 healthz）/ api（/health 探活）；
- `depends_on: condition: service_healthy` 保证有序启动；api 依赖 minio-init 完成建桶。

### 22.3 数据卷与备份

```text
postgres_data / minio_data / milvus_data / etcd_data / redis_data / huggingface_models
```

- 生产备份以 **PostgreSQL pg_dump 为业务事实来源**；MinIO 开启版本化并同步备份桶；Milvus 可由 Chunk 重建（不能替代 PG 备份）；
- 普通删除为软删除；owner/admin `purge=true` 才物理清除并同步 MinIO/Milvus，操作不可恢复，必须在备份后执行。

### 22.4 面试点

- 为什么 worker 与 api 共用镜像？同代码、同配置，减少镜像体积与漂移；启动命令不同（celery vs uvicorn）。
- GPU 构建为什么必须 `--reinstall-package torch`？uv 锁的 CPU wheel 版本号比 cu126 更高，`--upgrade` 不会替换，必须强制重装。

---

## 23. 测试与质量保障

### 23.1 后端

- pytest + pytest-asyncio（asyncio_mode=auto）+ **Testcontainers PostgreSQL**（真实 PG 上跑集成测试）；
- httpx ASGITransport 端到端测 API（auth/meetings/import/review 流程）；
- 单测覆盖：切块（边界/overlap/长表格/ID 稳定性）、解析器清洗、导入元数据确定性、RRF、JWT、模型客户端（复用/关闭/错误映射）、提取批处理与 provider 兼容、问题生成校验、embedding identity、vector_only 幂等；
- 静态检查：ruff（E/F/I/UP/B）+ mypy **strict**（exclude alembic）。

### 23.2 前端

- Vitest 单测（tests/unit/*.spec.ts）：KB 权限、会议状态、导入/审核/核验工具函数；
- 构建期 `vue-tsc --noEmit` 强制类型正确。

### 23.3 运行命令

```bash
cd backend && uv sync --dev && uv run pytest && uv run ruff check . && uv run mypy app
cd ../ && npm test && npm run lint && npm run build
docker compose --env-file .env.example config --quiet
```

---

## 24. 关键设计决策与权衡（面试问答）

> 这一章把全项目最重要的「为什么」集中整理，按面试追问深度排列。

### 24.1 为什么 PostgreSQL 是唯一权威，而不是把正文也放进向量库？

业务事实（会议、文档、知识项、审核、任务、版本）需要 ACID 事务、外键、唯一约束与审计；向量库的定位是**相似度索引**，其写入是异步、最终一致的。正文/引用/版本以 PG 为准，Milvus 任何时刻都可被 PG 重建；检索时回填 PG 再校验，杜绝"向量库里有、业务上已不可见"的脏结果。

### 24.2 为什么入库流水线用 LangGraph 而不是顺序函数？

三个硬需求：① 失败后从安全节点重入（条件路由 + 节点幂等）；② 人工审核中断/恢复（checkpoint + interrupt）；③ 多模式复用（完整入库 / vector_only 预向量化 / 确认后从 extract_knowledge 续跑）。这些用手写流水线会退化成一堆状态分支，而 LangGraph 原生支持状态持久化与回放。

### 24.3 为什么要有「预向量化 + 确认后复用」两段式？

纪要约校可能要反复编辑，若每次都全流程重跑（解析+切块+embedding+提取）代价极高。设计上：草稿就绪即预向量化（vector_only），编辑导致版本变化时旧向量置 STALE、按需重向量化；确认时要求"当前版本恰好 SYNCED"，确认后的入库任务直接从 `extract_knowledge` 开始，**复用已有 Chunk 与向量**。结果：编辑期快、确认后不重复计算。

### 24.4 为什么发布用 Outbox 而不是同步写 Milvus？

发布是用户等待的关键操作；Milvus 抖动不应阻塞业务提交。事务性 Outbox 保证"文档发布成功 ⇒ 向量发布事件必达"，配合幂等键（`document.publish:{id}:{version}`）与 30s 对账实现最终一致。

### 24.5 为什么稀疏向量 + 稠密向量混合，还要再重排？

BGE-M3 的 dense 捕捉语义、sparse（词法权重）捕捉精确词匹配（医药专名、剂量、代号），RRF 融合两者提升召回；reranker 是 cross-encoder，对 query-document 对逐对打分，精排 top-5 提升精度。评测（eval_retrieval）实测 hybrid_rerank 优于任何单一路径——这是量化过的决策而非拍脑袋。

### 24.6 为什么 embedding 策略默认 single_pass_pool？

入库是离线任务但受限于 CPU/GPU 吞吐；两段式编码对每个 chunk 算两次。single_pass_pool 只对语义单元编码一次，chunk 向量由单元向量平均（dense）+ 并集（sparse）得到，边界与 two_pass 完全一致，速度近一倍。代价是 chunk 级向量是近似，由 rerank 环节补偿。

### 24.7 为什么「人工审核」放在图里而不是 API 里？

发布门禁同时涉及状态机、证据校验、向量同步、Outbox 与图恢复（resume），集中在图节点让整个生命周期可追踪（node_executions 幂等表 + checkpoint）。`review_gate` 的 interrupt 是显式的"执行挂起"，比在 API 层拼装更可审计。

### 24.8 为什么用 advisory lock 而不是行锁？

向量化是跨会话的长任务，且与"正文编辑"是两个完全不同的请求路径，共同目标是"同一文档的写入互斥"。`pg_try_advisory_lock` 不依赖行存在性、可超时等待（轮询）、显式释放；行锁会因事务生命周期短而失效，且不适用于"文档级别"的粗粒度互斥。

### 24.9 为什么 AI 任务的失败不无限重试？

分两层：Celery 层对 5xx/未知错误指数退避重试（有上限）；AiTask 层 `retry_count < max_retries(2)` 且只对可重试错误，达到上限后置 FAILED 等待人工介入。业务冲突（4xx）一律不重试——重试不能解决输入/状态问题。

### 24.10 安全上做了哪些关键决策？

① Argon2 密码哈希；② Access 15 分钟 + Refresh 轮换且只存 SHA-256 摘要；③ `token_version` 支持整体吊销；④ 每次请求查库校验用户/组织/成员关系；⑤ 全链路 `organization_id` 租户隔离；⑥ 部分唯一索引 + 幂等键防重放/重复；⑦ Milvus 过滤表达式转义；⑧ 上传 MIME/扩展名双重校验 + 大小限制 + 文件名净化。

### 24.11 这个架构的瓶颈与扩展方向？

- **瓶颈**：CPU 嵌入吞吐（可上 GPU）、Milvus standalone 单节点（可切分布式/集群）、单 worker（可横向扩容 + prefetch 调优）、LangGraph checkpoint 与任务表增长（需定期清理）。
- **扩展**：实时 ASR 流式入库、GraphRAG 多跳、跨 KB 检索、最终问答生成、Prompt/Schema 在线编辑器、组织公共 KB。

---

## 25. 各模块高频面试问题清单（面经扩充版）

> 本章在原 30 题基础上扩充至 8 组 65 题。素材来自牛客网 2026 年 AI 应用开发岗位面经（大模型应用开发 5 年经验、校招 AI 岗「从基础到死亡追问」、凌脉/蚂蚁/安软/淘天 AI 应用开发实习与一面/二面、AI 岗八股与普通开发岗的区别等），每题按「一句话记忆点 + 展开」组织，展开尽量结合本项目代码。建议先背每组的一句话记忆点，再读展开；被追问细节时按展开分层作答。标记「了解即可」的题目本项目未直接实现，回答时须诚实说明"了解原理但未实践"。

### 25.1 项目背景与自我介绍

**1. 用一句话介绍这个项目？**

一句话记忆点：**医药会议资料的可审核知识入库与证据检索工作台**。

展开：面向医药企业与学术会议场景，把 PDF/DOCX/PPTX/逐字稿等会议资料经过「解析 → 语义切块 → 向量化 → 人工审核 → 发布」的受控流水线变成权威知识库，再提供带证据定位（页码/发言人/时间段）的混合检索工作台，并支持从已确认纪要进一步生成「切点/开放」两类可核验问题，为后续问答与分析做数据准备。核心特征是"AI 产物必须可追溯到真实 chunk/quote，未审核内容不可被检索"。

**2. 项目解决了什么真实业务痛点？**

一句话记忆点：**医药会议知识散、查不到、不敢信，通用 RAG 在医疗场景没有审核背书**。

展开：① 资料散落在 PDF/PPT/逐字稿里，人工整理知识条目耗时易漏；② 医学部/市场部想查"某药某适应症的推荐剂量、专家共识、研究证据"只能翻原始文件；③ 直接用通用 RAG 检索，结果没有版本与审核背书，模型可能无中生有，医疗场景不可接受。本项目把「入库-审核-发布-检索」变成受控流水线，检索结果可定位到出处，且只有已审核发布的内容可被检索，从流程上保证"可解释、可追责"。

**3. 你在项目中担任什么角色？做了哪些模块？**

一句话记忆点：**独立负责从 0 到 1 的 AI 应用全链路，重点在语义切块、检索编排与评测**。

展开：后端 API（FastAPI）、异步任务与工作流编排（Celery + LangGraph）、文档解析与语义切块、向量化与混合检索、评测与基准脚本、Docker Compose 部署均参与实现；前端负责检索工作台与核验控制台。面试时聚焦 2~3 个最有深度的点展开：语义切块器（结构+语义+overlap）、LangGraph 人工审核闸门与断点续跑、四路检索变体的量化评测。

**4. 项目有什么量化成果？**

一句话记忆点：**用黄金集实测「混合检索 + 重排」优于任何单一路径，延迟/吞吐有分阶段基准**。

展开：自建黄金集（每问锚定唯一 chunk）跑 `eval_retrieval`，四种变体 dense/sparse/hybrid/hybrid_rerank 同集同机对比，hybrid_rerank 在 Recall@k/MRR 上优于单路；`bench_search_latency` 按生产链路分阶段测 p50/p95/p99；`bench_embedding` 对比 batch_size 与吞吐。面试话术：**不报编造的数字，报"怎么测的、什么口径、和什么对比"**，并说明具体数值以评测报告为准。

**5. 与同类产品/通用 RAG 方案的区别？**

一句话记忆点：**通用 RAG 只有"检索+问答"，本项目多了一层医疗场景必须的"审核门禁与证据审计"**。

展开：开源 ChatPDF/示例代码通常是「解析-切块-入库-直接回答」，没有版本管理、发布状态与人工审核；本项目引入草稿预向量化、review_gate 人工闸门、证据校验（quote 必须真实存在）、检索回 PG 校验发布状态与最新版本、检索审计日志，构成"可审核知识库"而非"随便问答"。商业医学知识库（如 UpToDate）是编辑团队维护的公开权威内容，本项目解决的是**企业私有会议资料**的入库与检索，二者不冲突。

### 25.2 通用/架构

**6. 整体有哪些组件？各自职责？**

一句话记忆点：**API（入口）、Worker（长任务）、模型服务（推理）、三库一存储（权威/向量/任务/原件）+ 前端**。

展开：FastAPI 负责鉴权、CRUD、检索入口，规则薄、服务厚；Celery Worker + LangGraph 负责解析、切块、向量化、知识提取、问题生成等分钟级任务；独立模型服务加载 BGE-M3（dense+sparse）与 reranker；PostgreSQL 是唯一权威数据源，Milvus 存向量投影，Redis 承担 Stream 任务/事件、租约心跳与缓存，MinIO 存原件；前端 Vue3 + TS。详见第 2、3 章。

**7. 为什么拆成 API / Worker / 模型服务三个进程？**

一句话记忆点：**资源与失败域解耦，各自独立扩缩容**。

展开：API 追求低延迟、可水平扩展；Worker 跑分钟级长任务，独立控制并发；模型服务是 CPU/GPU 敏感型，可单独换镜像、加 GPU、扩容。失败域隔离：模型服务挂掉时上传/审核/发布仍然可用；模型服务可 mock，开发测试不依赖真实模型。同类思路也适用于任何"在线接口 + 离线计算 + 推理服务"三段式架构。

**8. 数据一致性如何保证？**

一句话记忆点：**PG 唯一权威 + 事务性 Outbox + 幂等写入 + 检索回源校验 + 对账补投**。

展开：① 所有业务事实以 PG 为准，Milvus 只是可重建投影；② 文档发布与事件派发同事务走 Outbox，避免"库变了事件丢了"；③ 切块/向量/任务全部幂等（幂等键 + 唯一索引），重复投递不重复执行；④ 检索前回 PG 校验最新版本与发布状态，防止脏向量被检索到；⑤ Celery Beat 周期性对账，把丢失事件补投回来。

**9. 如果 Milvus 挂了怎么办？**

一句话记忆点：**写入侧不受影响，检索侧优雅降级，恢复后从 PG 重建**。

展开：上传、确认、发布等写路径不依赖 Milvus 可用（向量写入失败任务会重试）；检索接口在 Milvus 不可用时返回可识别的 503 错误而非错误结果；恢复后由 Chunk 表与 `embedding_identity` 支撑从 PG 重建向量。回答加分点：**向量库不是数据源，只是索引投影**——这是架构上最重要的容灾前提。

**10. 如何评估一个 RAG 系统的好坏？**

一句话记忆点：**检索质量（黄金集 Recall@k/MRR）+ 生成质量（证据校验/人工抽检）+ 性能（延迟/吞吐）**。

展开：检索侧自建黄金集跑 `eval_retrieval` 四种变体对比；生成侧以"证据校验通过率 + 人工抽检"为主，可用 Ragas 的 Faithfulness/Answer Relevancy 作参考；性能侧用 `bench_search_latency`（p50/p95/p99）与 `bench_embedding`（吞吐）。面试结论：**没有评测就没有优化**，任何 RAG 改动都要有同一集、同一机、同一脚本的前后对比。

**11. 系统目前规模如何？10 万级用户量级怎么重构？**

一句话记忆点：**先承认单机现状，再给横向扩展路径**。

展开：当前是 Milvus standalone + 单 worker + CPU 嵌入，适合百万 chunk 级私有知识库起步。10 万级用户重构路径：Milvus 切分布式/分片并开启副本；Worker 水平扩容 + prefetch=1 调优；模型服务上 GPU 或独立多副本；检索加 Redis 热 query 缓存（带 org 隔离）；PG 读写分离、任务表按状态+时间分区并定期归档；查询走游标避免深翻页。结构上先答"量瓶颈在哪"，再答"对应怎么扩"。

### 25.3 RAG 深度（面经高频）

**12. RAG 是什么？最难的环节在哪里？**

一句话记忆点：**RAG 是"检索增强生成"，最难的是文档切分**。

展开：RAG 把外部知识检索出来拼进 Prompt，让模型基于证据作答，解决"模型不知道、知识更新快、回答要可溯源"三个问题。最难的不是把模型接上，而是**切分**：切碎了事实和证据被切断，召回与引用全失效；切大了无关信息稀释注意力，token 成本上升。本项目用语义切块（结构边界 + 语义边界 + token 预算 + overlap）正面解决这个问题，这也是面试最值得展开的点。

**13. RAG 与微调（Fine-tuning）的区别？怎么选型？**

一句话记忆点：**RAG 换知识、微调换行为；本项目选 RAG 因为知识高频更新且必须可溯源**。

展开：RAG 适合知识频繁更新、需要引用出处、领域私有资料（本项目）；微调适合固化模型行为/风格/格式（如一直用某种文书口吻）。RAG 优点：无需训练、更新即换库、可溯源、成本低；缺点：上限受检索质量限制、占用上下文。微调优点：行为稳定；缺点：训练成本、更新需重训、幻觉可能被固化且不可溯源。组合套路：先 RAG 给证据，若领域术语/文风仍有问题，再考虑 LoRA 轻量微调。

**14. 文档切分策略有哪些？如何避免语义被切断？**

一句话记忆点：**从固定窗口到语义切分，本项目用"结构边界 + 语义边界 + token 预算 + overlap"四件套**。

展开：① 固定窗口滑窗：简单但会切断事实与证据；② 按标题/段落：自然但有长有短、无法控 token；③ 纯语义切分：相邻单元 embedding 相似度找边界，计算量大；④ 本项目 semantic-v3：heading/table/speaker 切换等结构边界优先，相邻单元 dense 余弦 <0.65 触发语义边界（且当前组超过 min 350 token），超过 1000 token flush，flush 时保留 100 token overlap 且 overlap 只取同段落、同 heading、同 speaker 的单元，不拖入 heading/表格；表格独立成块。**记忆口诀：结构保语义、预算控大小、overlap 保连续、表格不拆。**

**15. 多路召回（Hybrid Search）是什么？为什么用 RRF？**

一句话记忆点：**dense 管语义、sparse 管专名，RRF 无痛融合两个排序**。

展开：dense 向量召回语义相近内容，但对医学专名/剂量/研究代号这类精确词不敏感；sparse（BGE-M3 的词法权重）擅长精确词匹配但对同义改写弱。两路各自召回后，用 RRF 融合：`score = Σ 1/(k + rank)`，只依赖排名不依赖分数，天然避免两套打分体系无法归一化的问题。本项目 Milvus 混合检索（50+50 → RRF 取 15）后接 reranker 精排取 top 5，实测优于任何单一路径。

**16. 为什么还要 Rerank？bi-encoder 与 cross-encoder 有什么区别？**

一句话记忆点：**bi-encoder 快而粗筛，cross-encoder 准而精排，两者是"召回-精排"两段式**。

展开：bi-encoder（如 BGE-M3）把 query 和 doc 分别编码成向量再算相似度，可预计算、海量候选也能跑，但精度有限；cross-encoder 把 query 与每个 doc 拼起来过 Transformer 逐对打分，慢但准，适合对少量候选精排。本项目 Milvus 先召回 15 条，reranker 精排取 top 5，兼顾规模与精度。面试必背一句：**召回要覆盖，重排要精准，二者不是替代关系。**

**17. 检索效果如何量化？黄金集怎么建？**

一句话记忆点：**自建领域黄金集：从 chunk 反推"只有它能回答"的问题，指标用 Recall@k/MRR**。

展开：`build_eval_set.py` 从已入库 chunk 采样，用 LLM 生成"仅该 chunk 可回答"的黄金问题，产出 query + expected_chunk_id/document_id；人工抽检剔除跨 chunk 可答、有歧义、语义重复的问题，防止"刷分"。`eval_retrieval.py` 用同一批 query 跑 dense/sparse/hybrid/hybrid_rerank 四种变体，输出 hit@1/3/5/10 与 MRR@10。回答要点：**评测集必须领域化、问题必须锚定唯一 chunk、优化前后同集同机对比才公平。**

**18. Ragas 评测框架了解吗？Faithfulness 和 Answer Relevancy 是什么？**

一句话记忆点：**Ragas 是开源 RAG 评测框架，Faithfulness 查幻觉、Answer Relevancy 查切题**。

展开：Faithfulness 衡量生成内容有多少能被检索到的上下文支持（越低越像幻觉）；Answer Relevancy 衡量回答与问题的相关性；另有 Context Precision/Recall 衡量检索上下文质量。本项目以自研黄金集（Recall@k/MRR）为主，因为医药领域没有现成评测集、且检索目标明确；若面试官追问为什么不用 Ragas——**Ragas 更偏"生成侧"，我们是"入库-检索"项目，生成评测靠证据校验与人工抽检，两者可互补**。

**19. 如何区分"检索不好"还是"模型不好"？**

一句话记忆点：**看黄金集 Recall 定检索、看分数与输出定生成，bad case 先归因再修**。

展开：蚂蚁二面原题。方法：检索侧跑黄金集，Recall@k/MRR 不达标 → 切块/embedding/融合/过滤问题；检索达标但答案错 → 生成侧问题（prompt、模型能力、证据利用）。本项目 `retrieval_logs` 记录 dense/sparse/fused/rerank 四类分数与 query_hash，bad case 可直接复现：**分数低是检索问题，分数高答案错是生成问题**。这个"分层归因"是 RAG 面试最高频的深挖点。

**20. BGE-M3 的 dense/sparse 是什么？Embedding 模型怎么选型？**

一句话记忆点：**BGE-M3 一个模型同时出 dense+sparse，天然支撑混合检索**。

展开：dense 是语义向量（1024 维），sparse 是词法权重（近似稀疏向量），M3 支持多语言（中文友好）且可自部署（数据不出域、离线可用）。选型对比：OpenAI text-embedding-3 方便但数据出域且要钱；bge-large-zh 中文好但只有 dense；E5 英文强；M3 的优势是**dense+sparse 同模型**，正好支撑多路召回。若问"为什么不用商业化 API"：医疗数据敏感，模型服务必须本地化。

**21. 如何防 LLM 幻觉？RAG 还会幻觉吗？**

一句话记忆点：**三层防线：检索 grounding → 证据校验 → 人工审核门禁；但 RAG 只能减幻觉，不能消幻觉**。

展开：① 问题生成的检索计划必须先通过 grounding 校验（实体必须出现在会议上下文）；② 每个知识项/问题必须有真实 chunk/block 引用且 quote 存在，无效即拒绝（422 knowledge_evidence_invalid）；③ 未审核内容不可发布、不可检索，人工核验兜底。但模型仍可能错误解读引文或检索到无关证据，所以还要"可追溯、可驳回"：所有 AI 产出带证据引用，人工可改可拒。

**22. 上下文窗口满了怎么办？**

一句话记忆点：**摘要压缩 + 检索切片 + 滑窗，核心是"只喂必要的"**。

展开：四种主流策略：① 摘要/压缩（长文档先摘要再拼）；② 检索切片（只拼 top-k 相关 chunk，本项目做法）；③ 滑窗（只保留最近 N 轮）；④ 分层（章节摘要 + 叶子原文按需取）。本项目回答"为什么按 chunk 检索而不是整篇塞进去"：token 预算 + 相关性 + 成本，整篇塞会稀释注意力且很贵。追问点："检索不到就答不出"怎么办？→ 加多路召回、放宽阈值、提示模型明确"知识不足"而不是编造。

**23. Token 怎么算？Tokenizer 是什么？中英文差异？**

一句话记忆点：**Token 是模型最小文本单元，中文约 1 字≈1~2 token，英文 1 词≈1.3 token，必须用 tokenizer 数**。

展开：Tokenizer 把文本切成词/子词片段并映射成 ID，不同模型分词器不同，不能跨模型用字符串长度估算。工程意义：本项目切块参数用 token 而非字符（CHUNK_TARGET_TOKENS=700/MAX=1000），模型服务按字符上限防御（32000）并按 30k 分批，都是"以 token 为单位的预算管理"。面试可答：**预算单位用 token，校验手段用字符上限，双层防御**。

**24. KV Cache / 前缀缓存了解吗？Prompt 怎么排利于缓存？**

一句话记忆点：**KV Cache 加速生成，前缀缓存省钱，静态内容放前面、动态内容放后面**。

展开：Transformer 解码时每步重算历史键值太贵，KV Cache 缓存中间层 K/V 避免重复计算；DeepSeek/OpenAI 等提供前缀缓存，对相同前缀只计一次费。工程启示：系统提示、固定规则、few-shot 示例等静态内容放 Prompt 前面并保持稳定，检索结果/用户问题等动态内容放后面，提高前缀命中率，降延迟降成本。本项目 prompt 就是"系统指令静态 + 证据/问题动态在后"的结构。

**25. 大模型调用超时/限流/报错怎么处理？**

一句话记忆点：**超时分级、错误码稳定化、指数退避重试、业务 4xx 不重试**。

展开：嵌入请求超时 300s（大 batch 合法长耗时）、LLM 结构化提取超时 60s；把 provider 的 context length/max tokens/finish_reason=length 等错误统一映射为稳定错误码 413 `llm_completion_limit`（批次二分递归定位到具体子批），而不是笼统 500；5xx/未知错误指数退避重试（min(300, 2^(n+1))，最多 5 次），429 限流退避；业务冲突（4xx）一律不重试，直接 FAILED 等人工。回答要点：**错误语义要稳定，重试策略要分错误类型**。

**26. 上下文预算管理与渐进式披露是什么？**

一句话记忆点：**调用前算好 token 预算，高价值信息优先，详细证据按需再取**。

展开：凌脉面经原题。每次调用前计算可用 token，按"必要信息优先"排序，装不下就截断次要内容；渐进式披露：先给模型结构化字段/摘要等浓缩信息，需要细节时再按需取原文。本项目对应实现：`rehydrate`——调模型前才从 PG 取正文并校验授权，不把整篇缓存进状态；前端纪要编辑按 chunk 提取正文，避免一次喂全部。面试可串讲：**预算管理 + 渐进披露 + 前缀缓存是 LLM 应用降本三件套**。

**27. GraphRAG 了解吗？和向量 RAG 的区别？**

一句话记忆点：**向量 RAG 找"相关段落"，GraphRAG 做"跨文档多跳推理"**。

展开：GraphRAG 先用 LLM 抽取实体-关系构建知识图谱，检索时沿图多跳扩展，擅长"全局性/聚合性"问题（如"整个数据集里有什么共性规律"）；代价是建图贵、慢。向量 RAG 适合"找出处"的问答，是大多数业务的第一选择。本项目当前用"双路证据源（权威知识库 + 确认版纪要）+ 检索计划"近似解决多实体问题，Roadmap 预留 GraphRAG 多跳作为演进项——**面试说"规划中"要明确标注**。

### 25.4 解析与入库

**28. Docling 解决什么问题？为什么不直接用 pdfplumber？**

一句话记忆点：**Docling 给"版式语义与阅读顺序"，pdfplumber 只是文本/表格提取器**。

展开：蚂蚁面经原题"跨页表格语义完整"。pdfplumber 擅长按坐标取文本和表格，但拿不到版面结构、阅读顺序、表格跨页合并等语义；Docling 输出统一 DoclingDocument（块、表格、阅读顺序），跨页表格能合并成完整表。本项目解析层统一输出 Block（block_type/heading_path/table_markdown/page），切块与检索不感知具体格式，PPTX/DOCX 都能走同一套。记忆点：**解析器只管"结构还原"，下游只认 Block**。

**29. 语义切块的边界规则有哪些？overlap 怎么控制？**

一句话记忆点：**四类边界触发 flush：结构、语义、token 预算、表格独立；overlap 只回带同段同 heading 同 speaker 的单元**。

展开：结构边界：heading/table 起始、heading_path 变化、speaker 切换；语义边界：相邻单元 dense 余弦 <0.65 且当前组超 min 350 token；token 预算：超过 1000 flush；表格独立成块且 overlap 不跨越表格。overlap=100 token，flush 时从尾部逆序挑选不超过预算的同段单元带回下块开头，heading/table 不进 overlap。**问"overlap 会不会重复计数"：只用于上下文连续，证据以 chunk 为单位，重复内容由 source_block_ids 可解释。**

**30. chunk_id 为什么必须稳定？**

一句话记忆点：**稳定 ID 是幂等写入与证据引用的地基**。

展开：chunk_id 由 document_id + chunk_index + chunker_version 派生，内容不变则完全稳定；支撑"先删后插"的幂等向量写入、检索证据引用跨任务一致、切块器升级后按 chunker_version 精准重索引。若不稳定：重跑任务生成新 ID，旧引用全部失效，审计与对账全乱。

**31. 上传后任务失败如何恢复？**

一句话记忆点：**断点续跑 + 幂等跳过 + 指数退避 + 原件保留**。

展开：LangGraph checkpoint 持久化执行状态，失败从最近安全节点重入，已完成节点由 NodeExecution 幂等表跳过；`run_ingestion` 对 5xx/未知错误指数退避重试（最多 5 次），4xx 业务错误带稳定错误码置 FAILED；原件在 MinIO 永久保留，可重试/重解析/重索引。Celery 侧 `acks_late` 保证 worker 崩溃不丢消息。

**32. 为什么会议导入要先"预向量化"（草稿阶段就切块入库）？**

一句话记忆点：**让审核过程"所见即所得"，发布时零等待**。

展开：草稿阶段就完成切块与向量化，用户在纪要编辑页能立即按 chunk 查看/定位内容，体验和最终发布一致；发布时向量已就绪，只做发布状态同步，缩短"确认 → 可检索"的延迟。安全上，检索强制过滤发布状态，草稿向量只入库不对外可见。**记忆点：预计算 + 状态门禁，而不是发布时才补算。**

**33. 百万文档/超大文档如何切分（系统设计题）？**

一句话记忆点：**离线批量流水线 + 幂等 + 分代 + 水平扩容**。

展开：① 架构分阶段：上传→解析→切块→向量化解耦，队列削峰；② 增量只处理新增/变更（内容哈希驱动），不重算全量；③ 按 embedding_identity 分代，新旧向量不混用，重索引只影响受影响文档；④ Worker 水平扩容、Milvus 分片、模型服务 batch 调优；⑤ 失败重试 + 对账 + 幂等防重复。这套回答直接映射本项目 pipeline 的放大版，比背八股可信。

**34. 为什么用 LangGraph 而不是自写流水线？状态怎么设计？**

一句话记忆点：**checkpoint + interrupt + 条件路由开箱即用，State 只存轻量元数据**。

展开：LangGraph 内置 PG checkpoint（失败断点续跑）、interrupt（人工闸门挂起/恢复）、条件路由与状态回放，自写要重复造轮子且难审计。State 设计（蚂蚁二面"状态膨胀"原题）：`IngestionState` 只存 job_id/document_id/start_node/input_version/status/summary/revision 等轻量字段，**不存正文**——正文放 DB 按需 rehydrate，避免 checkpoint 序列化与写入成本随状态线性膨胀。记忆点：**State 只放"指针"不放"货物"。**

**35. Human-in-the-loop（人工闸门）怎么实现？**

一句话记忆点：**review_gate 节点 interrupt() 挂起图执行，审核通过后 Command(resume) 从断点精确继续**。

展开：入库图到 review_gate 调用 `interrupt({"status": "WAITING_REVIEW"})`，执行状态被持久化（PG checkpoint），文档置 AWAITING_REVIEW；审核通过后 API 触发 resume_ingestion 以 `Command(resume={"published": True})` 继续，不是整个重跑；拒绝则 mark_failed。为什么放图里不放 API：发布门禁同时涉及状态机、证据校验、向量同步、Outbox 与图恢复，放图内整个生命周期可追踪（node_executions + checkpoint），比 API 拼装更可审计。延伸：高敏感操作（删除/转账）也应走 HITL 审批。

**36. 为什么每个节点都要幂等？input_version 怎么算？**

一句话记忆点：**重复投递不重复执行，输入一变版本就变**。

展开：`idempotency_key = job_id:node:input_version`，NodeExecution 表唯一索引兜底；input_version 是 `sha256(sha256(内容哈希) + template_id + template_version + embedding_version + chunker_version + chunker_config + revision_id + revision_version)`，任何输入变化都会产生新版本。为什么用摘要不用拼接原文：idempotency_key 列是 VARCHAR(255)，拼接会超长，摘要定长且确定性。

### 25.5 向量与检索

**37. RRF 的 k 为什么取 60？**

一句话记忆点：**k 是经验阻尼值，越大越抑制长列表，60 是社区验证的稳健默认**。

展开：RRF 分数 = Σ 1/(k + rank)，k 越小第一名权重越高、对长列表越敏感；k 越大越平滑、对长列表更宽容。60 是 Elasticsearch/Weaviate 等社区广泛验证的默认值，跨场景稳健；本项目直接采用并允许配置化调参。**面试延伸：RRF 不需要分数归一化，这是它比加权和更适合混合检索的原因。**

**38. 为什么检索要回 PG 校验？校验什么？**

一句话记忆点：**Milvus 是投影，PG 是权威，检索必须"验明正身"再返结果**。

展开：校验①最新版本（revision 是否最新）；②发布状态（PUBLISHED，草稿不可见）；③组织/租户权限与过滤条件；④回填正文、页码、发言人、时间段等展示字段。解决"删库了向量还在""旧版本内容被搜到""未审核内容泄漏"三类脏数据问题。**一句话：向量库负责"找得到"，PG 负责"能不能看、是不是最新"。**

**39. embedding 策略切换后旧向量怎么办？**

一句话记忆点：**embedding_identity 分代隔离，旧新不混用，按代重索引**。

展开：`embedding_identity = f"{embedding_version}@{model}:{strategy}"` 写入 Chunk 表与 Milvus 每条记录；查询按 identity 过滤，保证同一 query 只与同一代向量比较；切换后旧文档按受影响范围重索引（从 PG 重建）。这就是"向量库版本隔离"的工程实现，面试官问"模型升级怎么不炸"就答这个。

**40. 为什么用 Milvus 而不是 Qdrant / pgvector？**

一句话记忆点：**规模 + 原生混合检索 + 与业务库隔离**。

展开：pgvector 与业务同库最简单，但十万级以上性能与混合检索能力有限，且让"权威库"与"索引投影"耦合；Qdrant 轻量、过滤强，是单机首选之一；Milvus 专为大规模型设计、原生支持 dense+sparse 混合检索（本项目直接用它做 RRF 融合）、可独立扩缩容。**Redis 里为什么不存向量：向量检索不是 Redis 的强项，且大向量会制造 BigKey，Redis 只承担任务/事件/心跳。**

**41. Redis 在架构里的作用？Redis Stream 与 Pub/Sub 区别？**

一句话记忆点：**Stream 持久化可追尾，Pub/Sub 广播不持久**。

展开：Redis 承担 Celery broker/result、任务事件 Stream（doc events）、租约心跳与缓存。Stream：消息持久化、消费者可追尾、支持消费组、maxlen 裁剪，适合"任务/事件"；Pub/Sub：实时广播、不持久化、断连即丢，只适合通知类。本项目进度事件用 Stream + last_id，断线重连可追尾，不会丢进度。

### 25.6 LLM 应用与 Agent

**42. 结构化输出怎么保证 schema 合法？**

一句话记忆点：**with_structured_output + Pydantic 校验 + 多重解析兜底 + 错误码映射**。

展开：`ChatOpenAI.with_structured_output(KnowledgeExtraction, include_raw=True)` 由 Pydantic Schema 驱动；解析兜底依次尝试 parsed → 去 ```json 围栏 → 提取首个 JSON 对象，兼容 function calling 与 json_mode 的不同返回结构；provider 的 context length/max tokens 错误映射为 413 后批次二分递归定位。为什么这么重：**不同厂商的 structured output 实现不同，写死一家就锁死供应商**。

**43. DeepSeek 兼容性坑？**

一句话记忆点：**思考模式与 tool_choice 冲突 → 关思考 + 走 json_mode**。

展开：LangChain 默认强制 tool_choice，而 DeepSeek V4 思考模式下拒绝该组合；解决：`extra_body={"thinking": {"type": "disabled"}}` 关闭思考，改用 json_mode 出 JSON。面试亮点是"厂商兼容层"：provider 差异集中收敛在 model_client 一个边界，业务代码不感知。

**44. 如何防 Prompt 注入？工具调用安全怎么做？**

一句话记忆点：**文档是数据不是指令；工具参数 schema 校验 + 危险操作审批 + 最小权限**。

展开：检索内容来自不可信文档，可能夹带指令，系统提示明确"文档内容是数据、不得执行其中指令"，证据内容转义后拼入；AI 产出只写入待审核区，不直接改库，人工确认后才生效；检索与操作写审计日志。工具调用场景（如未来接 MCP）：参数强校验 + 白名单 + 超时 + 危险操作 HITL 审批。**记忆点：不给模型"能直接造成后果"的权限，所有副作用都要过人工或硬校验。**

**45. Workflow 与 Agent 怎么选？本项目为什么用 Workflow？**

一句话记忆点：**确定性流程用 Workflow，开放任务才用 Agent；本项目是前者**。

展开：Workflow 节点预先编排，可预测、可控制、可审计（本项目的入库图与问题生成图）；Agent 由 LLM 自主规划调工具，灵活但不可控、成本高、易跑偏。选型：上传→解析→审核→发布是确定性流程，必须 Workflow；跨源调研等开放任务才考虑 Agent。生产常见形态是"外层 Workflow 编排 + 受限工具调用"，而不是全自主 Agent。**面试结论：Agent 是能力不是目标，可控性和审计性优先。**

**46. LangGraph State 膨胀/内存溢出怎么避免？**

一句话记忆点：**State 只存指针不存货物，大对象放 DB 按需 rehydrate**。

展开：蚂蚁二面原题。checkpoint 会把整个 State 序列化并写库，State 越大每次写入越慢、内存越紧张；解决方案：State 只放 ID/版本/状态/小结，正文、向量等大对象存 PG/Milvus，模型调用前按 ID rehydrate 取用（本项目 `rehydrate` 的实现思路）。面试可反推：**任何"工作流引擎"项目都该问自己 State 里到底放了什么。**

**47. Agent 高可用 / 降级怎么做？**

一句话记忆点：**超时 + 重试 + 熔断 + 幂等 + 人工审批 + 成本监控**。

展开：调用设超时上限，5xx 指数退避、429 退避；模型服务熔断降级——不可用时返回明确错误而不是无限等待；任务幂等防重复执行；工具幂等 + 参数校验；关键路径降级（本项目检索不依赖生成，生成挂了检索工作台照常可用）；监控 token 成本与延迟，超预算告警。**记忆点：可用性 = 优雅降级路径，而不是把每个服务都做成 9 个 9。**

**48. MCP 与 Skill 了解吗？区别？**

一句话记忆点：**MCP 是"能干什么"的标准化工具协议，Skill 是"怎么干"的指令包**。

展开：MCP（Model Context Protocol）标准化"Agent ↔ 工具/资源"的接入，服务端暴露工具、客户端调用，解决工具生态碎片化；Skill 是给模型/Agent 的行为指令与流程知识，教它按什么步骤做事。两者可组合：Skill 指导流程，MCP 提供执行能力。本项目暂无 Agent，属"架构预留"，面试要标注"了解原理、未接入"。

### 25.7 工程与可靠性

**49. Celery 怎么防任务丢失？**

一句话记忆点：**acks_late + reject_on_worker_lost + prefetch=1 + 对账**。

展开：`acks_late=True` 任务执行成功才 ack，worker 崩溃消息重新投递；`reject_on_worker_lost` 处理进程被杀场景；`prefetch=1` 一次只取一个任务，避免长任务堆积在 worker 内存；Celery Beat 周期性对账把丢失事件补投。记忆点：**宁可消息排队等，不让 worker 死了丢任务**。

**50. 租约 + 心跳解决什么问题？为什么需要 attempt_token？**

一句话记忆点：**把"进程死亡"转化为"租约过期"，attempt_token 防止过期任务复活写脏数据**。

展开：长任务进程可能随时崩溃，无限期锁会造成死锁；租约（60s）+ 心跳续期，进程死了租约自然过期，其他 worker 可接管。attempt_token 标识"当前执行代"：任务过期复活时携带旧代 token，写入被拒，避免两个 worker 同时处理同一任务。**记忆点：锁必须有到期时间，代际标记防"僵尸执行"。**

**51. Outbox 模式的适用场景与缺点？**

一句话记忆点：**要"业务事务与事件发布原子"就用 Outbox，代价是额外表、延迟与契约维护**。

展开：适用场景：发布文档时必须保证"状态变更"与"派发 question_generation 事件"同事务，缺一不可。实现：业务表 + outbox 表同事务写入，后台 poller/Beat 投递成功后标记；缺点：额外表与消费延迟、事件契约需版本管理、投递乱序需幂等与补偿。本项目文档发布 + 事件派发走 Outbox，对账补投。

**52. 乐观锁版本号如何防并发覆盖？**

一句话记忆点：**WHERE version = expected，没命中就是 409，拉最新再重试**。

展开：每个可编辑实体带 version 字段，更新时携带 expected_version，SQL 更新条件带上 version，受影响行数为 0 说明版本过期，返回 409；客户端拉取最新版本合并/重试。本项目会议核验的 verification_version 每次编辑/确认递增，防止两个人同时编辑互相覆盖。为什么不用悲观锁：读多写少、冲突概率低，避免长事务持锁。

**53. 为什么用 WebSocket 推进度而不是轮询？**

一句话记忆点：**实时 + 服务端主动 + 断线可追尾**。

展开：WebSocket 实时推送任务进度，避免轮询的固定延迟与无效请求；关键细节是"追尾"：事件 Stream 带 last_id，断线重连后从上次位置补拉，不丢进度；本项目 DocumentsTab 用 WS 收进度、socket 断开回退轮询（前端兜底）。**面试延伸：WS 与轮询不是二选一，常见是"WS 为主 + 轮询兜底"。**

**54. Redis Stream 相比 Pub/Sub 好在哪？**

一句话记忆点：**Stream 持久化、可追尾、有消费组；Pub/Sub 广播、不持久、断连即丢**。

展开：Stream 消息写入内存并持久化，消费者可用 last_id 追尾，支持消费组与 maxlen 裁剪；Pub/Sub 是即发即弃的广播，消费者断连期间消息直接丢。任务/事件类用 Stream（本项目），实时通知类才用 Pub/Sub。

**55. 前端 401 自动刷新怎么防死循环？**

一句话记忆点：**独立刷新请求 + 并发合并 + 失败即登出**。

展开：用独立 axios 实例或标志位，让"刷新 token 的请求"本身不进入拦截器（避免刷新失败再触发刷新）；多个请求同时 401 时只发起一次刷新，其余排队等同一个 Promise；刷新失败（refresh 过期）清除 token 跳登录。**记忆点：刷新路径不能递归触发拦截器。**

**56. 如何做租户隔离？谁能看到别家数据？**

一句话记忆点：**organization_id 全链路强制注入 + 403 不泄露存在性**。

展开：所有表与查询强制带 organization_id（Service 层注入，不信任前端传参）；唯一索引含 org 防止跨租户撞键；Milvus 过滤表达式对 org 过滤并做转义；检索/上传/审核全链路校验用户-组织-成员关系；资源不存在或越权统一返回 403，不返回 404，避免探测。**记忆点：隔离是"每一层都带租户条件"，不是"前端少传一个参数"。**

**57. 数据库慢查询/索引怎么优化？**

一句话记忆点：**EXPLAIN 找全表扫描，复合索引覆盖"查询+排序"，幂等靠部分唯一索引，深分页用游标**。

展开：先 EXPLAIN ANALYZE 定位；为高频条件建复合索引（如任务表按状态+时间）；部分唯一索引支撑幂等键；避免在索引列上套函数；深翻页不要 offset 巨大值，用游标/keyset；正文等大字段单独存放，索引只建查询列。本项目对账与任务查询按状态+时间索引，幂等键用部分唯一索引。

**58. 部署、监控、成本控制怎么做？**

一句话记忆点：**容器化编排 + 观测三件套 + token/延迟/失败率告警 + batch/缓存降本**。

展开：Docker Compose 编排 API/Worker/模型服务/DB/Redis/Milvus/MinIO，GPU overlay 镜像；Langfuse 观测 LLM 调用（token 用量与成本）；监控指标：任务失败率、检索 p95、模型错误率、token 消耗；成本：嵌入 batch_size 调优（bench_embedding 定参数）、前缀缓存、结果缓存、模型服务降级。**记忆点：成本先量化再优化，指标先监控再告警。**

**59. Bad Case 分析三件套是什么？（死亡追问）**

一句话记忆点：**台账 → 归因 → 回归**。

展开：① 建 bad case 台账：记录 query、期望、实际输出、检索四类分数、命中的 chunk、模型输出全文，保证可复现；② 归类定位：检索失败 / 生成失败 / 数据缺失 / 评测问题，按 25.19 的分层归因；③ 修复后回归：同一黄金集重跑 + 人工抽检，确认没有引入新问题。面试话术：**"bad case 不是玄学，是可复现、可归类、可回归的流程。"**

**60. 准确率从 90% 到 99% 怎么提升？**

一句话记忆点：**先量化基线，再按"数据→切块→检索→生成"逐层优化，每步都要有评测数字**。

展开：① 建/扩黄金集量化当前基线；② 数据侧：补齐缺失资料、去噪、去重；③ 切块调优：语义边界与 overlap 参数；④ 检索侧：dense+sparse+RRF+rerank（本项目四变体实测就是这步的量化依据）；⑤ 生成侧：prompt、证据校验、质量评审；⑥ 最后靠黄金集扩充与 bad case 回归持续收敛。**关键话术：90→99 不是改一个参数，是按评测链路逐层逼近，每一层都有数据支撑。**

### 25.8 基础 AI 八股（了解即可/可能被追问）

**61. Transformer 和 self-attention 的基本原理？**

一句话记忆点：**Q/K/V 三路，softmax(QKᵀ/√d)V 加权聚合，多头并行捕捉不同子空间**。

展开：self-attention 用 query 与 key 算相关性分数，除以 √d 防 softmax 饱和，再用分数加权 value；多头让不同头关注不同关系；位置编码注入顺序信息。本项目里 embedding 与 reranker 都是 Transformer 系模型，可顺带讲"我们调 API/自部署模型，不训练"。

**62. LoRA 微调了解吗？（项目没做怎么说）**

一句话记忆点：**LoRA 冻结原权重、只训低秩增量 ΔW=BA，参数量骤减**。

展开：LoRA（低秩适配）把权重增量分解成两个小矩阵相乘，训练参数少、显存占用低、可与原模型合并。本项目未做微调，回答模板："了解原理，但我们选 RAG + prompt 方案，因为知识更新频繁且要可溯源；如果问什么场景用 LoRA——领域风格固化、输出格式稳定性要求高、想让模型'像某类人说话'时考虑。"

**63. Attention 与长文本历史衰减？（淘天面经）**

一句话记忆点：**越长注意力越分散，早期信息被淹没；靠摘要/检索/显式记忆缓解**。

展开：长上下文里 attention 分数被拉平，早期信息权重衰减，模型"记不住开头"；多轮对话里旧轮影响变小。工程缓解：摘要压缩、检索切片（只带相关证据）、显式 memory、KV Cache 保留重要前缀。本项目多轮场景少，主要靠"每轮重新检索当前证据"降低对历史依赖。

**64. temperature / top-p / top-k 是什么？**

一句话记忆点：**temperature 管"稳不稳"，top-p/top-k 管"候选池大小"**。

展开：temperature 越低分布越尖锐、输出越确定，越高越多样；top-k 只保留概率最高的 k 个词；top-p 保留累积概率 p 的候选。本项目知识提取与问题生成一律 temperature=0（追求可复现、可审计），这是"生产 AI"与"玩具 AI"的一个典型区别。

**65. 为什么大模型有上下文长度限制？**

一句话记忆点：**attention 是 O(n²) 计算 + KV Cache 线性内存 + 训练长度上限**。

展开：注意力对所有 token 两两计算，长度增长计算量平方级；KV Cache 内存随长度线性增长；训练时模型只见过有限长度内的依赖关系，超长也未必用得好。应对：分块检索（本项目）、摘要、长上下文模型、稀疏/线性 attention。**面试延伸：所以"长文本能力"工程上靠的是检索与摘要，而不是单纯堆窗口。**

---

## 26. 演进规划与预留占位（Roadmap）

> 面试时请明确：以下功能**当前未开发或仅占位**，但架构已为其预留位置。

### 26.1 已预留的占位

| 功能 | 占位位置 | 说明 |
|---|---|---|
| 最终问答生成 / 分析中心 | `AnalysisPlaceholderView.vue`；`submit_analysis` 中 `TODO(3.1)`（仅状态流转，不调模型） | 分析提交已幂等、问题已锁定，等待接入分析任务 |
| 纪要编辑页 | `MeetingImportReviewPlaceholderView.vue`（文件名即占位，实际功能已大部分实现） | 命名保留，后续替换 |
| 历史会议管理入口 | 路由将 `/meetings` 全部重定向到核验控制台 | 旧 UI 兼容别名 |
| 通用检索/分析子模块 | `ai_tasks` 表已支持任意 `task_type`；`AiTaskStatus` 已含 PENDING_REVIEW | 新 AI 任务类型可直接复用任务框架 |

### 26.2 未开发功能（README 明确排除）

- **实时 ASR / 音频流**：当前只支持静态文件（PDF/DOCX/PPTX/TXT/MD/逐字稿 JSON）；
- **最终问答生成**（对已确认问题的 AI 回答 + 证据支撑）；
- **PPT / 图表 / 结构化纪要生成**；
- **GraphRAG**（多跳关系推理检索）；
- **任意自定义 Schema / Prompt 编辑器**：当前提取字段为白名单（`ALLOWED_TEMPLATE_FIELDS` 8 类）；
- **组织公共 KB 与跨 KB 检索**：当前检索严格限定单 KB；
- **多文件会议导入**：当前为单文件模式（README 明确）。

### 26.3 建议的接入路径（给面试官展示工程前瞻性）

1. **最终问答**：`submit_analysis` 接入 `AiTask(task_type=ANALYSIS)` → 复用租约/心跳/对账框架 + LangGraph（检索增强回答）→ 结果入 `meeting_analysis` 表；
2. **实时 ASR**：独立 ASR 服务 → 分片逐字稿写 `meeting_imports` 流式状态 → 沿用现有 Block/切块/向量管线；
3. **GraphRAG**：在 `Chunk` 之上增加实体/关系表与图索引，检索阶段做"RRF 召回 → 图扩展 → 重排"；
4. **Prompt/Schema 编辑器**：将 `ExtractionTemplateVersion.fields` 扩展为 JSON Schema + 系统提示模板，`extract_knowledge` 改为模板驱动。

---

## 27. 代码地图（关键文件索引）

| 关注点 | 文件 |
|---|---|
| 配置中心 | `backend/app/core/config.py` |
| 应用入口/异常/中间件 | `backend/app/main.py` |
| JWT/密码/鉴权 | `backend/app/core/security.py`、`core/auth.py` |
| 数据模型 | `backend/app/models/kb.py`、`models/meeting.py` |
| 迁移 | `backend/alembic/versions/`（11 个） |
| 切块算法 | `backend/app/ingestion/chunking.py` |
| 文档状态机 | `backend/app/ingestion/state.py` |
| 上传校验 | `backend/app/ingestion/validation.py` |
| 入库 LangGraph | `backend/app/worker/graph.py` |
| 问题生成 LangGraph | `backend/app/worker/question_graph.py` |
| 会议导入 Worker | `backend/app/worker/meeting_import.py` |
| 解析器 | `backend/app/worker/parser.py` |
| 知识提取 | `backend/app/worker/extraction.py` |
| Celery 配置/任务 | `backend/app/worker/celery_app.py`、`worker/tasks.py` |
| 进度 Stream | `backend/app/worker/progress.py` |
| 向量库封装 | `backend/app/services/vector_store.py` |
| 模型客户端 | `backend/app/services/model_client.py` |
| 问题生成服务 | `backend/app/services/question_generation.py`、`question_model_client.py` |
| 对象存储 | `backend/app/services/storage.py` |
| 评测 | `backend/scripts/*`、`backend/app/services/benchmark.py` |
| 可观测 | `backend/app/services/observability.py` |
| 会议导入 API | `backend/app/api/v1/meeting_imports.py` |
| 检索 API | `backend/app/api/v1/search.py` |
| 发布/审核 API | `backend/app/api/v1/knowledge_items.py` |
| 模型服务 | `model-service/app.py` |
| 前端路由 | `frontend/src/router/index.ts` |
| 前端认证 | `frontend/src/stores/auth.ts` |
| 纪要约校前端 | `frontend/src/views/meetings/MeetingImportReviewPlaceholderView.vue` |
| 部署 | `docker-compose.yml`、`docker-compose.models.yml`、`docker-compose.gpu.yml` |

---

## 28. 附录 A：环境变量速查

| 变量 | 默认 | 说明 |
|---|---|---|
| `DATABASE_URL` | asyncpg 本地 | 唯一权威库连接 |
| `JWT_SECRET_KEY` | 必填 ≥32 字符 | 令牌签名 |
| `ACCESS_TOKEN_MINUTES` / `REFRESH_TOKEN_DAYS` | 15 / 30 | 令牌生命周期 |
| `MINIO_*` / `MINIO_BUCKET` | minioadmin / medical-kb | 对象存储 |
| `REDIS_URL` / `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | redis://…/0,1,2 | Redis 三库分离 |
| `MILVUS_URI` / `MILVUS_COLLECTION` | http://milvus:19530 / medical_kb_records | 向量库 |
| `MODEL_SERVICE_URL` | http://bge-models:8100 | 模型服务 |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | 空 | OpenAI 兼容 LLM；`DEEPSEEK_API_KEY` 兜底 |
| `EMBEDDING_MODEL` / `EMBEDDING_VERSION` | BAAI/bge-m3 / bge-m3-v1 | 向量身份 |
| `BGE_DEVICE` / `BGE_BATCH_SIZE` / `BGE_EMBEDDING_STRATEGY` | cpu / 8 / single_pass_pool | 推理与策略 |
| `CHUNK_*` | target 700 / max 1000 / overlap 100 / threshold 0.65 | 切块参数 |
| `FUSION_TOP_K` / `RERANK_TOP_K` | 15 / 5 | 检索参数 |
| `MAX_UPLOAD_BYTES` / `MEETING_IMPORT_STALE_SECONDS` | 50MB / 3600s | 上传与租约 |
| `LANGFUSE_*` | 空 | 可观测（空则 no-op） |

---

## 29. 附录 B：API 一览

### 认证

`POST /auth/register`、`POST /auth/login`、`POST /auth/refresh`、`POST /auth/logout`、`GET /auth/me`

### 会议

`POST|GET /meetings`、`GET|PATCH|DELETE /meetings/{id}`、`PATCH /meetings/{id}/status`

### 会议导入与纪要约校

`GET /meeting-imports/config`、`POST /meeting-imports`、`GET|POST …/{import_id}/retry`、`POST …/cancel`、`GET …/review`、`GET|PATCH …/revisions/{revision_id}`、`POST …/find`、`POST …/replace`、`POST …/replace/{op}/undo`、`PATCH …/metadata`、`GET|POST …/vectorization`、`POST …/confirm`

### 核验与问题生成

`GET /meetings/{id}/verification`、`POST|PATCH|DELETE …/questions…`、`POST …/verification/confirm`、`POST …/analysis-submissions`、`GET|POST …/question-generation…`、`GET …/questions/{qid}/evidences`

### 知识库

`GET|POST /knowledge-bases`、`GET|PATCH|DELETE /knowledge-bases/{kb_id}`、`GET|POST …/templates`、`GET …/templates/{tid}`、`GET|POST …/documents`、`GET|DELETE …/documents/{did}`、`POST …/{did}/retry|reindex`、`GET …/{did}/blocks|chunks`、`GET|PATCH /knowledge-items…`、`POST …/{item}/review`、`POST …/documents/{did}/publish`

### 检索/任务/组织/评测

`POST /knowledge-bases/{kb_id}/search`、`GET /jobs/{job_id}`、`WS /ws/jobs/{job_id}`、`GET|POST|PATCH|DELETE /organizations/current/members…`、`GET|POST|GET /benchmarks…`

---

## 30. 附录 C：状态机速查

### 文档（单一生命周期 status）

```text
UPLOADED → PARSING → PARSED → CHUNKING → EMBEDDING → EXTRACTING
→ AWAITING_REVIEW → IN_REVIEW → PUBLISHED
任意节点 → FAILED（可重试/重解析/重索引）；软删 → DELETED（终态）
```

### 会议导入

```text
UPLOADED → PARSING → EXTRACTING_METADATA → READY_FOR_REVIEW → CONFIRMED
任意节点 → FAILED / CANCELLED
```

### 会议业务/核验/分析

```text
业务：draft → published → in_progress → completed → archived（cancelled 终态）
核验：pending → in_progress → confirmed（编辑回退）
分析：not_ready → ready → queued → processing → succeeded/failed（可取消）
```

### AI 任务

```text
QUEUED → RUNNING → PENDING_REVIEW → （人工核验后闭环）
     ↘ RETRYING → RUNNING（重试闭环）
任意 → FAILED / CANCELLED
```

---

## 31. 附录 D：核心数据表速查

| 表 | 一句话职责 |
|---|---|
| users / organizations / organization_memberships / refresh_tokens | 身份、租户、角色、刷新令牌摘要 |
| knowledge_bases / extraction_templates / extraction_template_versions | KB 与提取模板（版本冻结） |
| documents / document_blocks | 文档版本链与不可变原始 Block |
| meeting_imports | 单文件导入任务（租约/元数据/确认） |
| transcript_revisions / transcript_revision_blocks / batch_replace_operations | 可编辑修订、修订正文、批量替换快照 |
| chunks | 语义切块结果（权威正文片段） |
| knowledge_items / review_events | 知识项与审核事件 |
| ingestion_jobs / node_executions | 入库任务与节点幂等 |
| checkpoint_* | LangGraph 图状态持久化 |
| outbox_events | 事务性 Outbox |
| audit_events / retrieval_logs | 审计与检索留痕 |
| meetings | 会议主表（含核验/分析状态） |
| ai_tasks / meeting_questions / question_evidences | AI 任务、核验问题、问题证据 |
| benchmark_runs | 管理端评测任务 |

---

> **维护约定**：每个里程碑完成后更新本文档对应章节；新功能先在本文档「演进规划」登记再开发；任何状态机/表结构/接口变更必须同步附录 B/C/D，保持本文档作为唯一参考的有效性。
