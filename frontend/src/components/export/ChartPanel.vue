<script setup lang="ts">
import { Download, RefreshRight, TrendCharts, View } from '@element-plus/icons-vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import ChartPreview from '@/components/export/ChartPreview.vue'
import { meetingExportsApi } from '@/api/meetingExports'
import type { MeetingAnalysisContext } from '@/types/meetingAnalysis'
import type { ChartCategory, ChartCutpointTemplate, ChartSpec, ExportRecord } from '@/types/meetingExport'
import { toApiError } from '@/utils/errors'

const props = defineProps<{ context: MeetingAnalysisContext }>()
const emit = defineEmits<{ refreshRecords: [] }>()

const charts = ref<ChartSpec[]>([])
const loading = ref(false)
const planning = ref(false)
const planTask = ref<ExportRecord | null>(null)
const hasRunAnalysis = ref(false)
const chart_type = ref<'bar' | 'pie'>('bar')
const templates = ref<ChartCutpointTemplate[]>([])
const selectedTemplateId = ref<string | null>(null)
const selectedCutpointKey = ref<string | null>(null)
const pptSelection = ref<string[]>([])
const showLegend = ref(true)
const showLabels = ref(true)
const selectedCategory = ref<{ chart: ChartSpec; category: ChartCategory } | null>(null)
const selectedCategoryVisible = ref(false)
const previewInstances = ref<Record<string, { downloadPng: () => void; downloadSvg: () => void }>>({})
const pollTimer = ref<ReturnType<typeof setTimeout> | null>(null)
let pollToken = 0

const selectedTemplate = computed(() => templates.value.find((item) => item.id === selectedTemplateId.value) ?? templates.value[0] ?? null)
const cutpointOptions = computed(() => selectedTemplate.value?.items ?? [])
const selectedCutpoint = computed(() => selectedTemplate.value?.items.find((item) => item.key === selectedCutpointKey.value) ?? null)

const countModeLabel = (mode?: ChartSpec['count_mode'] | null) =>
  mode === 'evidence_count' ? '有效证据次数' : '人数'

const formatBin = (bin: { label: string; lower?: number | null; upper?: number | null }) => bin.label || `${bin.lower ?? '-'}~${bin.upper ?? '+'}`

const currentCharts = computed(() => {
  if (!hasRunAnalysis.value) return []
  return charts.value.filter((chart) =>
    chart.validation.valid &&
    chart.type === chart_type.value &&
    (!selectedCutpointKey.value || chart.cutpoint_key === selectedCutpointKey.value),
  )
})

const pptCharts = computed(() => charts.value.filter((chart) => chart.validation.valid && chart.type === chart_type.value))

const planNote = computed(() => {
  if (!hasRunAnalysis.value) return null
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
        hasRunAnalysis.value = true
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
    // Chart specs are persisted by the backend. Restore the workbench state
    // when returning to the page instead of requiring another analysis run.
    hasRunAnalysis.value = charts.value.some((chart) => chart.validation.valid)
  } catch (error) {
    ElMessage.error(toApiError(error).message)
  } finally {
    loading.value = false
  }
}

async function loadTemplates() {
  try {
    templates.value = await meetingExportsApi.listChartCutpointTemplates()
    if (!selectedTemplateId.value && templates.value[0]) selectedTemplateId.value = templates.value[0].id
    if (!selectedCutpointKey.value && selectedTemplate.value?.items[0]) {
      selectedCutpointKey.value = selectedTemplate.value.items[0].key
    }
  } catch {
    templates.value = []
  }
}

async function runPlan() {
  if (planning.value) return
  planning.value = true
  try {
    const task = await meetingExportsApi.planChart(props.context.meeting.id, {
      chart_type: chart_type.value,
      template_id: selectedTemplateId.value,
      template_version: selectedTemplate.value?.version ?? null,
      cutpoint_key: selectedCutpointKey.value,
      count_mode: selectedCutpoint.value?.count_mode ?? 'unique_speakers',
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

function openCategory(chart: ChartSpec, category: ChartCategory) {
  if (chart.data_origin === 'demo') return
  selectedCategory.value = { chart, category }
  selectedCategoryVisible.value = true
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
  const validCharts = pptCharts.value
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
  const selectedIds = indexes.map((index) => validCharts[index].id)
  try {
    const saved = await meetingExportsApi.saveChartSelection(props.context.meeting.id, {
      chart_ids: selectedIds,
    })
    pptSelection.value = saved.chart_ids
    ElMessage.success('已保存并同步到 PPT 大纲（数据图表页）')
  } catch (error) {
    ElMessage.error(toApiError(error).message)
  }
}

async function loadPptSelection() {
  try {
    const saved = await meetingExportsApi.getChartSelection(props.context.meeting.id)
    pptSelection.value = saved.chart_ids
  } catch {
    pptSelection.value = []
  }
}

watch(
  () => props.context.meeting.id,
  () => {
    stopPolling()
    planTask.value = null
    hasRunAnalysis.value = false
    charts.value = []
    void loadCharts()
    void loadPptSelection()
    void loadTemplates()
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
          <div class="heading-tags">
            <el-tag v-if="pptSelection.length" size="small" type="success">
              已选 {{ pptSelection.length }} 张图插入 PPT
            </el-tag>
            <el-tag size="small" effect="plain">预设数据 + 程序统计</el-tag>
          </div>
        </div>
      </template>

      <div class="config-row">
        <el-form-item label="切点">
          <el-select v-model="selectedCutpointKey" style="width: 220px">
            <el-option v-for="item in cutpointOptions" :key="item.key" :label="item.label" :value="item.key" />
          </el-select>
        </el-form-item>
        <el-form-item label="切点问题" class="cutpoint-question-item">
          <div class="cutpoint-question">
            {{ selectedCutpoint?.question || '当前切点暂未配置问题' }}
          </div>
        </el-form-item>
        <el-form-item label="图表类型">
          <el-radio-group v-model="chart_type">
            <el-radio-button value="bar">条形图</el-radio-button>
            <el-radio-button value="pie">饼图</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="显示选项">
          <div class="toggle-group">
            <el-checkbox v-model="showLegend">图例</el-checkbox>
            <el-checkbox v-model="showLabels">数据标签</el-checkbox>
          </div>
        </el-form-item>
        <el-button type="primary" :icon="TrendCharts" :loading="planning" @click="runPlan">
          生成图表
        </el-button>
      </div>
      <div v-if="selectedCutpoint" class="cutpoint-definition">
        <span class="definition-label">当前切点区间：</span>
        <el-tag v-for="bin in selectedCutpoint.bins" :key="bin.key" size="small" effect="plain">
          {{ formatBin(bin) }}
        </el-tag>
      </div>

      <el-alert
        v-if="planTask && ['PENDING', 'ANALYZING', 'GENERATING', 'RENDERING'].includes(planTask.status)"
        type="info"
        :closable="false"
        show-icon
        class="plan-alert"
      >
        <template #title>正在生成切点图表（{{ planTask.progress }}%）</template>
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
          <div class="chart-stage">
            <ChartPreview
              :spec="chart"
              :show-legend="showLegend"
              :show-labels="showLabels"
              @category-click="(category) => openCategory(chart, category)"
            />
          </div>
          <ChartPreview
            :ref="(instance: unknown) => setPreviewRef(chart.id, instance)"
            download-mode
            :spec="chart"
            :show-legend="true"
            :show-labels="true"
          />
          <div class="chart-footer">
            <span>样本人数：{{ chart.denominator?.value ?? chart.valid_observation_count }} 人</span>
            <span>生成：{{ chart.generated_at.slice(0, 16).replace('T', ' ') }}</span>
          </div>
          <el-alert
            v-if="chart.excluded_observation_count"
            class="excluded-alert"
            type="warning"
            :closable="false"
            :title="`已排除 ${chart.excluded_observation_count} 条无法可靠归类的数据`"
          />
          <div v-if="chart.excluded_reasons.length" class="excluded-reasons">
            <div v-for="(item, index) in chart.excluded_reasons.slice(0, 3)" :key="`${chart.id}-excluded-${index}`">
              {{ item.rawValue || item.value || '未识别数值' }}：{{ item.reason }}
            </div>
            <div v-if="chart.excluded_reasons.length > 3">其余排除原因可在证据快照中查看。</div>
          </div>
          <p v-if="chart.interpretation" class="chart-interpretation">{{ chart.interpretation }}</p>
          <div class="chart-actions">
            <el-button size="small" :icon="Download" @click="downloadPng(chart)">PNG</el-button>
            <el-button size="small" :icon="Download" @click="downloadSvg(chart)">SVG</el-button>
            <el-button size="small" :icon="View" @click="insertToPpt">插入 PPT</el-button>
          </div>
        </div>
      </div>
      <p class="data-note">
        条形图展示各区间人数，饼图展示同一组数据的占比。
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
          {{ selectedCategory.category.value }} {{ countModeLabel(selectedCategory.chart.count_mode) }}<span v-if="selectedCategory.chart.type === 'pie'"> · {{ selectedCategory.category.percentage ?? 0 }}%</span>
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
        <div v-if="selectedCategory.chart.excluded_reasons.length" class="excluded-note">
          本次图表排除了 {{ selectedCategory.chart.excluded_observation_count }} 条无法可靠归类的数据。
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.chart-panel { display: grid; gap: 18px; }
.chart-config, .charts-list { border: 1px solid var(--line); border-radius: 14px; }
.card-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.heading-tags { display: flex; align-items: center; gap: 8px; }
.card-heading h3 { margin: 0; color: #173f58; font-size: 17px; }
.card-heading .eyebrow { margin: 0 0 4px; }
.config-row { display: flex; flex-wrap: wrap; gap: 16px; align-items: flex-end; }
.cutpoint-question-item { flex: 1 1 420px; min-width: 320px; }
.cutpoint-question { min-height: 32px; padding: 7px 11px; border: 1px solid #dbe7e3; border-radius: 7px; background: #f7faf9; color: #314e62; line-height: 1.55; }
.cutpoint-definition { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 14px; color: #6f8390; font-size: 12px; }
.definition-label { font-weight: 600; color: #314e62; }
.toggle-group { display: flex; gap: 12px; }
.plan-alert { margin-top: 14px; }
.charts-empty { min-height: 220px; }
.chart-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.chart-card { padding: 10px; border: 1px solid #e3ece9; border-radius: 12px; background: #fbfdfc; }
.chart-stage { position: relative; display: flex; justify-content: center; padding: 4px 10px 18px; }
.chart-stage .chart-preview { max-width: 760px; }
.chart-footer { display: flex; flex-wrap: wrap; gap: 10px; padding: 8px 12px 0; color: #8a99a0; font-size: 11px; }
.excluded-alert { margin: 10px 12px 0; }
.excluded-reasons { margin: 8px 12px 0; padding: 8px 10px; border-radius: 8px; background: #fffaf0; color: #8b6a25; font-size: 11px; line-height: 1.7; }
.chart-interpretation { margin: 8px 12px 0; padding: 10px 12px; border-left: 3px solid #168b82; background: #f0f7f5; color: #314e62; font-size: 12.5px; line-height: 1.7; }
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
.excluded-note { margin-top: 16px; padding: 10px 12px; border-radius: 8px; background: #fff7e6; color: #8b5a00; font-size: 12px; }
@media (max-width: 1100px) {
  .chart-grid { grid-template-columns: 1fr; }
}
</style>
