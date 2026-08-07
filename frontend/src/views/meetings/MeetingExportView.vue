<script setup lang="ts">
import {
  Back,
  DataAnalysis,
  Document,
  Files,
  Histogram,
  RefreshRight,
} from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import ChartPanel from '@/components/export/ChartPanel.vue'
import ExportHistoryDrawer from '@/components/export/ExportHistoryDrawer.vue'
import PptOutlineEditor from '@/components/export/PptOutlineEditor.vue'
import TextExportPanel from '@/components/export/TextExportPanel.vue'
import { loadAnalysisContext } from '@/api/meetingAnalysis'
import type { MeetingAnalysisContext } from '@/types/meetingAnalysis'
import type { ExportRecord, ExportType } from '@/types/meetingExport'
import { meetingExportsApi } from '@/api/meetingExports'
import { toApiError } from '@/utils/errors'
import { analysisStatusLabels } from '@/utils/meeting'

const route = useRoute()
const router = useRouter()
const meetingId = computed(() => String(route.params.meetingId))

const context = ref<MeetingAnalysisContext | null>(null)
const loading = ref(false)
const loadError = ref('')
const activeType = ref<ExportType>('text')
const historyVisible = ref(false)
const records = ref<ExportRecord[]>([])
const pollTimer = ref<ReturnType<typeof setTimeout> | null>(null)
const pollToken = ref(0)

const latestCompletedAt = computed(() => {
  const completed = records.value.filter((record) => record.status === 'COMPLETED')
  if (!completed.length) return null
  return completed.sort((a, b) => b.created_at.localeCompare(a.created_at))[0].completed_at
})

const exportTypes: Array<{
  key: ExportType
  title: string
  description: string
  icon: typeof Document
}> = [
  { key: 'text', title: '文字纪要', description: 'DOCX / PDF 排版导出，与已确认 AI 纪要一致', icon: Document },
  { key: 'ppt', title: '汇报 PPT', description: '6～8 页可编辑 PPTX，先预览大纲再生成', icon: Files },
  { key: 'chart', title: '数据图表', description: '基于会议证据的条形图与立场饼图', icon: Histogram },
]

function stopPolling() {
  pollToken.value += 1
  if (pollTimer.value !== null) {
    clearTimeout(pollTimer.value)
    pollTimer.value = null
  }
}

function schedulePoll() {
  stopPolling()
  const token = pollToken.value
  pollTimer.value = setTimeout(async () => {
    if (token !== pollToken.value) return
    try {
      const list = await meetingExportsApi.listExports(meetingId.value, 1, 50)
      records.value = list.items
      const active = records.value.some((record) =>
        ['PENDING', 'ANALYZING', 'GENERATING', 'RENDERING'].includes(record.status),
      )
      if (active) schedulePoll()
    } catch {
      // transient poll errors are ignored; the next refresh still works
    }
  }, 2500)
}

async function refreshRecords() {
  try {
    const list = await meetingExportsApi.listExports(meetingId.value, 1, 50)
    records.value = list.items
    if (
      records.value.some((record) =>
        ['PENDING', 'ANALYZING', 'GENERATING', 'RENDERING'].includes(record.status),
      )
    ) {
      schedulePoll()
    }
  } catch (error) {
    ElMessage.error(toApiError(error).message)
  }
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    context.value = await loadAnalysisContext(meetingId.value)
    await refreshRecords()
  } catch (error) {
    loadError.value = toApiError(error).message
  } finally {
    loading.value = false
  }
}

watch(meetingId, () => load(), { immediate: true })
onMounted(() => window.addEventListener('app-toast', onAppToast))
onBeforeUnmount(() => {
  stopPolling()
  window.removeEventListener('app-toast', onAppToast)
})

function onAppToast(event: Event) {
  const detail = (event as CustomEvent<string>).detail
  if (detail) ElMessage.success(detail)
}
</script>

<template>
  <section v-loading="loading" class="export-page">
    <el-alert v-if="loadError" type="error" :closable="false" :title="loadError" show-icon class="page-alert">
      <template #default>
        <el-button size="small" @click="load">重试</el-button>
      </template>
    </el-alert>

    <template v-if="context">
      <div class="page-header export-header">
        <div class="header-left">
          <el-button link type="primary" :icon="Back" @click="router.push({ name: 'meeting-export-list' })">
            返回会议成果导出
          </el-button>
          <p class="eyebrow">MEETING OUTCOME EXPORT</p>
          <h1 class="page-title">会议成果导出</h1>
          <p class="page-subtitle">{{ context.meeting.title }}</p>
          <div class="header-meta">
            <span>会议日期：{{ dayjs(context.meeting.starts_at).format('YYYY-MM-DD') }}</span>
            <span>分析版本：v{{ context.meeting.verification_version ?? 1 }}</span>
            <span>最近生成：{{ latestCompletedAt ? dayjs(latestCompletedAt).format('MM-DD HH:mm') : '暂无' }}</span>
            <el-tag size="small" effect="light" type="primary">{{ analysisStatusLabels[context.meeting.analysis_status] }}</el-tag>
          </div>
        </div>
        <div class="header-actions">
          <el-button :icon="RefreshRight" @click="refreshRecords">刷新</el-button>
          <el-button type="primary" :icon="DataAnalysis" @click="historyVisible = true">导出历史</el-button>
        </div>
      </div>

      <div class="type-cards">
        <button
          v-for="item in exportTypes"
          :key="item.key"
          class="type-card"
          :class="{ active: activeType === item.key }"
          @click="activeType = item.key"
        >
          <el-icon :size="26"><component :is="item.icon" /></el-icon>
          <strong>{{ item.title }}</strong>
          <span>{{ item.description }}</span>
        </button>
      </div>

      <TextExportPanel v-if="activeType === 'text'" :context="context" @refresh-records="refreshRecords" />
      <PptOutlineEditor v-else-if="activeType === 'ppt'" :context="context" @refresh-records="refreshRecords" />
      <ChartPanel v-else :context="context" @refresh-records="refreshRecords" />
    </template>

    <ExportHistoryDrawer
      v-model="historyVisible"
      :records="records"
      @refresh="refreshRecords"
    />
  </section>
</template>

<style scoped>
.export-page { display: grid; gap: 18px; }
.page-alert { margin-bottom: 4px; }
.export-header { margin-bottom: 0; }
.header-meta { display: flex; align-items: center; flex-wrap: wrap; gap: 14px; margin-top: 12px; color: #70818c; font-size: 13px; }
.header-actions { display: flex; gap: 10px; }
.type-cards { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }
.type-card {
  display: grid;
  gap: 8px;
  padding: 22px 20px;
  border: 1px solid var(--line);
  border-radius: 14px;
  color: #314e62;
  text-align: left;
  background: white;
  box-shadow: 0 4px 16px rgba(17, 67, 93, 0.05);
  cursor: pointer;
  transition: border-color 0.16s, box-shadow 0.16s, transform 0.16s;
}
.type-card .el-icon { color: #168b82; }
.type-card strong { color: #173f58; font-size: 17px; }
.type-card span { color: #7b8c95; font-size: 12px; line-height: 1.5; }
.type-card:hover { transform: translateY(-2px); box-shadow: 0 12px 28px rgba(17, 67, 93, 0.1); }
.type-card.active { border-color: #168b82; box-shadow: 0 0 0 3px rgba(22, 139, 130, 0.14), 0 12px 28px rgba(17, 67, 93, 0.08); }
@media (max-width: 900px) {
  .type-cards { grid-template-columns: 1fr; }
}
</style>
