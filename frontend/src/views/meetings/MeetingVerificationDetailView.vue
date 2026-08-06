<script setup lang="ts">
import dayjs from 'dayjs'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import MeetingInfoPanel from '@/components/MeetingInfoPanel.vue'
import QuestionDialog from '@/components/QuestionDialog.vue'
import QuestionSelectionPanel from '@/components/QuestionSelectionPanel.vue'
import { meetingVerificationApi, normalizeCandidate } from '@/api/meetingVerification'
import { useAuthStore } from '@/stores/auth'
import type {
  AnalysisTask,
  QuestionCandidate,
  QuestionEvidence,
  QuestionGenerationTask,
  VerificationQuestion,
  VerificationQuestionType,
} from '@/types/meetingVerification'
import {
  canEditVerification,
  isBusy,
  isQuestionGenerationActive,
  questionGenerationStageLabel,
  questionGenerationStatusLabels,
} from '@/utils/meetingVerification'
import { analysisStatusLabels } from '@/utils/meeting'
import { selectionTypeCounts } from '@/utils/meetingAnalysis'
import { toApiError } from '@/utils/errors'

type QuestionTypeKey = 'cut_point' | 'open_ended'
const ANALYSIS_ACTIVE_STATUSES = ['QUEUED', 'RUNNING', 'RETRYING']
const isAnalysisActive = (status?: string | null) => ANALYSIS_ACTIVE_STATUSES.includes(String(status ?? '').toUpperCase())

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const meetingId = computed(() => String(route.params.meetingId))
const snapshot = ref<Awaited<ReturnType<typeof meetingVerificationApi.get>>>()
const loading = ref(false)
const saving = ref(false)
const infoExpanded = ref(false)
const loadError = ref('')
const dialogVisible = ref(false)
const dialogType = ref<VerificationQuestionType>('cut_point')
const editingQuestion = ref<VerificationQuestion | null>(null)
const dialogDirty = ref(false)
const dialogRef = ref<InstanceType<typeof QuestionDialog>>()
const dirty = computed(() => dialogDirty.value || saving.value)
const readonly = computed(() => !canEditVerification(auth.user?.role))
const busy = computed(() => isBusy(loading.value, saving.value))

const generationTask = ref<QuestionGenerationTask | null>(null)
const generationError = ref('')
const generationInProgress = computed(() => isQuestionGenerationActive(generationTask.value?.status))
const pollTimer = ref<ReturnType<typeof setTimeout> | null>(null)
let pollToken = 0
let reloadedTaskId: string | null = null

const poolItems = ref<Record<QuestionTypeKey, QuestionCandidate[]>>({ cut_point: [], open_ended: [] })
const nextOffset = ref<Record<QuestionTypeKey, number>>({ cut_point: 0, open_ended: 0 })
const poolTotal = ref<Record<QuestionTypeKey, number>>({ cut_point: 0, open_ended: 0 })
const selectedIds = ref<Set<string>>(new Set())
const swapLoadingId = ref<string | null>(null)
const candidateLoading = ref(false)
const selectionSaveToken = ref(0)

const analysisTask = ref<AnalysisTask | null>(null)
const analysisError = ref('')
const analysisPollTimer = ref<ReturnType<typeof setTimeout> | null>(null)
let analysisPollToken = 0

const evidenceVisible = ref(false)
const evidenceLoading = ref(false)
const evidenceError = ref('')
const selectedQuestion = ref<{ id: string; content: string } | null>(null)
const evidences = ref<QuestionEvidence[]>([])

const manualQuestions = computed<QuestionCandidate[]>(() => {
  const value = snapshot.value
  if (!value) return []
  return [...value.cut_point_questions, ...value.open_ended_questions]
    .filter((question) => !question.candidate_rank)
    .map((question) => normalizeCandidate({
      ...question,
      selected: selectedIds.value.has(question.id),
    }))
    .filter((candidate): candidate is QuestionCandidate => Boolean(candidate))
})
const allQuestions = computed<QuestionCandidate[]>(() => [
  ...poolItems.value.cut_point,
  ...poolItems.value.open_ended,
  ...manualQuestions.value,
])
const displayCutPoints = computed(() => [
  ...poolItems.value.cut_point,
  ...manualQuestions.value.filter((question) => question.question_type === 'cut_point'),
])
const displayOpenEnded = computed(() => [
  ...poolItems.value.open_ended,
  ...manualQuestions.value.filter((question) => question.question_type === 'open_ended'),
])
const selectionCounts = computed(() => selectionTypeCounts(allQuestions.value, selectedIds.value))
const poolExhausted = computed(() => ({
  cut_point: nextOffset.value.cut_point >= poolTotal.value.cut_point,
  open_ended: nextOffset.value.open_ended >= poolTotal.value.open_ended,
}))
const showMoreAvailable = computed(() => ({
  cut_point: nextOffset.value.cut_point >= 5 && poolTotal.value.cut_point > nextOffset.value.cut_point,
  open_ended: nextOffset.value.open_ended >= 5 && poolTotal.value.open_ended > nextOffset.value.open_ended,
}))
const analysisStatus = computed(() => snapshot.value?.meeting.analysis_status ?? 'not_ready')
const analysisLocked = computed(() => ['queued', 'processing', 'succeeded'].includes(analysisStatus.value))
const actionReadonly = computed(() => readonly.value || generationInProgress.value || analysisLocked.value)
const canStart = computed(() => !actionReadonly.value && selectionCounts.value.cutPoint >= 1 && selectionCounts.value.openEnded >= 1)
const analysisSucceeded = computed(() => analysisStatus.value === 'succeeded')
const displayStatus = computed(() => analysisStatusLabels[analysisStatus.value])
const displayStatusType = computed<'success' | 'warning' | 'danger' | 'info' | 'primary'>(() => ({
  succeeded: 'success',
  processing: 'warning',
  queued: 'warning',
  failed: 'danger',
  cancelled: 'info',
  ready: 'primary',
  not_ready: 'info',
})[analysisStatus.value] ?? 'info')

function clearTaskTimer() {
  if (pollTimer.value !== null) {
    clearTimeout(pollTimer.value)
    pollTimer.value = null
  }
}
function stopPolling() {
  pollToken += 1
  clearTaskTimer()
}
function scheduleTaskPoll() {
  clearTaskTimer()
  const token = pollToken
  pollTimer.value = setTimeout(() => {
    if (token === pollToken) void refreshTask()
  }, 2500)
}
async function refreshTask() {
  const token = pollToken
  generationError.value = ''
  try {
    const task = await meetingVerificationApi.getQuestionGeneration(meetingId.value)
    if (token !== pollToken) return
    generationTask.value = task
    if (!task) {
      stopPolling()
      return
    }
    if (isQuestionGenerationActive(task.status)) {
      scheduleTaskPoll()
      return
    }
    stopPolling()
    if (['PENDING_REVIEW', 'SUCCEEDED'].includes(String(task.status).toUpperCase()) && task.task_id && reloadedTaskId !== task.task_id) {
      reloadedTaskId = task.task_id
      await load()
    }
  } catch (error) {
    if (token !== pollToken) return
    stopPolling()
    generationError.value = toApiError(error).message
  }
}
async function retryGeneration() {
  generationError.value = ''
  try {
    generationTask.value = await meetingVerificationApi.retryQuestionGeneration(meetingId.value)
    reloadedTaskId = null
    pollToken += 1
    scheduleTaskPoll()
  } catch (error) {
    generationError.value = toApiError(error).message
  }
}

function initSelection() {
  const next = new Set<string>()
  for (const question of [...(snapshot.value?.cut_point_questions ?? []), ...(snapshot.value?.open_ended_questions ?? [])]) {
    if (question.analysis_selected) next.add(question.id)
  }
  selectedIds.value = next
}

async function loadCandidates() {
  candidateLoading.value = true
  try {
    for (const type of ['cut_point', 'open_ended'] as const) {
      const page = await meetingVerificationApi.getQuestionCandidates(meetingId.value, type, 0, 5)
      poolItems.value[type] = page.items
      nextOffset.value[type] = page.items.length
      poolTotal.value[type] = page.total
    }
  } catch (error) {
    ElMessage.error(toApiError(error).message)
  } finally {
    candidateLoading.value = false
  }
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    snapshot.value = await meetingVerificationApi.get(meetingId.value)
    initSelection()
    await loadCandidates()
  } catch (error) {
    loadError.value = toApiError(error).message
  } finally {
    loading.value = false
  }
}

async function persistSelection(next: Set<string>) {
  if (!snapshot.value || readonly.value) return
  const token = ++selectionSaveToken.value
  try {
    await meetingVerificationApi.saveAnalysisSelection(meetingId.value, {
      expected_version: snapshot.value.verification_version,
      selected_question_ids: [...next],
    })
  } catch (error) {
    if (token === selectionSaveToken.value) ElMessage.error(toApiError(error).message)
  }
}

function toggleSelect(question: QuestionCandidate, selected: boolean) {
  if (actionReadonly.value) return
  const next = new Set(selectedIds.value)
  if (selected) next.add(question.id)
  else next.delete(question.id)
  selectedIds.value = next
  void persistSelection(next)
}

async function swapCandidate(question: QuestionCandidate) {
  const type = question.question_type
  if (swapLoadingId.value || poolExhausted.value[type] || readonly.value) return
  swapLoadingId.value = question.id
  try {
    const page = await meetingVerificationApi.getQuestionCandidates(meetingId.value, type, nextOffset.value[type], 1)
    const replacement = page.items[0]
    if (!replacement) {
      ElMessage.info('候选池已无更多候选')
      return
    }
    const list = poolItems.value[type]
    const index = list.findIndex((item) => item.id === question.id)
    if (index >= 0) list[index] = replacement
    nextOffset.value[type] += 1
    if (selectedIds.value.has(question.id)) {
      const next = new Set(selectedIds.value)
      next.delete(question.id)
      selectedIds.value = next
      void persistSelection(next)
    }
  } catch (error) {
    ElMessage.error(toApiError(error).message)
  } finally {
    swapLoadingId.value = null
  }
}

async function showMoreCandidates(type: QuestionTypeKey) {
  const offset = nextOffset.value[type]
  const remaining = poolTotal.value[type] - offset
  if (remaining <= 0 || candidateLoading.value) return
  candidateLoading.value = true
  try {
    const page = await meetingVerificationApi.getQuestionCandidates(meetingId.value, type, offset, Math.min(5, remaining))
    const existing = new Set(poolItems.value[type].map((item) => item.id))
    poolItems.value[type] = [...poolItems.value[type], ...page.items.filter((item) => !existing.has(item.id))]
    nextOffset.value[type] = Math.min(poolTotal.value[type], offset + page.items.length)
  } catch (error) {
    ElMessage.error(toApiError(error).message)
  } finally {
    candidateLoading.value = false
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
  analysisError.value = ''
  try {
    const task = await meetingVerificationApi.getAnalysisTask(meetingId.value)
    if (token !== analysisPollToken) return
    analysisTask.value = task
    if (!task) {
      stopAnalysisPolling()
      return
    }
    if (isAnalysisActive(task.status)) {
      scheduleAnalysisPoll()
      return
    }
    stopAnalysisPolling()
    if (String(task.status).toUpperCase() === 'SUCCEEDED') {
      await load()
      ElMessage.success('AI 纪要分析已生成')
    }
  } catch (error) {
    if (token !== analysisPollToken) return
    stopAnalysisPolling()
    analysisError.value = toApiError(error).message
  }
}

async function startAnalysis() {
  if (!snapshot.value || !canStart.value || busy.value) return
  try {
    await ElMessageBox.confirm(
      `将基于已选中的 ${selectionCounts.value.cutPoint} 条切点问题与 ${selectionCounts.value.openEnded} 条开放性问题生成 AI 纪要，提交后问题选择将被锁定。确定开始吗？`,
      '开始 AI 分析',
      { type: 'warning', confirmButtonText: '开始分析', cancelButtonText: '取消' },
    )
    saving.value = true
    const result = await meetingVerificationApi.submitAnalysis(meetingId.value, {
      expected_version: snapshot.value.verification_version,
      selected_question_ids: [...selectedIds.value],
    })
    snapshot.value = result.verification
    ElMessage.success(result.message || 'AI 分析已提交')
    analysisPollToken += 1
    analysisTask.value = await meetingVerificationApi.getAnalysisTask(meetingId.value)
    scheduleAnalysisPoll()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(toApiError(error).message)
  } finally {
    saving.value = false
  }
}

function startAdd(type: VerificationQuestionType) {
  dialogType.value = type
  editingQuestion.value = null
  dialogDirty.value = false
  dialogVisible.value = true
}
function startEdit(question: QuestionCandidate) {
  editingQuestion.value = {
    id: question.id,
    question_type: question.question_type,
    content: question.content,
    version: question.version,
  }
  dialogType.value = question.question_type
  dialogDirty.value = false
  dialogVisible.value = true
}
async function saveQuestion(payload: { content: string; question?: VerificationQuestion; question_type: VerificationQuestionType }) {
  if (!snapshot.value) return
  saving.value = true
  try {
    if (payload.question) {
      await meetingVerificationApi.updateQuestion(meetingId.value, payload.question.id, {
        content: payload.content,
        expected_version: payload.question.version,
      })
    } else {
      await meetingVerificationApi.createQuestion(meetingId.value, {
        content: payload.content,
        question_type: payload.question_type,
      })
    }
    dialogDirty.value = false
    dialogVisible.value = false
    await load()
    ElMessage.success('问题已即时保存')
  } catch (error) {
    ElMessage.error(toApiError(error).message)
  } finally {
    saving.value = false
  }
}
async function removeQuestion(question: QuestionCandidate) {
  if (!snapshot.value) return
  try {
    await ElMessageBox.confirm('删除后无法恢复，确定删除这条问题吗？', '确认删除', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
    saving.value = true
    await meetingVerificationApi.removeQuestion(meetingId.value, question.id, question.version)
    await load()
    ElMessage.success('问题已删除')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(toApiError(error).message)
  } finally {
    saving.value = false
  }
}
async function showEvidence(question: { id: string; content: string }) {
  selectedQuestion.value = question
  evidences.value = []
  evidenceError.value = ''
  evidenceVisible.value = true
  evidenceLoading.value = true
  try {
    evidences.value = await meetingVerificationApi.getQuestionEvidences(meetingId.value, question.id)
  } catch (error) {
    evidenceError.value = toApiError(error).message
  } finally {
    evidenceLoading.value = false
  }
}
function onDraftChange(value: boolean) {
  dialogDirty.value = value
}
function beforeUnload(event: BeforeUnloadEvent) {
  if (dirty.value) {
    event.preventDefault()
    event.returnValue = ''
  }
}
async function allowNavigation() {
  if (!dirty.value) return true
  try {
    await ElMessageBox.confirm('当前操作尚未完成，离开将丢失未保存内容。确定离开吗？', '提示', {
      type: 'warning',
      confirmButtonText: '离开',
      cancelButtonText: '留下',
    })
    return true
  } catch {
    return false
  }
}
watch(meetingId, () => {
  stopPolling()
  stopAnalysisPolling()
  generationTask.value = null
  generationError.value = ''
  analysisTask.value = null
  analysisError.value = ''
  reloadedTaskId = null
  snapshot.value = undefined
  dialogVisible.value = false
  dialogDirty.value = false
  editingQuestion.value = null
  void (async () => {
    await load()
    await refreshTask()
    await refreshAnalysisTask()
  })()
}, { immediate: true })
onMounted(() => window.addEventListener('beforeunload', beforeUnload))
onBeforeUnmount(() => {
  stopPolling()
  stopAnalysisPolling()
  window.removeEventListener('beforeunload', beforeUnload)
})
onBeforeRouteUpdate(allowNavigation)
onBeforeRouteLeave(allowNavigation)
</script>

<template>
  <section v-loading="loading" class="verification-page">
    <el-alert v-if="loadError" type="error" :closable="false" :title="loadError" show-icon>
      <template #default><el-button size="small" @click="load">重试</el-button></template>
    </el-alert>
    <template v-if="snapshot">
      <div class="flow-hint">选择会议 <span>→</span> 核验基本信息 <span>→</span> 选择带入分析的问题 <span>→</span> AI 纪要分析</div>
      <div class="page-header">
        <div>
          <el-button link @click="router.push({ name: 'meeting-review' })">← 返回列表</el-button>
          <p class="eyebrow">MEETING VERIFICATION</p>
          <h1 class="page-title">{{ snapshot.meeting.title }}</h1>
          <p class="page-subtitle">最近更新：{{ dayjs(snapshot.meeting.updated_at).format('YYYY-MM-DD HH:mm') }}</p>
        </div>
        <div class="detail-header-actions">
          <el-tag :type="displayStatusType" size="large">{{ displayStatus }}</el-tag>
          <el-button :disabled="!analysisSucceeded" type="primary" @click="router.push({ name: 'meeting-analysis', params: { meetingId } })">AI 纪要分析</el-button>
        </div>
      </div>
      <div class="detail-stack">
        <MeetingInfoPanel :meeting="snapshot.meeting" :expanded="infoExpanded" @toggle="infoExpanded = !infoExpanded" />
        <el-card v-if="generationTask && generationInProgress" class="generation-card" shadow="never">
          <div class="generation-heading">
            <div>
              <strong>{{ questionGenerationStatusLabels[generationTask.status as keyof typeof questionGenerationStatusLabels] || generationTask.status }}</strong>
              <p>{{ generationTask.message || questionGenerationStageLabel(generationTask.current_stage) || '正在处理，请稍候…' }}</p>
            </div>
            <el-button text @click="refreshTask">刷新</el-button>
          </div>
          <el-progress :percentage="generationTask.progress" />
          <p v-if="generationTask.current_stage" class="generation-stage">{{ questionGenerationStageLabel(generationTask.current_stage) }}</p>
        </el-card>
        <el-alert v-if="generationTask && ['FAILED', 'CANCELLED'].includes(String(generationTask.status).toUpperCase())" class="generation-alert" :type="generationTask.status === 'FAILED' ? 'error' : 'warning'" :closable="false" :title="questionGenerationStatusLabels[generationTask.status as keyof typeof questionGenerationStatusLabels] || generationTask.status">
          <template #default>
            <p>{{ generationTask.error_message || (generationTask.status === 'CANCELLED' ? '任务已取消。' : '生成问题时遇到暂时性错误，请重试。') }}</p>
            <div class="generation-actions">
              <el-button v-if="generationTask.status === 'FAILED'" size="small" type="primary" @click="retryGeneration">重新生成</el-button>
              <el-button size="small" @click="router.push({ name: 'meeting-review' })">返回会议列表</el-button>
            </div>
          </template>
        </el-alert>
        <el-alert v-if="generationError" class="generation-alert" type="warning" :closable="false" title="问题生成状态暂时不可用">
          <template #default><p>{{ generationError }}</p><el-button size="small" @click="refreshTask">刷新</el-button></template>
        </el-alert>

        <QuestionSelectionPanel
          :cut-points="displayCutPoints"
          :open-ended="displayOpenEnded"
          :selected-ids="selectedIds"
          :swap-loading-id="swapLoadingId"
          :pool-exhausted="poolExhausted"
          :show-more-available="showMoreAvailable"
          :readonly="actionReadonly"
          :saving="saving || candidateLoading"
          @select="toggleSelect"
          @swap="swapCandidate"
          @show-more="showMoreCandidates"
          @add="startAdd"
          @edit="startEdit"
          @remove="removeQuestion"
          @evidence="showEvidence"
        />

        <el-card v-if="analysisTask && isAnalysisActive(analysisTask.status)" class="generation-card analysis-card" shadow="never">
          <div class="generation-heading">
            <div>
              <strong>AI 纪要分析{{ analysisTask.status === 'RETRYING' ? '（重试中）' : '' }}</strong>
              <p>{{ analysisTask.message || '正在检索会议与知识库并生成纪要分析，请稍候…' }}</p>
            </div>
            <el-button text @click="refreshAnalysisTask">刷新</el-button>
          </div>
          <el-progress :percentage="analysisTask.progress" />
          <p class="generation-stage">{{ analysisTask.current_stage }}</p>
        </el-card>
        <el-alert v-if="analysisTask && ['FAILED', 'CANCELLED'].includes(String(analysisTask.status).toUpperCase())" class="generation-alert" :type="analysisTask.status === 'FAILED' ? 'error' : 'warning'" :closable="false" title="AI 纪要分析未完成">
          <template #default>
            <p>{{ analysisTask.error_message || (analysisTask.status === 'CANCELLED' ? '任务已取消。' : '生成分析时遇到暂时性错误，请重试。') }}</p>
            <div class="generation-actions">
              <el-button size="small" type="primary" :disabled="!canStart" :loading="saving" @click="startAnalysis">重新开始分析</el-button>
            </div>
          </template>
        </el-alert>
        <el-alert v-if="analysisError" class="generation-alert" type="warning" :closable="false" title="分析任务状态暂时不可用">
          <template #default><p>{{ analysisError }}</p><el-button size="small" @click="refreshAnalysisTask">刷新</el-button></template>
        </el-alert>

        <div class="action-bar">
          <div>
            <strong>AI 分析输入</strong>
            <p v-if="analysisSucceeded">分析已完成，可进入 AI 纪要分析页查看结果。</p>
            <p v-else-if="analysisLocked">分析任务已提交，输入已锁定，请等待生成完成。</p>
            <p v-else-if="!canStart">已选中 {{ selectionCounts.cutPoint }} 条切点问题、{{ selectionCounts.openEnded }} 条开放性问题；两类均需至少选中 1 条才能开始。</p>
            <p v-else>已选中 {{ selectionCounts.cutPoint }} 条切点问题、{{ selectionCounts.openEnded }} 条开放性问题，可以开始 AI 分析。</p>
          </div>
          <div class="action-buttons">
            <el-button v-if="analysisSucceeded" type="success" @click="router.push({ name: 'meeting-analysis', params: { meetingId } })">查看 AI 纪要</el-button>
            <el-button v-else type="primary" :disabled="!canStart || busy" :loading="saving" @click="startAnalysis">开始 AI 分析</el-button>
          </div>
        </div>
      </div>
    </template>
    <el-empty v-else-if="!loading && !loadError" description="暂无核验数据" />
    <QuestionDialog ref="dialogRef" v-model="dialogVisible" :question="editingQuestion" :type="dialogType" :loading="saving" @save="saveQuestion" @draft-change="onDraftChange" />
    <el-drawer v-model="evidenceVisible" title="问题来源" size="min(560px, 94vw)">
      <template v-if="selectedQuestion">
        <p class="evidence-question">{{ selectedQuestion.content }}</p>
        <el-skeleton v-if="evidenceLoading" :rows="5" animated />
        <el-alert v-else-if="evidenceError" type="warning" :closable="false" :title="evidenceError" />
        <el-empty v-else-if="!evidences.length" description="暂无可展示的来源" />
        <el-collapse v-else accordion>
          <el-collapse-item v-for="(evidence, index) in evidences" :key="`${evidence.document_title || 'evidence'}-${index}`" :name="index">
            <template #title><span class="evidence-title">{{ evidence.document_title || '原始证据' }}<small v-if="evidence.section_title"> · {{ evidence.section_title }}</small></span></template>
            <div class="evidence-body">
              <p class="evidence-label">原文</p>
              <p class="quote-text">{{ evidence.quote || evidence.chunk_text || '暂无原文' }}</p>
              <p v-if="evidence.evidence_summary && evidence.evidence_summary !== evidence.quote" class="evidence-summary">证据摘要：{{ evidence.evidence_summary }}</p>
              <el-collapse><el-collapse-item title="展开完整片段" name="chunk"><p class="chunk-text">{{ evidence.chunk_text || evidence.quote || '暂无片段' }}</p></el-collapse-item></el-collapse>
              <div class="evidence-scores">
                <span v-if="evidence.vector_score !== null">向量分数：{{ evidence.vector_score.toFixed(3) }}</span>
                <span v-if="evidence.keyword_score !== null">关键词分数：{{ evidence.keyword_score.toFixed(3) }}</span>
                <span v-if="evidence.rerank_score !== null">重排分数：{{ evidence.rerank_score.toFixed(3) }}</span>
              </div>
            </div>
          </el-collapse-item>
        </el-collapse>
      </template>
    </el-drawer>
  </section>
</template>

<style scoped>
.verification-page { max-width: 1050px; margin: 0 auto; }
.detail-stack { display: grid; gap: 18px; }
.flow-hint { padding: 10px 14px; margin-bottom: 14px; border-radius: 8px; color: #52727c; background: #edf7f3; font-size: 13px; }
.flow-hint span { padding: 0 8px; color: #168b82; font-weight: 700; }
.page-header { align-items: flex-start; }
.page-header .el-button { padding-left: 0; }
.detail-header-actions { display: grid; justify-items: end; gap: 12px; }
.generation-card { border: 1px solid #b9dedd; background: #f4fbfa; }
.analysis-card { border-color: #cdc3ee; background: #f8f6fd; }
.generation-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; margin-bottom: 14px; }
.generation-heading strong { color: #176b69; }
.analysis-card .generation-heading strong { color: #5b3fa8; }
.generation-heading p { margin: 5px 0 0; color: #5d7d81; font-size: 13px; }
.generation-stage { margin: 8px 0 0; color: #7a9598; font-size: 12px; }
.generation-alert p { margin: 0 0 10px; }
.generation-actions { display: flex; gap: 8px; }
.action-bar { display: flex; align-items: center; justify-content: space-between; gap: 18px; padding: 16px 20px; border: 1px solid #cfe7df; border-radius: 12px; background: #f2faf7; }
.action-bar strong { color: #164d5f; }
.action-bar p { margin: 5px 0 0; color: #71888d; font-size: 12px; }
.action-buttons { display: flex; gap: 8px; white-space: nowrap; }
.evidence-question { padding: 12px 14px; margin: 0 0 18px; border-radius: 8px; color: #2f5060; background: #f2f8f6; line-height: 1.55; }
.evidence-title { color: #264f62; }
.evidence-title small { color: #84989d; }
.evidence-label { margin: 0 0 6px; color: #718a91; font-size: 12px; font-weight: 700; }
.quote-text { padding: 12px; margin: 0 0 12px; border-left: 3px solid #168b82; border-radius: 4px; color: #254e5d; background: #f3faf7; white-space: pre-wrap; line-height: 1.65; }
.evidence-summary { color: #456775; line-height: 1.6; }
.chunk-text { max-height: 230px; overflow: auto; padding: 10px; margin: 0; border-radius: 6px; color: #557078; background: #f7faf9; white-space: pre-wrap; line-height: 1.55; }
.evidence-scores { display: flex; flex-wrap: wrap; gap: 12px; padding-top: 10px; color: #819497; font-size: 12px; }
@media (max-width: 700px) {
  .action-bar { align-items: stretch; flex-direction: column; }
  .action-buttons { flex-wrap: wrap; }
}
</style>
