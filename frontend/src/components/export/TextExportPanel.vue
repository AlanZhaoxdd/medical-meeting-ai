<script setup lang="ts">
import { Download, View } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { meetingExportsApi } from '@/api/meetingExports'
import type { MeetingAnalysisContext } from '@/types/meetingAnalysis'
import type {
  ExportRecord,
  TextExportConfig,
  TextExportSection,
  TextPreview,
} from '@/types/meetingExport'
import { toApiError } from '@/utils/errors'

const props = defineProps<{ context: MeetingAnalysisContext }>()
const emit = defineEmits<{ refreshRecords: [] }>()

const format = ref<'docx' | 'pdf'>('docx')
const file_name = ref('')
const template = ref<'formal' | 'minimal'>('formal')
const include_cover = ref(true)
const show_attendee_names = ref(true)
const include_references = ref(true)
const include_timestamps = ref(false)
const selectedSections = ref<string[]>([
  'overview',
  'summary',
  'topics',
  'viewpoints',
  'consensus',
  'divergence',
  'cutoff',
  'open',
  'actions',
  'ai',
  'sources',
])

const sectionOptions: Array<{ key: string; label: string }> = [
  { key: 'overview', label: '会议基本信息' },
  { key: 'summary', label: '会议核心摘要' },
  { key: 'topics', label: '主要议题' },
  { key: 'viewpoints', label: '参会者观点' },
  { key: 'consensus', label: '会议共识' },
  { key: 'divergence', label: '分歧与待确认问题' },
  { key: 'cutoff', label: '切点问题及分析' },
  { key: 'open', label: '开放性问题及分析' },
  { key: 'actions', label: '行动项' },
  { key: 'ai', label: 'AI 分析结论' },
  { key: 'sources', label: '引用来源或知识库依据' },
]

const preview = ref<TextPreview | null>(null)
const previewLoading = ref(false)
const previewError = ref('')
const exporting = ref(false)
const activeExport = ref<ExportRecord | null>(null)
const pollTimer = ref<ReturnType<typeof setTimeout> | null>(null)
let pollToken = 0

const config = computed<TextExportConfig>(() => ({
  format: format.value,
  file_name: file_name.value.trim() || undefined,
  include_cover: include_cover.value,
  template: template.value,
  sections: selectedSections.value.length ? [...selectedSections.value] : undefined,
  show_attendee_names: show_attendee_names.value,
  include_references: include_references.value,
  include_timestamps: include_timestamps.value,
}))

async function loadPreview() {
  previewLoading.value = true
  previewError.value = ''
  try {
    preview.value = await meetingExportsApi.previewText(props.context.meeting.id, config.value)
  } catch (error) {
    previewError.value = toApiError(error).message
    preview.value = null
  } finally {
    previewLoading.value = false
  }
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
    if (token !== pollToken || !activeExport.value) return
    try {
      const record = await meetingExportsApi.getExport(activeExport.value.export_id)
      activeExport.value = record
      if (['PENDING', 'ANALYZING', 'GENERATING', 'RENDERING'].includes(record.status)) {
        schedulePoll()
      } else {
        if (record.status === 'COMPLETED') {
          ElMessage.success('文字版纪要导出完成')
          emit('refreshRecords')
        }
      }
    } catch {
      stopPolling()
    }
  }, 2500)
}

async function submitExport() {
  if (exporting.value) return
  exporting.value = true
  try {
    const record = await meetingExportsApi.createTextExport(props.context.meeting.id, config.value)
    activeExport.value = record
    schedulePoll()
    ElMessage.success('导出任务已提交，正在异步生成')
  } catch (error) {
    ElMessage.error(toApiError(error).message)
  } finally {
    exporting.value = false
  }
}

async function download() {
  if (!activeExport.value) return
  try {
    await meetingExportsApi.downloadExportFile(
      activeExport.value.export_id,
      activeExport.value.file_name,
    )
  } catch (error) {
    ElMessage.error(toApiError(error).message)
  }
}

function sectionBody(section: TextExportSection): string {
  if (section.items?.length) return section.items.join('\n')
  return section.content ?? ''
}

watch(config, () => void loadPreview(), { deep: true })
watch(
  () => props.context.meeting.id,
  () => {
    stopPolling()
    activeExport.value = null
    void loadPreview()
  },
  { immediate: true },
)
onBeforeUnmount(stopPolling)
</script>

<template>
  <div class="text-export">
    <el-card class="config-card" shadow="never">
      <template #header>
        <div class="card-heading">
          <div>
            <p class="eyebrow">TEXT EXPORT</p>
            <h3>文字版会议纪要</h3>
          </div>
          <el-tag size="small" effect="plain">内容来自已确认 AI 纪要</el-tag>
        </div>
      </template>

      <el-form label-position="top" class="config-form">
        <div class="form-grid">
          <el-form-item label="文件格式">
            <el-radio-group v-model="format">
              <el-radio-button value="docx">DOCX</el-radio-button>
              <el-radio-button value="pdf">PDF</el-radio-button>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="模板">
            <el-radio-group v-model="template">
              <el-radio-button value="formal">正式版</el-radio-button>
              <el-radio-button value="minimal">简洁版</el-radio-button>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="导出文件名称">
            <el-input v-model="file_name" placeholder="留空则自动生成" clearable />
          </el-form-item>
        </div>

        <div class="toggle-row">
          <el-checkbox v-model="include_cover">包含封面</el-checkbox>
          <el-checkbox v-model="show_attendee_names">显示参会者姓名</el-checkbox>
          <el-checkbox v-model="include_references">包含知识库引用</el-checkbox>
          <el-checkbox v-model="include_timestamps">包含时间戳</el-checkbox>
        </div>

        <el-form-item label="导出章节（无数据的章节会自动隐藏）">
          <el-checkbox-group v-model="selectedSections" class="section-checks">
            <el-checkbox v-for="option in sectionOptions" :key="option.key" :value="option.key">
              {{ option.label }}
            </el-checkbox>
          </el-checkbox-group>
        </el-form-item>

        <div class="form-actions">
          <el-button :icon="View" :loading="previewLoading" @click="loadPreview">刷新预览</el-button>
          <el-button type="primary" :icon="Download" :loading="exporting" @click="submitExport">
            发起导出
          </el-button>
        </div>
      </el-form>

      <el-alert
        v-if="activeExport && ['PENDING', 'ANALYZING', 'GENERATING', 'RENDERING'].includes(activeExport.status)"
        type="info"
        :closable="false"
        show-icon
        class="progress-alert"
      >
        <template #title>
          正在生成 {{ format.toUpperCase() }}（{{ activeExport.progress }}%）
        </template>
        <div class="progress-row">
          <el-progress :percentage="activeExport.progress" :stroke-width="10" />
          <p>{{ activeExport.message || '正在排版导出内容…' }}</p>
        </div>
      </el-alert>
      <el-alert
        v-else-if="activeExport?.status === 'FAILED'"
        type="error"
        :closable="false"
        show-icon
        class="progress-alert"
      >
        <template #title>导出失败：{{ activeExport.error_message || activeExport.message }}</template>
        <template #default>
          <el-button size="small" type="primary" @click="submitExport">重试</el-button>
        </template>
      </el-alert>
      <el-alert
        v-else-if="activeExport?.status === 'COMPLETED'"
        type="success"
        :closable="false"
        show-icon
        class="progress-alert"
      >
        <template #title>导出完成：{{ activeExport.file_name }}</template>
        <template #default>
          <el-button size="small" type="primary" :icon="Download" @click="download">下载文件</el-button>
        </template>
      </el-alert>
    </el-card>

    <el-card class="preview-card" shadow="never">
      <template #header>
        <div class="card-heading">
          <div>
            <p class="eyebrow">PREVIEW</p>
            <h3>导出前预览</h3>
          </div>
          <span class="preview-note">页面内容与导出文件一致</span>
        </div>
      </template>
      <div v-loading="previewLoading" class="preview-body">
        <el-alert v-if="previewError" type="error" :closable="false" :title="previewError" show-icon />
        <template v-else-if="preview">
          <div v-if="preview.include_cover" class="preview-cover">
            <h2>{{ preview.meeting_title }}</h2>
            <p>会议成果导出 · 文字版会议纪要</p>
            <p v-if="preview.starts_at">会议日期：{{ dayjs(preview.starts_at).format('YYYY-MM-DD HH:mm') }}</p>
            <p v-if="preview.organizer">组织方：{{ preview.organizer }}</p>
            <p v-if="preview.location">地点：{{ preview.location }}</p>
          </div>
          <section v-for="section in preview.sections" :key="section.key" class="preview-section">
            <h4>{{ section.title }}</h4>
            <p v-if="section.items?.length" class="preview-items">
              <span v-for="(item, index) in section.items" :key="index">{{ item }}</span>
            </p>
            <p v-else class="preview-content">{{ sectionBody(section) }}</p>
          </section>
          <el-empty v-if="!preview.sections.length" description="所选章节没有可用内容" />
        </template>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.text-export { display: grid; grid-template-columns: minmax(300px, 0.9fr) minmax(0, 1.1fr); gap: 18px; align-items: start; }
.config-card, .preview-card { border: 1px solid var(--line); border-radius: 14px; }
.card-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.card-heading h3 { margin: 0; color: #173f58; font-size: 17px; }
.card-heading .eyebrow { margin: 0 0 4px; }
.preview-note { color: #8a99a0; font-size: 12px; }
.form-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
.toggle-row { display: flex; flex-wrap: wrap; gap: 16px; margin-bottom: 18px; }
.section-checks { display: grid; grid-template-columns: repeat(2, 1fr); gap: 4px; width: 100%; }
.form-actions { display: flex; gap: 10px; }
.progress-alert { margin-top: 14px; }
.progress-row { display: grid; gap: 6px; }
.progress-row p { margin: 0; color: #6f8390; font-size: 12px; }
.preview-body { min-height: 420px; }
.preview-cover { padding: 26px; margin-bottom: 18px; border-radius: 12px; color: white; background: linear-gradient(125deg, #123c53, #155868); }
.preview-cover h2 { margin: 0 0 8px; }
.preview-cover p { margin: 4px 0; color: #c7e0e6; font-size: 13px; }
.preview-section { padding: 14px 0; border-bottom: 1px dashed #e4ece9; }
.preview-section h4 { margin: 0 0 8px; color: #173f58; font-size: 15px; }
.preview-content { margin: 0; color: #314e62; font-size: 13px; line-height: 1.75; white-space: pre-wrap; }
.preview-items { display: grid; gap: 6px; margin: 0; }
.preview-items span { color: #314e62; font-size: 13px; line-height: 1.65; }
@media (max-width: 1100px) {
  .text-export { grid-template-columns: 1fr; }
  .form-grid { grid-template-columns: 1fr; }
}
</style>
