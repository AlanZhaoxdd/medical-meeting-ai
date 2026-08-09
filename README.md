# 医药会议知识库 KB v1

面向医药会议资料的可审核知识入库与证据检索工作台。现有 PostgreSQL 会议 CRUD 保持兼容，新增 KB 项目容器、文档解析、知识审核和混合检索闭环。

## 架构

- **PostgreSQL 是唯一权威数据库**：会议、用户、组织、KB、文档元数据、Block、Chunk 正文、知识项、模板版本、任务、审计、Outbox 与 LangGraph Checkpoint 全部存于 PostgreSQL。
- **MinIO**：只保存原始文件；对象路径不使用用户目录。
- **Milvus**：只保存检索向量与过滤字段，正文始终从 PostgreSQL 回填并再次做组织/KB/发布状态校验。
- **Redis / Celery**：耗时入库任务、结果后端与进度 Stream。
- **Docling**：PDF、DOCX、PPTX 的主解析器；TXT/Markdown 与标准逐字稿 JSON 使用确定性解析器。
- **BGE 模型服务**：独立加载 `BAAI/bge-m3` 和 `BAAI/bge-reranker-v2-m3`，API/Worker 不加载大模型。
- **LangChain / LangGraph**：OpenAI 兼容结构化知识提取；确定性 StateGraph 使用 PostgreSQL Checkpointer，在人工审核门暂停和恢复。

## 本地启动

1. 准备配置：

   ```bash
   cp .env.example .env
   ```

   至少修改 `JWT_SECRET_KEY`、`MINIO_ROOT_PASSWORD`，并填写 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`。使用 DeepSeek 时也可直接提供 `DEEPSEEK_API_KEY`，程序会将其作为 `LLM_API_KEY` 的回退值。

2. 启动基础设施、应用和模型服务（默认 CPU 推理）：

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.models.yml up --build
   ```

3. 打开：

   - 管理端：http://localhost:5173
   - OpenAPI：http://localhost:8000/docs
   - MinIO Console：http://localhost:9001

## 会议成果导出

AI 纪要分析页新增“成果导出”入口，支持文字版纪要（DOCX/PDF）、6～8 页可编辑
PPTX（先预览/编辑大纲再生成）与基于证据的条形图/饼图（PNG/SVG，可插入 PPT）。

- 导出任务统一异步执行（`PENDING → ANALYZING → GENERATING → RENDERING →
  COMPLETED`），进度通过 `GET /exports/{export_id}` 轮询；失败可重试、运行中可取消。
- 文字版与 PPT 均只使用已确认的 AI 纪要内容，不调用 LLM 重新生成纪要正文；
  PPT 大纲与图表分类调用 LLM，但所有数值由后端按 `speakerId/sourceId` 聚合计算。
- 文件保存在 MinIO（`exports/{meeting_id}/{export_id}.*`），PostgreSQL 只保存
  任务元数据与配置快照；下载返回短期预签名 URL。
- 生成工具：文字版使用 `python-docx`（DOCX）与 `reportlab`（PDF），PPT 使用
  `python-pptx` 确定性渲染，图表使用 ECharts 预览、PIL 渲染 PPT 内嵌 PNG。

开发环境更新依赖后执行一次 `uv sync`（会同步 `uv.lock` 中的 `reportlab`）。

首次模型启动会下载权重并占用较多磁盘空间。模型尚未就绪时，上传原件仍会安全保存，任务会明确报告模型服务错误并按退避策略重试。

## GPU

GPU 启动与 CPU 使用同一套结构：基础栈 `docker-compose.yml` 只编排业务服务，
`docker-compose.models.yml` 是模型服务（CPU 默认），`docker-compose.gpu.yml`
是 GPU 专用 overlay，负责切换 CUDA 版 torch、设置 `BGE_DEVICE=cuda` 并申请 1 张
NVIDIA GPU。三个文件通过 Compose merge 合并，业务镜像/依赖关系完全一致。

已安装 NVIDIA Container Toolkit 的主机运行：

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.models.yml \
  -f docker-compose.gpu.yml \
  up --build
```

不带 `docker-compose.gpu.yml` 即为 CPU 推理（`docker-compose.models.yml` 中的
`BGE_DEVICE` 默认 `cpu`）。`TORCH_INDEX_URL` 默认指向 cu126 镜像；宿主机驱动支持
更新的 CUDA 运行时时可覆盖，例如：

```bash
TORCH_INDEX_URL=https://mirror.sjtu.edu.cn/pytorch-wheels/cu128 \
docker compose -f docker-compose.yml -f docker-compose.models.yml \
  -f docker-compose.gpu.yml up --build
```

Windows 用户可在 Docker Desktop（WSL2 后端）+ NVIDIA 驱动下使用同一命令；先在
WSL 中执行 `nvidia-smi` 确认驱动可见。模型名、设备、batch size 均可由环境变量覆盖
（`BGE_EMBEDDING_MODEL`、`BGE_DEVICE`、`BGE_BATCH_SIZE` 等）。

## 向量化策略

`BGE_EMBEDDING_STRATEGY` 控制文档入库时的嵌入方式：

- `single_pass_pool`（默认）：对语义单元只嵌入一次（dense + sparse），chunk 向量由所属单元的 dense 均值与 sparse 词权重并集聚合而来，分块边界与 `two_pass` 一致，但省去对最终 chunk 的第二次完整编码。
- `two_pass`：保留旧行为，语义单元先算 dense 用于分块，最终 chunk 再算一次 dense + sparse 入库。

策略名称会写入 `embedding_version`（如 `bge-m3-v1@BAAI/bge-m3:single_pass_pool`）。切换策略后旧向量与新向量不混用，已入库文档需要重新索引。

## 处理与审核

```text
保存原件 → 解析 → 标准化 Block → 语义 Chunk → Dense/Sparse 向量
→ 结构化知识提取 → 证据校验 → 等待人工审核 → 发布 → 正式检索
```

每个有副作用的 Graph 节点使用 `job_id + node_name + input_version` 幂等键。Graph State 仅保存 ID、状态和小摘要。发布和删除通过 PostgreSQL Outbox 幂等同步 Milvus；Celery Beat 每 30 秒对账。

详细状态机见 [docs/state-machine.md](docs/state-machine.md)，数据结构见 [docs/data-model.md](docs/data-model.md)，API 清单见 [docs/api.md](docs/api.md)。

会议智能问答通过 `POST /api/v1/meetings/{meeting_id}/ai-chat` 提供：基于确认版会议纪要（可选叠加已发布知识库）做混合检索，由 LLM 生成带引用来源的答案；材料不足时返回 `INSUFFICIENT_CONTEXT` 而不编造。会话与每轮问答落库（`chat_conversations` / `chat_messages`）用于审计；带 `conversation_id` 的追问会先用最近几轮历史做指代改写（仅用于检索，答案仍以本轮证据为准），改写前后的问题都会留存。

## 测试与检查

```bash
cd backend
uv sync --dev
uv run pytest
uv run ruff check .
uv run mypy app

cd ../
npm test
npm run lint
npm run build
docker compose --env-file .env.example config --quiet
```

外部模型在单元测试中应使用 Mock；真实 PostgreSQL/MinIO/Milvus/Redis/Celery 的端到端验证使用 Docker Compose 环境。

## 数据卷、备份与清理

Compose 使用 `postgres_data`、`minio_data`、`milvus_data`、`redis_data`、`etcd_data` 和 `huggingface_models`。生产备份至少包含：

- PostgreSQL：定期 `pg_dump`，这是业务事实来源。
- MinIO：启用版本化并同步对象 Bucket。
- Milvus：可从 PostgreSQL Chunk 重新向量化，不应替代 PostgreSQL 备份。

普通删除是软删除。owner/admin 可通过 `purge=true` 彻底清除，并同步移除 MinIO 与 Milvus 数据；此操作不可恢复，应在备份后执行。

## 本迭代明确不含

实时 ASR/音频流、PPT/图表/结构化纪要生成、GraphRAG、任意自定义 Schema/Prompt 编辑器、组织公共 KB 和跨 KB 检索。

## 官方接口参考

- [Docling DocumentConverter](https://docling-project.github.io/docling/reference/document_converter/)
- [Milvus 多向量混合检索](https://milvus.io/docs/multi-vector-search.md)
- [LangGraph PostgreSQL Checkpointer](https://docs.langchain.com/oss/python/langgraph/add-memory)
