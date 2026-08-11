<script setup lang="ts">
import { ArrowDown, ArrowUp, Delete, Download, RefreshRight, VideoPlay } from '@element-plus/icons-vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getAnalysisModules } from '@/api/meetingAnalysis'
import { meetingExportsApi } from '@/api/meetingExports'
import type { MeetingAnalysisContext } from '@/types/meetingAnalysis'
import type { ExportRecord, PptOutline, PptSlide } from '@/types/meetingExport'
import { toApiError } from '@/utils/errors'

const props = defineProps<{ context: MeetingAnalysisContext }>()
const emit = defineEmits<{ refreshRecords: [] }>()

const outline = ref<PptOutline | null>(null)
const loading = ref(false)
const generating = ref(false)
const outlineTask = ref<ExportRecord | null>(null)
const regeneratingPage = ref<number | null>(null)
const pollTimer = ref<ReturnType<typeof setTimeout> | null>(null)
let pollToken = 0

const page_count = ref<'auto' | '6' | '7' | '8'>('auto')
const include_charts = ref(true)
const include_references = ref(true)
const anonymous_attendees = ref(false)
const file_name = ref('')
const report_unit = ref('')
const presenter = ref('')
const exporting = ref(false)
const exportTask = ref<ExportRecord | null>(null)
const modules = ref<Awaited<ReturnType<typeof getAnalysisModules>> | null>(null)

const sources = computed(() => {
  const map = new Map<string, { index: number; title: string; snippet: string }>()
  for (const module of modules.value ?? []) {
    for (const source of module.references ?? []) {
      map.set(String(source.index), { index: source.index, title: source.title, snippet: source.snippet })
    }
  }
  return map
})

function sourceLabel(sourceId: string): string {
  const item = sources.value.get(sourceId)
  return item ? `[${item.index}] ${item.title}` : `[${sourceId}]`
}

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
    if (token !== pollToken) return
    if (outlineTask.value) {
      try {
        const record = await meetingExportsApi.getExport(outlineTask.value.export_id)
        outlineTask.value = record
        if (['PENDING', 'ANALYZING', 'GENERATING', 'RENDERING'].includes(record.status)) {
          schedulePoll()
        } else if (record.status === 'COMPLETED') {
          await loadOutline()
          ElMessage.success('PPT 大纲已生成，可预览与编辑')
          emit('refreshRecords')
        }
      } catch {
        stopPolling()
      }
    }
    if (exportTask.value) {
      try {
        const record = await meetingExportsApi.getExport(exportTask.value.export_id)
        exportTask.value = record
        if (['PENDING', 'ANALYZING', 'GENERATING', 'RENDERING'].includes(record.status)) {
          schedulePoll()
        } else if (record.status === 'COMPLETED') {
          ElMessage.success('PPT 导出完成')
          emit('refreshRecords')
        }
      } catch {
        stopPolling()
      }
    }
  }, 2500)
}

async function loadOutline() {
  loading.value = true
  try {
    outline.value = await meetingExportsApi.getPptOutline(props.context.meeting.id)
  } catch (error) {
    ElMessage.error(toApiError(error).message)
  } finally {
    loading.value = false
  }
}

async function generateOutline() {
  generating.value = true
  try {
    const task = await meetingExportsApi.createPptOutline(props.context.meeting.id)
    outlineTask.value = task
    schedulePoll()
    ElMessage.success('PPT 大纲生成任务已提交')
  } catch (error) {
    ElMessage.error(toApiError(error).message)
  } finally {
    generating.value = false
  }
}

async function saveOutline(): Promise<boolean> {
  if (!outline.value) return false
  try {
    await meetingExportsApi.savePptOutline(props.context.meeting.id, outline.value.spec)
    return true
  } catch (error) {
    ElMessage.error(toApiError(error).message)
    return false
  }
}

async function exportPpt() {
  if (!outline.value || exporting.value) return
  if (!(await saveOutline())) return
  exporting.value = true
  try {
    const count = page_count.value === 'auto' ? undefined : Number(page_count.value)
    const slides = count ? [...outline.value.spec.slides].slice(0, count) : [...outline.value.spec.slides]
    const config: Record<string, unknown> = {
      include_charts: include_charts.value,
      include_references: include_references.value,
      anonymous_attendees: anonymous_attendees.value,
      page_count: page_count.value,
      file_name: file_name.value.trim() || undefined,
      report_unit: report_unit.value.trim() || undefined,
      presenter: presenter.value.trim() || undefined,
      slides: slides.map((slide) => ({
        pageNumber: slide.pageNumber,
        type: slide.type,
        title: slide.title,
        bullets: slide.bullets,
        chartIds: slide.chartIds ?? [],
        speakerNotes: slide.speakerNotes ?? '',
      })),
    }
    const task = await meetingExportsApi.createPptExport(props.context.meeting.id, config)
    exportTask.value = task
    schedulePoll()
    ElMessage.success('PPT 导出任务已提交')
  } catch (error) {
    ElMessage.error(toApiError(error).message)
  } finally {
    exporting.value = false
  }
}

async function downloadPpt() {
  if (!exportTask.value) return
  try {
    await meetingExportsApi.downloadExportFile(
      exportTask.value.export_id,
      exportTask.value.file_name,
    )
    ElMessage.success('开始下载 PPT')
  } catch (error) {
    ElMessage.error(toApiError(error).message)
  }
}

function moveSlide(index: number, direction: -1 | 1) {
  if (!outline.value) return
  const target = index + direction
  if (target < 0 || target >= outline.value.spec.slides.length) return
  const slides = [...outline.value.spec.slides]
  ;[slides[index], slides[target]] = [slides[target], slides[index]]
  slides.forEach((slide, slideIndex) => {
    slide.pageNumber = slideIndex + 1
  })
  outline.value.spec.slides = slides
}

async function removeSlide(index: number) {
  if (!outline.value) return
  if (outline.value.spec.slides.length <= 6) {
    ElMessage.warning('至少保留 6 页')
    return
  }
  const slides = [...outline.value.spec.slides]
  slides.splice(index, 1)
  slides.forEach((slide, slideIndex) => {
    slide.pageNumber = slideIndex + 1
  })
  outline.value.spec.slides = slides
}

async function regeneratePage(slide: PptSlide) {
  if (regeneratingPage.value !== null) return
  regeneratingPage.value = slide.pageNumber
  try {
    const { value } = await ElMessageBox.prompt(
      `输入对第 ${slide.pageNumber} 页“${slide.title}”的重新生成要求（可选）`,
      '重新生成单页内容',
      { confirmButtonText: '生成', cancelButtonText: '取消', inputPlaceholder: '例如：补充行动项截止时间' },
    )
    const updated = await meetingExportsApi.regeneratePptPage(
      props.context.meeting.id,
      slide.pageNumber,
      value,
    )
    outline.value = updated
    ElMessage.success('该页内容已重新生成')
  } catch (error) {
    if ((error as { message?: string })?.message !== 'cancel') {
      ElMessage.error(toApiError(error).message)
    }
  } finally {
    regeneratingPage.value = null
  }
}

function slideTypeLabel(type: string): string {
  const map: Record<string, string> = {
    cover: '封面',
    summary: '核心摘要',
    topics: '议题与观点',
    viewpoints: '参会者观点',
    cutoff_questions: '切点问题',
    charts: '数据图表',
    consensus: '共识与分歧',
    actions: '行动项',
    sources: '引用来源',
  }
  return map[type] ?? type
}

watch(
  () => props.context.meeting.id,
  () => {
    stopPolling()
    outlineTask.value = null
    exportTask.value = null
    void Promise.all([loadOutline(), loadSources()])
  },
  { immediate: true },
)
onBeforeUnmount(stopPolling)

async function loadSources() {
  try {
    modules.value = await getAnalysisModules(props.context.meeting.id)
  } catch {
    modules.value = null
  }
}
</script>

<template>
  <div class="ppt-export">
    <el-card v-if="!outline && !outlineTask && !loading" class="outline-empty" shadow="never">
      <el-empty description="尚未生成 PPT 大纲">
        <template #description>
          <p>先生成大纲，确认每页标题与要点后再生成 PPTX 文件。</p>
        </template>
        <el-button type="primary" :icon="VideoPlay" :loading="generating" @click="generateOutline">
          生成 PPT 大纲
        </el-button>
      </el-empty>
    </el-card>

    <el-card v-else-if="outlineTask && ['PENDING', 'ANALYZING', 'GENERATING', 'RENDERING'].includes(outlineTask.status)" class="outline-progress" shadow="never">
      <div class="progress-head">
        <div>
          <strong>AI 正在生成 PPT 大纲</strong>
          <p>{{ outlineTask.message || '正在组织会议汇报内容…' }}</p>
        </div>
      </div>
      <el-progress :percentage="outlineTask.progress" />
    </el-card>

    <template v-else-if="outline">
      <el-card class="outline-card" shadow="never">
        <template #header>
          <div class="card-heading">
            <div>
              <p class="eyebrow">OUTLINE PREVIEW</p>
              <h3>PPT 大纲预览（{{ outline.spec.slides.length }} 页）</h3>
            </div>
            <div class="heading-actions">
              <el-button :icon="RefreshRight" size="small" @click="loadOutline">刷新</el-button>
              <el-button size="small" @click="saveOutline">保存大纲</el-button>
            </div>
          </div>
        </template>

        <el-form label-position="top" class="outline-form">
          <div class="form-grid">
            <el-form-item label="PPT 标题">
              <el-input v-model="outline.spec.title" />
            </el-form-item>
            <el-form-item label="副标题">
              <el-input v-model="outline.spec.subtitle" />
            </el-form-item>
            <el-form-item label="页数">
              <el-select v-model="page_count">
                <el-option label="自动（6～8 页）" value="auto" />
                <el-option label="6 页" value="6" />
                <el-option label="7 页" value="7" />
                <el-option label="8 页" value="8" />
              </el-select>
            </el-form-item>
          </div>
        </el-form>

        <div class="slide-list">
          <div v-for="(slide, index) in outline.spec.slides" :key="`${slide.pageNumber}-${index}`" class="slide-row">
            <div class="slide-order">
              <span>{{ slide.pageNumber }}</span>
              <div class="slide-actions">
                <el-button text :icon="ArrowUp" size="small" :disabled="index === 0" @click="moveSlide(index, -1)" />
                <el-button text :icon="ArrowDown" size="small" :disabled="index === outline.spec.slides.length - 1" @click="moveSlide(index, 1)" />
                <el-button text :icon="Delete" size="small" @click="removeSlide(index)" />
              </div>
            </div>
            <div class="slide-editor">
              <div class="slide-title-row">
                <el-tag size="small" effect="plain" class="type-tag">{{ slideTypeLabel(slide.type) }}</el-tag>
                <el-input v-model="slide.title" size="small" class="title-input" />
                <el-button
                  text
                  size="small"
                  :icon="RefreshRight"
                  :loading="regeneratingPage === slide.pageNumber"
                  @click="regeneratePage(slide)"
                >
                  重生成
                </el-button>
              </div>
              <div v-for="(bullet, bulletIndex) in slide.bullets" :key="bulletIndex" class="bullet-row">
                <span class="bullet-mark">•</span>
                <el-input v-model="bullet.text" size="small" class="bullet-input" />
                <span class="bullet-sources">
                  <el-tag v-for="sourceId in bullet.sourceIds" :key="sourceId" size="small" effect="plain" class="source-tag">
                    {{ sourceLabel(sourceId) }}
                  </el-tag>
                </span>
              </div>
              <div class="notes-row">
                <el-input v-model="slide.speakerNotes" size="small" placeholder="演讲备注（写入 PPT 备注区）" />
              </div>
            </div>
          </div>
        </div>
      </el-card>

      <el-card class="render-card" shadow="never">
        <template #header>
          <div class="card-heading">
            <div>
              <p class="eyebrow">RENDER</p>
              <h3>PPT 文件导出</h3>
            </div>
          </div>
        </template>
        <div class="render-form">
          <el-checkbox v-model="include_charts">包含图表</el-checkbox>
          <el-checkbox v-model="include_references">包含引用来源</el-checkbox>
          <el-checkbox v-model="anonymous_attendees">匿名展示参会者</el-checkbox>
          <el-input v-model="file_name" placeholder="PPT 文件名（留空自动生成）" clearable class="render-input" />
          <el-input v-model="report_unit" placeholder="汇报单位（可选）" clearable class="render-input" />
          <el-input v-model="presenter" placeholder="汇报人（可选）" clearable class="render-input" />
          <el-button type="primary" :icon="Download" :loading="exporting" @click="exportPpt">生成 PPTX</el-button>
        </div>
        <el-alert
          v-if="exportTask && ['PENDING', 'ANALYZING', 'GENERATING', 'RENDERING'].includes(exportTask.status)"
          type="info"
          :closable="false"
          show-icon
          class="progress-alert"
        >
          <template #title>正在生成 PPT（{{ exportTask.progress }}%）</template>
          <el-progress :percentage="exportTask.progress" :stroke-width="10" />
        </el-alert>
        <el-alert
          v-else-if="exportTask?.status === 'FAILED'"
          type="error"
          :closable="false"
          show-icon
          class="progress-alert"
        >
          <template #title>PPT 生成失败：{{ exportTask.error_message || exportTask.message }}</template>
          <template #default><el-button size="small" type="primary" @click="exportPpt">重试</el-button></template>
        </el-alert>
        <el-alert
          v-else-if="exportTask?.status === 'COMPLETED'"
          type="success"
          :closable="false"
          show-icon
          class="progress-alert"
        >
          <template #title>PPT 已生成：{{ exportTask.file_name }}</template>
          <template #default>
            <el-button size="small" type="primary" :icon="Download" @click="downloadPpt">下载 PPT</el-button>
          </template>
        </el-alert>
      </el-card>
    </template>
  </div>
</template>

<style scoped>
.ppt-export { display: grid; gap: 18px; }
.outline-empty, .outline-card, .render-card { border: 1px solid var(--line); border-radius: 14px; }
.outline-progress { border: 1px solid #cdc3ee; border-radius: 14px; background: #f8f6fd; }
.progress-head strong { color: #5b3fa8; }
.progress-head p { margin: 5px 0 12px; color: #7a6fb0; font-size: 13px; }
.card-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.card-heading h3 { margin: 0; color: #173f58; font-size: 17px; }
.card-heading .eyebrow { margin: 0 0 4px; }
.heading-actions { display: flex; gap: 8px; }
.form-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
.slide-list { display: grid; gap: 12px; }
.slide-row { display: grid; grid-template-columns: 46px minmax(0, 1fr); gap: 12px; padding: 14px; border: 1px solid #e3ece9; border-radius: 12px; background: #fbfdfc; }
.slide-order { display: flex; flex-direction: column; align-items: center; gap: 6px; }
.slide-order > span { display: grid; place-items: center; width: 30px; height: 30px; border-radius: 8px; color: #123c53; background: #e7f1ee; font-weight: 700; }
.slide-actions { display: grid; }
.slide-editor { display: grid; gap: 8px; min-width: 0; }
.slide-title-row { display: flex; align-items: center; gap: 8px; }
.title-input { flex: 1; }
.type-tag { flex: 0 0 auto; }
.bullet-row { display: flex; align-items: flex-start; gap: 8px; }
.bullet-mark { color: #168b82; font-weight: 700; line-height: 30px; }
.bullet-input { flex: 1; }
.bullet-sources { display: flex; flex-wrap: wrap; gap: 4px; max-width: 300px; align-items: center; }
.source-tag { max-width: 190px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.notes-row { margin-top: 2px; }
.render-form { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }
.render-input { width: 240px; }
.progress-alert { margin-top: 14px; }
@media (max-width: 1100px) {
  .form-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .bullet-row { flex-wrap: wrap; }
}
</style>
