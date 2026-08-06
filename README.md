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

2. 启动基础设施、应用和真实 CPU 模型：

   ```bash
   docker compose --profile models up --build
   ```

3. 打开：

   - 管理端：http://localhost:5173
   - OpenAPI：http://localhost:8000/docs
   - MinIO Console：http://localhost:9001

首次模型启动会下载权重并占用较多磁盘空间。模型尚未就绪时，上传原件仍会安全保存，任务会明确报告模型服务错误并按退避策略重试。

## GPU

已安装 NVIDIA Container Toolkit 的 Linux 主机可运行：

```bash
docker compose --profile models -f docker-compose.yml -f docker-compose.gpu.yml up --build
```

这会将 `BGE_DEVICE` 设为 `cuda`、为模型服务申请一张 GPU，并用 CUDA 版 torch
重新构建模型服务镜像（默认 CPU 锁定的 torch wheel 不支持 GPU）。Windows 用户可在
Docker Desktop（WSL2 后端）+ NVIDIA 驱动下使用同一命令；先在 WSL 中执行
`nvidia-smi` 确认驱动可见。CPU/GPU 的模型名、设备和 batch size 均可由环境变量覆盖。

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

会议智能问答通过 `POST /api/v1/meetings/{meeting_id}/ai-chat` 提供：基于确认版会议纪要（可选叠加已发布知识库）做混合检索，由 LLM 生成带引用来源的答案；材料不足时返回 `INSUFFICIENT_CONTEXT` 而不编造。

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
