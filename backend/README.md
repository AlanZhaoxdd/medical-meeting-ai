# FastAPI 后端

KB v1 后端使用 Python 3.11、FastAPI、SQLAlchemy Async 与 PostgreSQL。PostgreSQL 是用户、组织、会议、KB、文档、知识、任务、审计、Outbox 和 LangGraph Checkpoint 的唯一权威数据库。

完整架构、Docker Compose、CPU/GPU 模型说明与验收流程见仓库根目录 [README](../README.md)。

本地仅启动 API：

```bash
cp .env.example .env
uv sync --dev
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

需另行提供 PostgreSQL、Redis、MinIO、Milvus、Celery Worker 和 BGE 模型服务。推荐从仓库根目录运行完整 Compose。

质量检查：

```bash
uv run ruff check .
uv run mypy app
uv run pytest
```

API 文档运行后位于 `http://localhost:8000/docs`。
