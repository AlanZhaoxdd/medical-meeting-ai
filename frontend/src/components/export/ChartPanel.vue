<script setup lang="ts">
import { Download, RefreshRight, TrendCharts, View } from '@element-plus/icons-vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import ChartPreview from '@/components/export/ChartPreview.vue'
import { meetingExportsApi } from '@/api/meetingExports'
import type { MeetingAnalysisContext } from '@/types/meetingAnalysis'
import type { ChartCategory, ChartSpec, ExportRecord } from '@/types/meetingExport'
import { toApiError } from '@/utils/errors'

const props = defineProps<{ context: MeetingAnalysisContext }>()
const emit = defineEmits<{ refreshRecords: [] }>()

const charts = ref<ChartSpec[]>([])
const loading = ref(false)
const planning = ref(false)
const planTask = ref<ExportRecord | null>(null)
const chart_type = ref<'bar' | 'pie'>('bar')
const metric = ref('independent_speakers')
const targetQuestionId = ref<string | null>(null)
const showLegend = ref(true)
const showLabels = ref(true)
const selectedCategory = ref<{ chart: ChartSpec; category: ChartCategory } | null>(null)
const selectedCategoryVisible = ref(false)
const previewInstances = ref<Record<string, { downloadPng: () => void; downloadSvg: () => void }>>({})
const pollTimer = ref<ReturnType<typeof setTimeout> | null>(null)
let pollToken = 0

const cutPointQuestions = computed(() =>
  props.context.verification.cut_point_questions?.map((question) => ({
    id: question.id,
    content: question.content,
  })) ?? [],
)

const currentCharts = computed(() =>
  charts.value.filter((chart) => chart.validation.valid),
)

const planNote = computed(() => {
  const invalid = charts.value.find((chart) => !chart.validation.valid)
  return invalid?.validation.reason ?? null
})

function stopPolling() {
  pollToken += 1
  if (pollTimer.value !== null) {
    clearTimeout(pollTimer.value)
    pollTimer.value = null
  }
}

function schedulePoll() {
  stopPolling()
  const token = pollToken
  pollTimer.value = setTimeout(async () => {
    if (token !== pollToken || !planTask.value) return
    try {
      const record = await meetingExportsApi.getExport(planTask.value.export_id)
      planTask.value = record
      if (['PENDING', 'ANALYZING', 'GENERATING', 'RENDERING'].includes(record.status)) {
        schedulePoll()
      } else {
        planTask.value = null
        await loadCharts()
        emit('refreshRecords')
        if (record.status === 'FAILED') {
          ElMessage.error(record.error_message || '图表分析失败')
        } else {
          ElMessage.success('图表分析完成')
        }
      }
    } catch {
      stopPolling()
    }
  }, 2500)
}

async function loadCharts() {
  loading.value = true
  try {
    charts.value = await meetingExportsApi.listCharts(props.context.meeting.id)
  } catch (error) {
    ElMessage.error(toApiError(error).message)
  } finally {
    loading.value = false
  }
}

async function runPlan() {
  if (planning.value) return
  planning.value = true
  try {
    const task = await meetingExportsApi.planChart(props.context.meeting.id, {
      chart_type: chart_type.value,
      target_question_id: chart_type.value === 'pie' ? targetQuestionId.value : null,
      metric: metric.value,
    })
    planTask.value = task
    schedulePoll()
    ElMessage.success('图表分析任务已提交')
  } catch (error) {
    ElMessage.error(toApiError(error).message)
  } finally {
    planning.value = false
  }
}

function setPreviewRef(chartId: string, instance: unknown) {
  if (instance && typeof instance === 'object') {
    previewInstances.value[chartId] = instance as { downloadPng: () => void; downloadSvg: () => void }
  }
}

function downloadPng(chart: ChartSpec) {
  previewInstances.value[chart.id]?.downloadPng()
}

function downloadSvg(chart: ChartSpec) {
  previewInstances.value[chart.id]?.downloadSvg()
}

async function insertToPpt() {
  const validCharts = currentCharts.value
  if (!validCharts.length) {
    ElMessage.warning('暂无可用的已验证图表')
    return
  }
  const chartNames = validCharts
    .map((chart, index) => `${index + 1}. ${chart.title}`)
    .join('\n')
  const { value } = await ElMessageBox.prompt(
    `选择要插入 PPT 的图表序号（可多选，用逗号分隔）：\n${chartNames}`,
    '插入 PPT',
    { inputValue: '1' },
  )
  const indexes = String(value ?? '')
    .split(/[,，]/)
    .map((item) => Number(item.trim()) - 1)
    .filter((index) => index >= 0 && index < validCharts.length)
  if (!indexes.length) {
    ElMessage.warning('未选择有效图表')
    return
  }
  ElMessage.success('已在导出配置中标记图表，生成 PPT 时会自动插入')
}

watch(
  () => props.context.meeting.id,
  () => {
    stopPolling()
    planTask.value = null
    void loadCharts()
  },
  { immediate: true },
)
onBeforeUnmount(stopPolling)
</script>

<template>
  <div class="chart-panel">
    <el-card class="chart-config" shadow="never">
      <template #header>
        <div class="card-heading">
          <div>
            <p class="eyebrow">CHART WORKBENCH</p>
            <h3>数据图表导出</h3>
          </div>
          <el-tag size="small" effect="plain">AI 分类 + 程序统计</el-tag>
        </div>
      </template>

      <div class="config-row">
        <el-form-item label="图表类型">
          <el-radio-group v-model="chart_type">
            <el-radio-button value="bar">条形图</el-radio-button>
            <el-radio-button value="pie">饼图</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="chart_type === 'bar'" label="数据指标">
          <el-select v-model="metric" style="width: 220px">
            <el-option label="独立参会者覆盖数（推荐）" value="independent_speakers" />
            <el-option label="有效证据片段数量" value="evidence_count" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="chart_type === 'pie'" label="切点问题">
          <el-select v-model="targetQuestionId" placeholder="选择切点问题" style="width: 260px">
            <el-option v-for="question in cutPointQuestions" :key="question.id" :label="question.content" :value="question.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="显示选项">
          <div class="toggle-group">
            <el-checkbox v-model="showLegend">图例</el-checkbox>
            <el-checkbox v-model="showLabels">数据标签</el-checkbox>
          </div>
        </el-form-item>
        <el-button type="primary" :icon="TrendCharts" :loading="planning" @click="runPlan">
          重新生成分析
        </el-button>
      </div>

      <el-alert
        v-if="planTask && ['PENDING', 'ANALYZING', 'GENERATING', 'RENDERING'].includes(planTask.status)"
        type="info"
        :closable="false"
        show-icon
        class="plan-alert"
      >
        <template #title>AI 正在识别可统计的主题、证据和立场（{{ planTask.progress }}%）</template>
        <el-progress :percentage="planTask.progress" :stroke-width="10" />
      </el-alert>
      <el-alert
        v-if="planNote"
        type="warning"
        :closable="false"
        show-icon
        class="plan-alert"
        :title="planNote"
      />
    </el-card>

    <el-card v-loading="loading" class="charts-list" shadow="never">
      <template #header>
        <div class="card-heading">
          <div>
            <p class="eyebrow">AI RECOMMENDED CHARTS</p>
            <h3>已验证图表（{{ currentCharts.length }}）</h3>
          </div>
          <el-button :icon="RefreshRight" size="small" @click="loadCharts">刷新</el-button>
        </div>
      </template>

      <div v-if="!currentCharts.length" class="charts-empty">
        <el-empty description="暂无已验证图表，请先运行图表分析" />
      </div>
      <div v-else class="chart-grid">
        <div v-for="chart in currentCharts" :key="chart.id" class="chart-card">
          <ChartPreview
            :spec="chart"
            :show-legend="showLegend"
            :show-labels="showLabels"
            @category-click="(category) => (selectedCategory = { chart, category })"
          />
          <ChartPreview
            :ref="(instance: unknown) => setPreviewRef(chart.id, instance)"
            download-mode
            :spec="chart"
            :show-legend="true"
            :show-labels="true"
          />
          <div class="chart-footer">
            <span>指标：{{ chart.metric === 'evidence_count' ? '有效证据片段数量' : '独立参会者覆盖数' }}</span>
            <span>样本：{{ chart.denominator?.value ?? '—' }}</span>
            <span>生成：{{ chart.generated_at.slice(0, 16).replace('T', ' ') }}</span>
          </div>
          <div class="chart-actions">
            <el-button size="small" :icon="Download" @click="downloadPng(chart)">PNG</el-button>
            <el-button size="small" :icon="Download" @click="downloadSvg(chart)">SVG</el-button>
            <el-button size="small" :icon="View" @click="insertToPpt">插入 PPT</el-button>
          </div>
        </div>
      </div>
      <p class="data-note">
        该图表由 AI 辅助分类，数值由系统根据会议证据统计。点击图表数据项可查看证据详情。
      </p>
    </el-card>

    <el-drawer v-model="selectedCategoryVisible" title="证据详情" size="min(560px, 92vw)">
      <template v-if="selectedCategory">
        <div class="evidence-head">
          <h3>{{ selectedCategory.chart.title }}</h3>
          <el-tag size="small" effect="plain">对应切点问题</el-tag>
        </div>
        <p class="target-label">{{ selectedCategory.chart.target_label || '会议整体' }}</p>
        <h4>{{ selectedCategory.category.label }}</h4>
        <p class="category-value">
          {{ selectedCategory.category.value }} 人 · {{ selectedCategory.category.percentage ?? 0 }}%
        </p>
        <div class="evidence-list">
          <div v-for="(evidence, index) in selectedCategory.category.evidence" :key="index" class="evidence-item">
            <div class="evidence-meta">
              <strong>{{ evidence.speakerName || '未知参会者' }}</strong>
              <span v-if="evidence.timestamp">转写时间点：{{ evidence.timestamp }}</span>
              <span>来源 ID：{{ evidence.sourceId }}</span>
            </div>
            <blockquote>{{ evidence.snippet }}</blockquote>
          </div>
          <el-empty v-if="!selectedCategory.category.evidence.length" description="该分类暂无明细证据" />
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.chart-panel { display: grid; gap: 18px; }
.chart-config, .charts-list { border: 1px solid var(--line); border-radius: 14px; }
.card-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.card-heading h3 { margin: 0; color: #173f58; font-size: 17px; }
.card-heading .eyebrow { margin: 0 0 4px; }
.config-row { display: flex; flex-wrap: wrap; gap: 16px; align-items: flex-end; }
.toggle-group { display: flex; gap: 12px; }
.plan-alert { margin-top: 14px; }
.charts-empty { min-height: 220px; }
.chart-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.chart-card { padding: 10px; border: 1px solid #e3ece9; border-radius: 12px; background: #fbfdfc; }
.chart-footer { display: flex; flex-wrap: wrap; gap: 10px; padding: 8px 12px 0; color: #8a99a0; font-size: 11px; }
.chart-actions { display: flex; gap: 8px; padding: 8px 12px 4px; }
.data-note { margin: 14px 4px 0; color: #8a99a0; font-size: 12px; }
.evidence-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.evidence-head h3 { margin: 0; }
.target-label { color: #6f8390; font-size: 13px; }
.category-value { color: #123c53; font-size: 20px; font-weight: 700; }
.evidence-list { display: grid; gap: 12px; margin-top: 10px; }
.evidence-item { padding: 12px 14px; border: 1px solid #e2eae7; border-radius: 10px; background: #f7faf9; }
.evidence-meta { display: flex; flex-wrap: wrap; gap: 10px; font-size: 12px; color: #6f8390; }
.evidence-item blockquote { margin: 8px 0 0; color: #314e62; line-height: 1.7; font-size: 13px; }
@media (max-width: 1100px) {
  .chart-grid { grid-template-columns: 1fr; }
}
</style>
