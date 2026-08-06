<script setup lang="ts">
import { Back, Download, RefreshRight } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import ChatPanel from '@/components/analysis/ChatPanel.vue'
import MinutesCard from '@/components/analysis/MinutesCard.vue'
import SourceDrawer from '@/components/analysis/SourceDrawer.vue'
import { exportMeetingMinutes, getAnalysisModules, loadAnalysisContext, reanalyzeMeeting } from '@/api/meetingAnalysis'
import type { AnalysisModule, MeetingAnalysisContext, RagSource } from '@/types/meetingAnalysis'
import { meetingVerificationApi } from '@/api/meetingVerification'
import { composeMinutesDocument } from '@/utils/meetingAnalysis'
import { toApiError } from '@/utils/errors'
import { analysisStatusLabels } from '@/utils/meeting'
import { attendeeCount } from '@/utils/meetingVerification'

const route = useRoute()
const router = useRouter()
const meetingId = computed(() => String(route.params.meetingId))

const context = ref<MeetingAnalysisContext | null>(null)
const loading = ref(false)
const loadError = ref('')
const modules = ref<AnalysisModule[]>([])
const modulesLoading = ref(false)
const modulesError = ref('')
const analysisMissing = ref(false)
const analysisTask = ref<{ task_id: string; status: string; progress?: number; message?: string | null } | null>(null)
const analysisPollTimer = ref<ReturnType<typeof setTimeout> | null>(null)
let analysisPollToken = 0
const reanalyzing = ref(false)
const exporting = ref(false)
const selectedSource = ref<RagSource | null>(null)
const chatPanelRef = ref<InstanceType<typeof ChatPanel>>()

let requestToken = 0

const analysisStatus = computed(() => context.value?.meeting.analysis_status ?? 'not_ready')
const analysisBusy = computed(() =>
  analysisTask.value !== null &&
  ['QUEUED', 'RUNNING', 'RETRYING'].includes(String(analysisTask.value.status).toUpperCase()),
)
const statusTagType = computed(() => {
  const map: Record<string, 'success' | 'warning' | 'danger' | 'info' | 'primary'> = {
    succeeded: 'success',
    processing: 'warning',
    queued: 'warning',
    failed: 'danger',
    cancelled: 'info',
    ready: 'primary',
    not_ready: 'info',
  }
  return map[analysisStatus.value] ?? 'info'
})

const minutesModule = computed<AnalysisModule | null>(() => modules.value[0] ?? null)

async function load() {
  const token = ++requestToken
  loading.value = true
  loadError.value = ''
  modulesError.value = ''
  context.value = null
  modules.value = []
  try {
    const nextContext = await loadAnalysisContext(meetingId.value)
    if (token !== requestToken) return
    context.value = nextContext
    await loadModules(token)
    if (token === requestToken) void ensureAnalysisPolling().catch(() => undefined)
  } catch (error) {
    if (token !== requestToken) return
    loadError.value = toApiError(error).message
  } finally {
    if (token === requestToken) loading.value = false
  }
}

async function loadModules(token = requestToken) {
  modulesLoading.value = true
  modulesError.value = ''
  analysisMissing.value = false
  try {
    const next = await getAnalysisModules(meetingId.value)
    if (token !== requestToken) return
    const minutes = composeMinutesDocument(next)
    modules.value = minutes ? [minutes] : []
  } catch (error) {
    if (token !== requestToken) return
    const apiError = toApiError(error)
    if (apiError.status === 404) {
      analysisMissing.value = true
      modules.value = []
    } else {
      modulesError.value = apiError.message
    }
  } finally {
    if (token === requestToken) modulesLoading.value = false
  }
}

async function reanalyze() {
  if (!context.value || reanalyzing.value) return
  reanalyzing.value = true
  modulesError.value = ''
  analysisMissing.value = false
  try {
    const task = await reanalyzeMeeting(meetingId.value, context.value)
    analysisTask.value = { task_id: task.task_id, status: task.status, progress: 0, message: '已重新提交分析' }
    analysisPollToken += 1
    scheduleAnalysisPoll()
    ElMessage.success('已重新提交 AI 分析，生成完成后自动刷新')
  } catch (error) {
    modulesError.value = toApiError(error).message
    ElMessage.error(modulesError.value)
  } finally {
    reanalyzing.value = false
  }
}

function clearAnalysisTimer() {
  if (analysisPollTimer.value !== null) {
    clearTimeout(analysisPollTimer.value)
    analysisPollTimer.value = null
  }
}
function stopAnalysisPolling() {
  analysisPollToken += 1
  clearAnalysisTimer()
}
function scheduleAnalysisPoll() {
  clearAnalysisTimer()
  const token = analysisPollToken
  analysisPollTimer.value = setTimeout(() => {
    if (token === analysisPollToken) void refreshAnalysisTask()
  }, 2500)
}
async function refreshAnalysisTask() {
  const token = analysisPollToken
  try {
    const task = await meetingVerificationApi.getAnalysisTask(meetingId.value)
    if (token !== analysisPollToken) return
    if (!task) {
      stopAnalysisPolling()
      return
    }
    analysisTask.value = { task_id: task.task_id, status: task.status, progress: task.progress, message: task.message }
    if (['QUEUED', 'RUNNING', 'RETRYING'].includes(String(task.status).toUpperCase())) {
      scheduleAnalysisPoll()
      return
    }
    stopAnalysisPolling()
    if (String(task.status).toUpperCase() === 'SUCCEEDED') {
      await load()
      ElMessage.success('AI 纪要分析已更新')
    }
  } catch (error) {
    if (token !== analysisPollToken) return
    stopAnalysisPolling()
    modulesError.value = toApiError(error).message
  }
}

async function ensureAnalysisPolling() {
  const task = await meetingVerificationApi.getAnalysisTask(meetingId.value)
  if (!task) return
  analysisTask.value = { task_id: task.task_id, status: task.status, progress: task.progress, message: task.message }
  if (['QUEUED', 'RUNNING', 'RETRYING'].includes(String(task.status).toUpperCase())) {
    analysisPollToken += 1
    scheduleAnalysisPoll()
  }
}

async function exportMinutes() {
  if (!context.value || exporting.value) return
  exporting.value = true
  try {
    const conversation = chatPanelRef.value?.getConversation() ?? []
    await exportMeetingMinutes(context.value, modules.value, conversation)
    ElMessage.success('纪要已导出')
  } catch (error) {
    ElMessage.error(toApiError(error).message)
  } finally {
    exporting.value = false
  }
}

function openSource(source: RagSource) {
  selectedSource.value = source
}

function onAppToast(event: Event) {
  const detail = (event as CustomEvent<string>).detail
  if (detail) ElMessage.success(detail)
}

watch(meetingId, () => load(), { immediate: true })
onBeforeRouteUpdate(() => {
  requestToken += 1
  return true
})
onMounted(() => window.addEventListener('app-toast', onAppToast))
onBeforeUnmount(() => {
  requestToken += 1
  stopAnalysisPolling()
  window.removeEventListener('app-toast', onAppToast)
})
</script>

<template>
  <section v-loading="loading" class="analysis-page">
    <el-alert v-if="loadError" type="error" :closable="false" :title="loadError" show-icon class="page-alert">
      <template #default><el-button size="small" @click="load">重试</el-button></template>
    </el-alert>

    <template v-if="context">
      <div class="page-header analysis-header">
        <div class="header-left">
          <el-button link type="primary" :icon="Back" @click="router.push({ name: 'meeting-review' })">返回会议管理</el-button>
          <p class="eyebrow">AI MEETING ANALYSIS</p>
          <h1 class="page-title">AI 纪要分析</h1>
          <p class="page-subtitle">{{ context.meeting.title }}</p>
          <div class="header-meta">
            <span>日期：{{ dayjs(context.meeting.starts_at).format('YYYY-MM-DD') }}</span>
            <span>时间：{{ dayjs(context.meeting.starts_at).format('HH:mm') }} - {{ dayjs(context.meeting.ends_at).format('HH:mm') }}</span>
            <span>参会人数：{{ attendeeCount(context.meeting) || '未提供' }}</span>
            <el-tag :type="statusTagType" size="small" effect="light">{{ analysisStatusLabels[analysisStatus] }}</el-tag>
            <span class="chat-mode-note">{{ context.knowledgeBaseName ? `已连接知识库：${context.knowledgeBaseName}` : '未连接知识库' }}</span>
          </div>
        </div>
        <div class="header-actions">
          <el-button :icon="RefreshRight" :loading="reanalyzing" :disabled="analysisStatus === 'not_ready' || analysisStatus === 'ready' || analysisBusy" @click="reanalyze">重新分析</el-button>
          <el-button type="primary" :icon="Download" :loading="exporting" @click="exportMinutes">导出纪要</el-button>
        </div>
      </div>

      <el-card v-if="analysisTask && ['QUEUED', 'RUNNING', 'RETRYING'].includes(String(analysisTask.status).toUpperCase())" class="analysis-progress-card" shadow="never">
        <div class="analysis-progress-head">
          <div><strong>AI 纪要分析生成中</strong><p>{{ analysisTask.message || '正在检索会议与知识库并生成分析结果，请稍候…' }}</p></div>
          <el-button text @click="refreshAnalysisTask">刷新</el-button>
        </div>
        <el-progress :percentage="analysisTask.progress ?? 0" />
      </el-card>
      <el-alert v-if="analysisTask && ['FAILED', 'CANCELLED'].includes(String(analysisTask.status).toUpperCase())" type="error" :closable="false" title="AI 纪要分析未完成" show-icon>
        <template #default>
          <p class="task-error">{{ analysisTask.message || '生成失败，请返回核验页重新开始分析。' }}</p>
          <el-button size="small" type="primary" @click="reanalyze">重新分析</el-button>
        </template>
      </el-alert>

      <div class="analysis-grid">
        <main class="analysis-main">
          <el-alert v-if="modulesError" type="error" :closable="false" :title="modulesError" show-icon>
            <template #default><el-button size="small" @click="loadModules">重试</el-button></template>
          </el-alert>
          <div v-if="modulesLoading && !modules.length" class="module-skeletons">
            <el-skeleton v-for="index in 3" :key="index" :rows="3" animated class="skeleton-card" />
          </div>
          <el-empty v-else-if="analysisMissing && !modules.length" description="该会议尚未生成 AI 纪要分析">
            <template #description>
              <div class="missing-analysis">
                <p>请先在核验页选择带入分析的切点问题与开放性问题，并点击“开始 AI 分析”。</p>
                <el-button type="primary" @click="router.push({ name: 'meeting-review-detail', params: { meetingId } })">前往核验页选择问题</el-button>
              </div>
            </template>
          </el-empty>
          <template v-else>
            <MinutesCard v-if="minutesModule" :module="minutesModule" @open-source="openSource" />
            <el-empty v-else description="暂无分析结果" />
          </template>
        </main>

        <aside class="analysis-chat">
          <ChatPanel ref="chatPanelRef" :context="context" @open-source="openSource" />
        </aside>
      </div>

      <SourceDrawer :source="selectedSource" @close="selectedSource = null" />
    </template>
  </section>
</template>

<style scoped>
.analysis-page { display: grid; gap: 18px; }
.page-alert { margin-bottom: 4px; }
.analysis-header { margin-bottom: 0; }
.header-meta { display: flex; align-items: center; flex-wrap: wrap; gap: 14px; margin-top: 12px; color: #70818c; font-size: 13px; }
.header-meta span { white-space: nowrap; }
.header-meta .chat-mode-note { color: #6c4fd0; font-size: 12px; }
.header-actions { display: flex; gap: 10px; }
.analysis-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(340px, 34%); gap: 22px; align-items: start; }
.analysis-main { display: grid; gap: 16px; min-width: 0; }
.analysis-chat { position: sticky; top: 20px; height: calc(100vh - 40px); min-height: 560px; }
.module-skeletons { display: grid; gap: 16px; }
.skeleton-card { padding: 18px 20px; border: 1px solid var(--line); border-radius: 12px; background: white; }
.analysis-progress-card { border: 1px solid #cdc3ee; border-radius: 12px; background: #f8f6fd; }
.analysis-progress-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; margin-bottom: 12px; }
.analysis-progress-head strong { color: #5b3fa8; }
.analysis-progress-head p { margin: 5px 0 0; color: #7a6fb0; font-size: 13px; }
.task-error { margin: 0 0 10px; }
.missing-analysis { display: grid; gap: 10px; justify-items: center; }
.missing-analysis p { margin: 0; color: #6f7a90; font-size: 13px; }
@media (max-width: 1180px) {
  .analysis-grid { grid-template-columns: 1fr; }
  .analysis-chat { position: static; height: min(74vh, 660px); }
}
@media (max-width: 640px) {
  .header-actions { flex-wrap: wrap; }
}
</style>
