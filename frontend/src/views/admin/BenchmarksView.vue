<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { benchmarksApi } from '@/api/benchmarks'
import { useAuthStore } from '@/stores/auth'
import { canAccessSettings } from '@/utils/kb'
import type {
  BenchmarkEnvironment,
  BenchmarkKind,
  BenchmarkRun,
  BenchmarkStatus,
} from '@/types/benchmark'

const auth = useAuthStore()
const canManage = computed(() => canAccessSettings(auth.user?.role))
const runs = ref<BenchmarkRun[]>([])
const loading = ref(false)
const environment = ref<BenchmarkEnvironment | null>(null)
const dialog = ref(false)
const creating = ref(false)
const detail = ref<BenchmarkRun | null>(null)
const detailDialog = ref(false)
let pollTimer: number | undefined

const kindOptions: Array<{ value: BenchmarkKind; label: string }> = [
  { value: 'retrieval_quality', label: '检索质量（Recall@k / MRR）' },
  { value: 'search_latency', label: '检索延迟（p50/p95/p99 + QPS）' },
  { value: 'embedding_throughput', label: '嵌入吞吐（texts/sec）' },
  { value: 'ragas_quality', label: 'RAG 端到端质量（Ragas）' },
]

const kindLabels = Object.fromEntries(kindOptions.map((item) => [item.value, item.label]))
const ragasMetricOptions: Array<{ value: string; label: string }> = [
  { value: 'faithfulness', label: '忠实度 faithfulness' },
  { value: 'answer_relevancy', label: '答案相关性 answer_relevancy' },
  { value: 'context_precision', label: '上下文精度 context_precision' },
  { value: 'context_recall', label: '上下文召回 context_recall' },
  { value: 'semantic_similarity', label: '语义相似度 semantic_similarity' },
]
const statusLabels: Record<BenchmarkStatus, string> = {
  PENDING: '排队中',
  RUNNING: '运行中',
  COMPLETED: '完成',
  FAILED: '失败',
  DISPATCH_FAILED: '派发失败',
}

const form = reactive({
  kind: 'retrieval_quality' as BenchmarkKind,
  name: '',
  goldenText: '',
  queriesText: '',
  corpusText: '',
  batchSizes: '1,4,8,16',
  iterations: 10,
  topK: '1,3,5,10',
  rerank: true,
  ragasDatasetFile: 'eval-datasets-1786019104793.json',
  ragasEntriesText: '',
  meetingId: '',
  scope: 'MEETING_AND_KB',
  ragasMetrics: [
    'faithfulness',
    'answer_relevancy',
    'context_precision',
    'context_recall',
    'semantic_similarity',
  ],
  maxItems: 0,
  seed: 42,
})

async function load() {
  loading.value = true
  try {
    const [list, env] = await Promise.all([benchmarksApi.list(), benchmarksApi.environment()])
    runs.value = list
    environment.value = env
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '评测记录加载失败')
  } finally {
    loading.value = false
  }
}

function parseNumbers(raw: string): number[] {
  const values = raw
    .split(',')
    .map((item) => Number(item.trim()))
    .filter((item) => Number.isInteger(item) && item > 0)
  if (!values.length) throw new Error('数字列表格式无效')
  return values
}

function buildParams(): Record<string, unknown> {
  if (form.kind === 'retrieval_quality') {
    let parsed: unknown
    try {
      parsed = JSON.parse(form.goldenText)
    } catch (error) {
      throw new Error(`黄金集 JSON 解析失败：${error instanceof Error ? error.message : '语法错误'}`)
    }
    const entries = Array.isArray(parsed) ? parsed : (parsed as { entries?: unknown }).entries
    if (!Array.isArray(entries) || !entries.length) throw new Error('黄金集需要非空 entries 列表')
    const valid = entries.every(
      (entry) =>
        typeof entry === 'object' &&
        entry !== null &&
        Boolean((entry as Record<string, unknown>).query) &&
        Boolean((entry as Record<string, unknown>).expected_chunk_id) &&
        Boolean((entry as Record<string, unknown>).kb_id),
    )
    if (!valid) throw new Error('黄金集每条需要 query / expected_chunk_id / kb_id')
    return { entries, top_ks: parseNumbers(form.topK), rerank: form.rerank }
  }
  if (form.kind === 'search_latency') {
    const queries = form.queriesText.split('\n').map((item) => item.trim()).filter(Boolean)
    if (!queries.length) throw new Error('请至少输入一条 query')
    return { queries, iterations: form.iterations, rerank: form.rerank }
  }
  if (form.kind === 'ragas_quality') {
    if (!form.meetingId.trim()) throw new Error('请填写会议 ID（meeting_id）')
    if (!form.ragasDatasetFile.trim() && !form.ragasEntriesText.trim()) {
      throw new Error('请填写测试集文件，或直接粘贴 entries JSON')
    }
    const params: Record<string, unknown> = {
      meeting_id: form.meetingId.trim(),
      scope: form.scope,
      metrics: form.ragasMetrics,
      max_items: form.maxItems,
      seed: form.seed,
    }
    if (form.ragasDatasetFile.trim()) params.dataset_file = form.ragasDatasetFile.trim()
    if (form.ragasEntriesText.trim()) {
      let parsed: unknown
      try {
        parsed = JSON.parse(form.ragasEntriesText)
      } catch (error) {
        throw new Error(`测试集 JSON 解析失败：${error instanceof Error ? error.message : '语法错误'}`)
      }
      const entries = Array.isArray(parsed) ? parsed : (parsed as { entries?: unknown }).entries
      if (!Array.isArray(entries) || !entries.length) throw new Error('测试集需要非空 entries 列表')
      if (
        !entries.every(
          (entry) =>
            typeof entry === 'object' &&
            entry !== null &&
            Boolean((entry as Record<string, unknown>).question) &&
            Boolean((entry as Record<string, unknown>).correctAnswer),
        )
      ) {
        throw new Error('测试集每条需要 question / correctAnswer')
      }
      params.entries = entries
    }
    return params
  }
  const texts = form.corpusText.split('\n').map((item) => item.trim()).filter(Boolean)
  if (!texts.length) throw new Error('请至少输入一条文本')
  return { texts, batch_sizes: parseNumbers(form.batchSizes), iterations: form.iterations }
}

async function create() {
  let params: Record<string, unknown>
  try {
    params = buildParams()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '参数无效')
    return
  }
  creating.value = true
  try {
    const run = await benchmarksApi.create({
      kind: form.kind,
      name: form.name.trim(),
      params,
    })
    runs.value.unshift(run)
    dialog.value = false
    ElMessage.success('评测任务已启动')
    startPolling(run.id)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '创建失败')
  } finally {
    creating.value = false
  }
}

function stopPolling() {
  if (pollTimer) window.clearInterval(pollTimer)
  pollTimer = undefined
}

function startPolling(id: string) {
  stopPolling()
  pollTimer = window.setInterval(async () => {
    try {
      const run = await benchmarksApi.get(id)
      const index = runs.value.findIndex((item) => item.id === id)
      if (index >= 0) runs.value[index] = run
      if (['COMPLETED', 'FAILED', 'DISPATCH_FAILED'].includes(run.status)) {
        stopPolling()
        if (run.status === 'COMPLETED') ElMessage.success('评测完成')
        else ElMessage.warning(`评测失败：${run.error_message || '未知错误'}`)
        await load()
      }
    } catch {
      stopPolling()
    }
  }, 3000)
}

function summary(run: BenchmarkRun): string {
  const metrics = run.metrics
  if (!metrics) return statusLabels[run.status]
  if (run.kind === 'retrieval_quality') {
    const results = metrics.results as Record<string, Record<string, number>> | undefined
    const best = results?.hybrid_rerank ?? results?.hybrid
    if (!best) return '—'
    return `hybrid hit@5 ${(best['hit@5'] * 100).toFixed(1)}% · MRR@10 ${best['mrr@10']}`
  }
  if (run.kind === 'search_latency') {
    const stages = metrics.stages_ms as Record<string, { p95: number; qps: number }> | undefined
    const total = stages?.total
    return total ? `p95 ${total.p95}ms · QPS ${total.qps}` : '—'
  }
  if (run.kind === 'ragas_quality') {
    const m = metrics.metrics as Record<string, number> | undefined
    if (!m) return '—'
    const fmt = (value: number | undefined) =>
      typeof value === 'number' ? `${(value * 100).toFixed(1)}%` : '—'
    return `faithfulness ${fmt(m.faithfulness)} · context_recall ${fmt(m.context_recall)} · similarity ${fmt(m.semantic_similarity)}`
  }
  const batches = metrics.batch_ms as Record<string, { texts_per_second: number }>
  const best = Object.values(batches).reduce(
    (max, item) => Math.max(max, item.texts_per_second),
    0,
  )
  return best ? `${best} texts/sec` : '—'
}

function showDetail(run: BenchmarkRun) {
  detail.value = run
  detailDialog.value = true
}

const detailJson = computed(() =>
  detail.value
    ? JSON.stringify(
        {
          environment: detail.value.environment,
          params: detail.value.params,
          metrics: detail.value.metrics,
        },
        null,
        2,
      )
    : '',
)

onMounted(load)
onBeforeUnmount(stopPolling)
</script>

<template>
  <section class="benchmarks-page">
    <header class="page-hero">
      <div>
        <p class="eyebrow">BENCHMARKS</p>
        <h1>性能评测</h1>
        <p>量化检索质量、延迟与嵌入吞吐，配置 A/B 结果可复现。</p>
      </div>
      <el-button v-if="canManage" type="primary" size="large" :icon="Plus" @click="dialog = true">
        运行评测
      </el-button>
    </header>

    <div v-if="environment" class="env-chips">
      <el-tag effect="plain">{{ environment.device }}</el-tag>
      <el-tag effect="plain">{{ environment.embedding_model }}</el-tag>
      <el-tag effect="plain">{{ environment.embedding_strategy }}</el-tag>
      <el-tag effect="plain">batch {{ environment.bge_batch_size }}</el-tag>
    </div>

    <div class="list-toolbar">
      <h2>评测记录</h2>
      <el-button :icon="Refresh" circle aria-label="刷新" @click="load" />
    </div>

    <el-table v-loading="loading" :data="runs" class="runs-table">
      <el-table-column prop="name" label="名称" min-width="160">
        <template #default="{ row }">{{ row.name || '未命名' }}</template>
      </el-table-column>
      <el-table-column label="类型" min-width="180">
        <template #default="{ row }">{{ kindLabels[row.kind] }}</template>
      </el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag :type="row.status === 'COMPLETED' ? 'success' : row.status === 'RUNNING' ? 'primary' : 'danger'">
            {{ statusLabels[row.status] }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="进度" width="160">
        <template #default="{ row }">
          <el-progress :percentage="row.progress" :status="row.status === 'FAILED' ? 'exception' : undefined" />
        </template>
      </el-table-column>
      <el-table-column label="摘要" min-width="220">
        <template #default="{ row }">{{ summary(row) }}</template>
      </el-table-column>
      <el-table-column label="时间" width="170">
        <template #default="{ row }">{{ new Date(row.created_at).toLocaleString('zh-CN') }}</template>
      </el-table-column>
      <el-table-column label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="showDetail(row)">查看</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialog" title="运行评测" width="640px">
      <el-form label-position="top">
        <el-form-item label="评测类型">
          <el-select v-model="form.kind" style="width: 100%">
            <el-option v-for="item in kindOptions" :key="item.value" :value="item.value" :label="item.label" />
          </el-select>
        </el-form-item>
        <el-form-item label="名称（可选）"><el-input v-model="form.name" placeholder="例如：CPU 基线 / GPU batch16" /></el-form-item>

        <template v-if="form.kind === 'retrieval_quality'">
          <el-form-item label="黄金集 JSON（entries 数组或含 entries 的对象）">
            <el-input v-model="form.goldenText" type="textarea" :rows="7" placeholder='[{"query":"...","expected_chunk_id":"...","kb_id":"..."}]' />
          </el-form-item>
          <el-form-item label="top-k"><el-input v-model="form.topK" /></el-form-item>
        </template>

        <template v-if="form.kind === 'search_latency'">
          <el-form-item label="查询（每行一条）">
            <el-input v-model="form.queriesText" type="textarea" :rows="5" placeholder="每行一条 query" />
          </el-form-item>
        </template>

        <template v-if="form.kind === 'embedding_throughput'">
          <el-form-item label="语料（每行一条文本）">
            <el-input v-model="form.corpusText" type="textarea" :rows="5" placeholder="每行一条文本" />
          </el-form-item>
          <el-form-item label="batch 列表"><el-input v-model="form.batchSizes" /></el-form-item>
        </template>

        <template v-if="form.kind === 'ragas_quality'">
          <el-form-item label="会议 ID（meeting_id）">
            <el-input v-model="form.meetingId" placeholder="例如：7a6ed448-35a1-448f-bd6b-c4ad74e21b03" />
          </el-form-item>
          <el-form-item label="检索范围">
            <el-select v-model="form.scope" style="width: 100%">
              <el-option value="MEETING_AND_KB" label="会议纪要 + 知识库" />
              <el-option value="CURRENT_MEETING" label="仅当前会议" />
            </el-select>
          </el-form-item>
          <el-form-item label="测试集文件（backend/eval_data 下）">
            <el-input v-model="form.ragasDatasetFile" placeholder="eval-datasets-1786019104793.json" />
          </el-form-item>
          <el-form-item label="或直接粘贴测试集 entries JSON（可选）">
            <el-input
              v-model="form.ragasEntriesText"
              type="textarea"
              :rows="5"
              placeholder='[{"question":"...","correctAnswer":"..."}]'
            />
          </el-form-item>
          <el-form-item label="Ragas 指标">
            <el-checkbox-group v-model="form.ragasMetrics">
              <el-checkbox v-for="item in ragasMetricOptions" :key="item.value" :value="item.value">
                {{ item.label }}
              </el-checkbox>
            </el-checkbox-group>
          </el-form-item>
          <el-form-item label="样本上限（0 = 全部）">
            <el-input-number v-model="form.maxItems" :min="0" :max="5000" />
          </el-form-item>
          <el-form-item label="采样种子">
            <el-input-number v-model="form.seed" :min="0" />
          </el-form-item>
        </template>

        <el-form-item v-if="form.kind === 'search_latency'" label="包含重排">
          <el-switch v-model="form.rerank" />
        </el-form-item>
        <el-form-item v-if="form.kind === 'search_latency' || form.kind === 'embedding_throughput'" label="迭代次数">
          <el-input-number v-model="form.iterations" :min="3" :max="500" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="create">启动</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="detailDialog" title="评测详情" width="720px">
      <template v-if="detail">
        <div class="detail-head">
          <el-tag>{{ kindLabels[detail.kind] }}</el-tag>
          <el-tag :type="detail.status === 'COMPLETED' ? 'success' : 'danger'">{{ statusLabels[detail.status] }}</el-tag>
          <span v-if="detail.error_message" class="detail-error">{{ detail.error_message }}</span>
        </div>
        <pre class="detail-json">{{ detailJson }}</pre>
      </template>
    </el-dialog>
  </section>
</template>

<style scoped>
.benchmarks-page {
  padding: 24px;
}
.page-hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
}
.page-hero h1 {
  margin: 4px 0;
}
.eyebrow {
  margin: 0;
  font-size: 12px;
  letter-spacing: 0.12em;
  color: var(--el-color-primary);
}
.env-chips {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}
.list-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.runs-table {
  width: 100%;
}
.detail-head {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 12px;
}
.detail-error {
  color: var(--el-color-danger);
  font-size: 13px;
}
.detail-json {
  max-height: 480px;
  overflow: auto;
  padding: 12px;
  border-radius: 8px;
  background: var(--el-fill-color-light);
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
}
</style>
