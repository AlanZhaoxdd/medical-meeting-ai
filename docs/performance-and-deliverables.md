# 性能测试与成果交付数据口径说明

> 原则：每个数字都能指出「怎么测的、在什么数据/机器上测的、用哪个脚本/哪份证据」。
> 没有实测的指标一律标为「待环境实测」，不填数字、不编数字。

## 1. 可直接写进成果交付的一句话

> 在 1 场脱敏会议材料、15 份知识材料（14 份知识库材料 + 1 份会议材料）上完成
> 检索质量验证：GPU 环境「混合检索 + 重排」在 41 题黄金集上 hit@10 达 92.7%、
> MRR@10 为 0.761；纪要内容引用覆盖率 100%（无引用内容不进入确认版，由机制与
> 单元测试保证，可审计）；PPT/图表确定性渲染完整路径 p95 耗时为 128.6 ms
> （Windows 11 / Intel 6 核 / Python 3.12.13，30 次迭代）。

> 「单场会议人工整理与复核时间由 X 分钟缩短至 Y 分钟」当前仓库没有实测基线，
> 不能直接写。第 5 节给出了可执行的口径与测试步骤，拿到基线后按公式填充。

## 2. 指标总览

| 指标 | 当前值 | 状态 | 证据文件 |
| --- | --- | --- | --- |
| 验证会议场次（脱敏数据） | 1 场（心血管领域专家顾问会） | 已由黄金集确认 | `backend/eval_set_nn.json` |
| 验证知识材料份数 | 15 份（14 份知识库材料 + 1 份会议材料） | 已由黄金集确认 | `backend/eval_set.json`、`backend/eval_set_nn.json` |
| 黄金题规模 | 188 题（去重后 179 题） | 已确认 | 三个 `eval_set*.json` |
| 检索质量 hit@10（GPU，hybrid_rerank） | 92.68% | 已实测 | `backend/report_nn_gpu.json` |
| 检索质量 MRR@10（GPU，hybrid_rerank） | 0.761 | 已实测 | `backend/report_nn_gpu.json` |
| 纪要引用覆盖率 | 100%（机制保证） | 代码 + 单测背书 | `backend/app/worker/analysis_graph.py`、`backend/tests/test_analysis_units.py` |
| PPT/图表渲染 p95（完整路径） | 128.58 ms | 已实测 | `backend/report_chart_ppt.json` |
| 检索延迟 p50/p95/p99 + QPS | 待环境实测 | 待跑 | `bench_search_latency.py` 输出 |
| 嵌入吞吐 texts/sec | 待环境实测 | 待跑 | `bench_embedding.py` 输出 |
| Ragas 端到端质量（faithfulness/context_recall 等） | 待环境实测 | 待跑 | `eval_ragas.py` 输出 |
| 人工整理与复核时间对比 | 待基线 + 实测 | 待填 | 第 5 节口径 |

## 3. 各指标怎么来的

### 3.1 验证规模：x 场会议、y 份知识材料

**口径**：以检索评测黄金集为准。黄金集由 `build_eval_set.py` 从已入库的知识库
chunk 中按文档均匀采样，用 LLM 为每个 chunk 生成 1~2 个「只有该 chunk 能回答」
的问题，并记录 `expected_chunk_id` / `expected_document_id` / `kb_id`。因此：

- **材料数 y** = 黄金集里 `expected_document_id` 去重数；
- **会议数 x** = 黄金集问题锚定的会议材料数（会议材料本身是脱敏/模拟数据）。

统计命令（任意 Python 环境）：

```python
import json
from pathlib import Path

all_docs, all_queries = set(), []
for f in ["eval_set.json", "eval_set_indexed.json", "eval_set_nn.json"]:
    d = json.loads(Path("backend", f).read_text(encoding="utf-8"))
    entries = d["entries"]
    all_queries.extend(e["query"] for e in entries)
    all_docs.update(e["expected_document_id"] for e in entries)
print("questions:", len(all_queries), "unique:", len(set(all_queries)))
print("unique documents:", len(all_docs))
```

结果：188 题 / 去重 179 题 / 15 份材料（14 份知识库材料 + 1 份会议材料）。
`eval_set_indexed.json` 的 3 份材料是 `eval_set.json` 14 份的子集，去重后不计入新增。
`eval_set_nn.json` 的 41 题全部锚定同一场「心血管领域专家顾问会」材料。

另外，`backend/eval_data/eval-datasets-1786019104793.json` 有 761 条 QA 黄金集
（开放题 + 标准答案），用于 Ragas 端到端评测，可支撑「N 条业务问答上完成验证」。

### 3.2 检索质量：Recall@k / MRR（引用可溯源性的支撑）

**口径**：`eval_retrieval.py` 对同一份黄金集、同一台机器，依次跑四种检索变体
（dense_only / sparse_only / hybrid / hybrid_rerank），统计 `hit@1/3/5/10`
与 `mrr@10`。生产路径是「混合检索（dense+sparse+RRF 融合）→ 重排」。

命令：

```bash
cd backend
uv run python scripts/eval_retrieval.py --golden eval_set_nn.json --output report.json
```

已有实测（GPU，`report_nn_gpu.json`，41 题）：

| 变体 | hit@1 | hit@3 | hit@5 | hit@10 | mrr@10 |
| --- | --- | --- | --- | --- | --- |
| hybrid_rerank | 65.85% | 85.37% | 92.68% | 92.68% | 0.761 |

注意：仓库里的 `report.json`（111 题）是早期未完整建索引时的结果，rerank 全为 0，
不能作为最终质量结论；交付引用请用 `report_nn_gpu.json`（标注 CUDA + 41 题）
或重新跑同集同机的新报告。

### 3.3 纪要引用覆盖率

项目对「引用」有硬性机制：AI 分析结果的每个内容模块必须带 `[n]` 引用锚点，
`validate_and_persist` 会把「有内容但无引用」的模块直接丢弃（不进入确认版纪要），
并有单元测试背书（`test_analysis_units.py`、`test_meeting_chat_units.py`）。

因此有两个可写口径：

1. **机制口径（可直接写 100%）**：确认版纪要中的**正文内容模块** 100% 带
   可溯源引用；无引用正文在入库阶段被拒绝保留。这句话由代码行为 + 单测保证，
   可审计。注意边界：它保证的是「模块级」，不等于「每一句都有锚点」；
   纯条目型模块（如行动项列表）在提示词中同样要求引用，但校验器不强制。
2. **实测口径（推荐同时给出数字）**：在真实 analysis run 上统计
   `引用覆盖率 = 带引用锚点的正文模块数 / 总正文模块数`，并区分
   「段落级覆盖率」（正文段落中带 `[n]` 锚点的比例，通常低于 100%）。
   仓库提供离线统计脚本：

```bash
cd backend
uv run python scripts/measure_citation_coverage.py \
  --modules analysis_run.json --sources sources.json --output coverage_report.json
```

也可直接查数据库：

```sql
SELECT
  COUNT(*) FILTER (WHERE jsonb_array_length(modules->'citations') > 0) AS cited,
  COUNT(*) AS total,
  ROUND(100.0 * COUNT(*) FILTER (WHERE jsonb_array_length(modules->'citations') > 0)
    / NULLIF(COUNT(*), 0), 2) AS coverage_pct
FROM meeting_analysis_runs, jsonb_array_elements(modules) AS modules
WHERE status = 'SUCCEEDED';
```

> 口径红线：不要在没跑真实分析任务前写「覆盖率 92%」这类数字；要么写机制保证的
> 100%（注明「正文模块级」），要么在部署环境跑上面的脚本/SQL 统计后填实测值。
> 句子级或段落级覆盖率不要与模块级覆盖率混用。

### 3.4 PPT / 图表生成 p95 耗时

**口径**：`bench_chart_ppt.py` 只测导出链路中**确定性渲染阶段**（不依赖数据库与
外部 LLM）：条形图 PNG（8 类目）、饼图 PNG（5 类目）、8 页 PPTX 打包、以及
「2 张图表 PNG + PPTX」的完整渲染路径。统计 p50/p95/p99、均值与 ops/sec。

命令：

```bash
cd backend
uv run python scripts/bench_chart_ppt.py --iterations 30 --warmup 3
```

实测（`backend/report_chart_ppt.json`，Windows 11 / Intel 6 核 / Python 3.12.13 /
PIL 12.2.0 / python-pptx 1.0.2，30 次迭代 + 3 次预热）：

| 阶段 | p50 | p95 | p99 | 均值 |
| --- | --- | --- | --- | --- |
| 条形图 PNG | 20.24 ms | 22.62 ms | 23.16 ms | 20.39 ms |
| 饼图 PNG | 23.07 ms | 33.03 ms | 36.82 ms | 24.49 ms |
| PPTX 打包（8 页，含图） | 55.39 ms | 61.52 ms | 66.62 ms | 55.90 ms |
| 完整路径（2 图 + PPTX） | 98.74 ms | 128.58 ms | 173.11 ms | 102.80 ms |

写成果时建议写：**PPT/图表确定性渲染完整路径 p95 为 128.6 ms**，并注明机器环境
与迭代次数。若要宣称全链路（含 LLM 大纲生成）的 p95，需在部署环境记录
`export_tasks` 的任务时间戳后再补测。

### 3.5 单场会议「人工整理与复核时间」对比

这是业务口径，公式：

```text
节省时间 = 人工基线 T0 − AI 辅助后 T1
节省率   = (T0 − T1) / T0
```

- **T0（人工基线）**：业务侧「人工整理一份会议纪要 + 复核」的历史平均时长，
  来自访谈记录或工时系统（例如团队给出 60–120 分钟/场，需有出处）。
- **T1（AI 辅助后）** = AI 纪要初稿生成时长 + 人工确认/修改时长：
  - AI 部分实测：`ai_tasks` 表 `created_at → completed_at`
    （或 analysis run 的 start/end 时间戳），也可由前端操作日志计时；
  - 人工部分：操作日志中「确认纪要」页面停留/编辑时长，或直接按场次记录。

```sql
-- AI 纪要初稿生成耗时（秒）
SELECT meeting_id,
       EXTRACT(EPOCH FROM (completed_at - started_at)) AS ai_seconds
FROM ai_tasks
WHERE task_type = 'analysis' AND status = 'SUCCEEDED';
```

> 当前仓库没有客户环境的人工基线数据，因此这行必须留待实测后填写，不能编造。
> 可以写「按上述口径在 N 场会议上实测」，等数字齐了再填「由 X 分钟缩短至 Y 分钟，
> 节省 Z%」。

### 3.6 检索延迟与嵌入吞吐（待环境实测）

需要 PostgreSQL + Milvus + BGE 模型服务（Docker Compose 全套）就绪后执行：

```bash
cd backend
# 检索链路分阶段 p50/p95/p99 + QPS（embed → search → db_fetch → rerank → total）
uv run python scripts/bench_search_latency.py --golden eval_set_nn.json --iterations 100

# 嵌入吞吐（batch 1/4/8/16 对比）
uv run python scripts/bench_embedding.py --golden eval_set_nn.json --batch 1,4,8,16
```

产出 `search_latency_report.json` / `embedding_benchmark.json`，报告中自带
environment（device / model / strategy），引用时注明机器与数据集即可。

### 3.7 Ragas 端到端质量（待环境实测）

761 条 QA 黄金集已就绪；在部署环境用真实会议 ID 执行：

```bash
cd backend
uv run python scripts/eval_ragas.py \
  --meeting <MEETING_ID> \
  --dataset eval_data/eval-datasets-1786019104793.json \
  --max-items 50 --output report_ragas.json
```

输出 faithfulness / answer_relevancy / context_precision / context_recall /
semantic_similarity。其中 `context_recall` 可直接支撑「答案引用覆盖」类表述。

## 4. 复现清单（按顺序）

1. 起服务：`docker compose -f docker-compose.yml -f docker-compose.models.yml up --build`
   （模型服务默认 GPU；无 GPU 主机回退 CPU：`TORCH_INDEX_URL= BGE_DEVICE=cpu \
   docker compose -f docker-compose.yml -f docker-compose.models.yml up --build`）
2. 建索引并跑检索质量：`eval_retrieval.py --golden eval_set_nn.json`
3. 检索延迟：`bench_search_latency.py --golden eval_set_nn.json --iterations 100`
4. 嵌入吞吐：`bench_embedding.py --golden eval_set_nn.json --batch 1,4,8,16`
5. Ragas：`eval_ragas.py --meeting <MEETING_ID> --dataset ... --max-items 50`
6. PPT/图表渲染（无需服务）：`bench_chart_ppt.py --iterations 30`
7. 引用覆盖率 / 时间对比：按第 3.3、3.5 节 SQL 与口径统计

## 5. 证据文件清单

| 文件 | 作用 |
| --- | --- |
| `backend/report_chart_ppt.json` | PPT/图表渲染 p95（本次新实测） |
| `backend/report_nn_gpu.json` | 检索质量 GPU 报告（41 题） |
| `backend/report_indexed.json` | 检索质量 CPU 报告（36 题，索引化后） |
| `backend/eval_set.json` / `eval_set_indexed.json` / `eval_set_nn.json` | 检索黄金集 |
| `backend/eval_data/eval-datasets-1786019104793.json` | Ragas QA 黄金集（761 条） |
| `backend/scripts/bench_chart_ppt.py` | 新增的 PPT/图表渲染基准脚本 |
| `backend/scripts/measure_citation_coverage.py` | 纪要引用覆盖率离线统计脚本 |
| `backend/tests/test_analysis_units.py` 等 | 引用机制单测背书 |

## 6. 口径红线（面试/交付时被追问必须能答）

- 所有数字必须能说出「数据集规模 + 机器环境 + 脚本/口径」，否则不写。
- CPU 与 GPU 结果分开标注；优化对比必须同脚本、同黄金集、同机器。
- `report.json`（111 题、rerank 全 0）是早期未建索引的基线，不能作为最终质量。
- 「人工时间缩短」类结论需要人工基线出处 + 实测 T1，二者缺一不可。
