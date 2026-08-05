# 检索评测与基准脚本

这些脚本用于量化 RAG 系统的检索质量、检索延迟和嵌入吞吐，产出可写进简历、
可复现的 JSON 报告。所有脚本都要在 `backend/` 目录下运行（保证 `app` 包可导入）：

```bash
cd backend
uv run python scripts/<script>.py ...
```

## 1. 黄金集生成（数据准备）

```bash
uv run python scripts/build_eval_set.py --kb <KB_ID> --count 60 --output eval_set.json
```

- 从知识库已入库 chunk 中按文档均匀采样，用 LLM（`LLM_BASE_URL/LLM_API_KEY/LLM_MODEL`）
  为每个 chunk 生成 1~2 个"只有该 chunk 能回答"的问题；
- `--dry-run` 模式只导出 chunk，供人工写问题；
- 产出 `entries` 数组，每项 `{query, expected_chunk_id, expected_document_id, kb_id}`，
  这是 Recall@k / MRR 的 ground truth。

建议：生成后人工抽检 10%，删掉跨 chunk 可回答的问题。

## 2. 离线检索质量（Recall@k / MRR）

```bash
uv run python scripts/eval_retrieval.py --golden eval_set.json --output report.json
```

同一批 query 分别跑四种检索变体并对比：

| 变体 | 含义 |
|---|---|
| dense_only | 只用 BGE-M3 dense 向量 |
| sparse_only | 只用 BGE-M3 lexical 稀疏向量 |
| hybrid | dense + sparse + RRF 融合（生产路径） |
| hybrid_rerank | hybrid 候选再过重排模型（生产路径） |

指标：`hit@1/3/5/10`、`mrr@10`。参数可用 `--dense-limit`、`--ef`、`--rerank-top`
等控制，便于做参数 A/B。

## 3. 检索延迟（p50/p95/p99 + QPS）

```bash
uv run python scripts/bench_search_latency.py --golden eval_set.json --iterations 100
```

镜像生产检索链路（嵌入 → Milvus 混合检索 → PG 取正文 → 重排），分阶段统计
`embed / search / db_fetch / rerank / total` 的 p50/p95/p99、均值与 QPS。

## 4. 嵌入吞吐（texts/sec）

```bash
uv run python scripts/bench_embedding.py --kb <KB_ID> --batch 1,4,8,16
```

也支持 `--golden` 或 `--corpus` 提供语料。批量对比不同 `BGE_BATCH_SIZE`
或 CPU/GPU 的吞吐，直接量化入库优化效果。

## 简历口径建议

- 每份报告 JSON 已带环境元数据（device / model / strategy），引用时注明数据集规模
  与硬件（如 "M4 CPU，60 条内部黄金集"、"RTX 2060 Super，150 条黄金集"）；
- 优化前后对比用同一脚本、同一黄金集、同一机器，控制变量后才写"提升 2x"；
- 检索质量基线对比（dense vs sparse vs hybrid vs +rerank）是最有说服力的素材。
