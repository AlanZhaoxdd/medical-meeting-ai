<script setup lang="ts">
import dayjs from 'dayjs'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import MeetingInfoPanel from '@/components/MeetingInfoPanel.vue'
import QuestionDialog from '@/components/QuestionDialog.vue'
import QuestionList from '@/components/QuestionList.vue'
import ReviewActionBar from '@/components/ReviewActionBar.vue'
import { meetingVerificationApi } from '@/api/meetingVerification'
import { useAuthStore } from '@/stores/auth'
import type { QuestionEvidence, QuestionGenerationTask, VerificationQuestion, VerificationQuestionType } from '@/types/meetingVerification'
import { canEditVerification, isBusy, isQuestionGenerationActive, missingConditionLabels, questionGenerationStageLabel, questionGenerationStatusLabels, verificationStatusLabels, verificationStatusType } from '@/utils/meetingVerification'
import { toApiError } from '@/utils/errors'

const route = useRoute(); const router = useRouter(); const auth = useAuthStore()
const meetingId = computed(() => String(route.params.meetingId))
const snapshot = ref<Awaited<ReturnType<typeof meetingVerificationApi.get>>>()
const loading = ref(false); const saving = ref(false); const infoExpanded = ref(false); const loadError = ref('')
const dialogVisible = ref(false); const dialogType = ref<VerificationQuestionType>('cut_point'); const editingQuestion = ref<VerificationQuestion | null>(null); const dialogDirty = ref(false)
const dialogRef = ref<InstanceType<typeof QuestionDialog>>()
const dirty = computed(() => dialogDirty.value || saving.value)
const readonly = computed(() => !canEditVerification(auth.user?.role))
const busy = computed(() => isBusy(loading.value, saving.value))
const analysisLocked = computed(() => Boolean(snapshot.value && !['not_ready', 'ready'].includes(snapshot.value.meeting.analysis_status)))
const generationTask = ref<QuestionGenerationTask | null>(null)
const generationError = ref('')
const generationInProgress = computed(() => isQuestionGenerationActive(generationTask.value?.status))
const actionReadonly = computed(() => readonly.value || analysisLocked.value || generationInProgress.value)
const pollTimer = ref<ReturnType<typeof setTimeout> | null>(null)
let pollToken = 0
let reloadedTaskId: string | null = null
const evidenceVisible = ref(false)
const evidenceLoading = ref(false)
const evidenceError = ref('')
const selectedQuestion = ref<VerificationQuestion | null>(null)
const evidences = ref<QuestionEvidence[]>([])
const displayStatus = computed(() => snapshot.value?.meeting.analysis_status === 'queued' ? '已提交分析' : verificationStatusLabels[snapshot.value?.meeting.verification_status ?? 'pending'])
const displayStatusType = computed(() => snapshot.value?.meeting.analysis_status === 'queued' ? 'success' : verificationStatusType(snapshot.value?.meeting.verification_status ?? 'pending'))

function clearTaskTimer() { if (pollTimer.value !== null) { clearTimeout(pollTimer.value); pollTimer.value = null } }
function stopPolling() { pollToken += 1; clearTaskTimer() }
function scheduleTaskPoll() {
  clearTaskTimer()
  const token = pollToken
  pollTimer.value = setTimeout(() => { if (token === pollToken) void refreshTask() }, 2500)
}
async function refreshTask() {
  const token = pollToken
  generationError.value = ''
  try {
    const task = await meetingVerificationApi.getQuestionGeneration(meetingId.value)
    if (token !== pollToken) return
    generationTask.value = task
    if (!task) { stopPolling(); return }
    if (isQuestionGenerationActive(task.status)) { scheduleTaskPoll(); return }
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
  } catch (error) { generationError.value = toApiError(error).message }
}
async function showEvidence(question: VerificationQuestion) {
  selectedQuestion.value = question; evidences.value = []; evidenceError.value = ''; evidenceVisible.value = true; evidenceLoading.value = true
  try { evidences.value = question.evidences?.length ? question.evidences : await meetingVerificationApi.getQuestionEvidences(meetingId.value, question.id) } catch (error) { evidenceError.value = toApiError(error).message } finally { evidenceLoading.value = false }
}

async function load() { loading.value = true; loadError.value = ''; try { snapshot.value = await meetingVerificationApi.get(meetingId.value) } catch (error) { loadError.value = toApiError(error).message } finally { loading.value = false } }
function startAdd(type: VerificationQuestionType) { dialogType.value = type; editingQuestion.value = null; dialogDirty.value = false; dialogVisible.value = true }
function startEdit(question: VerificationQuestion) { editingQuestion.value = question; dialogType.value = question.question_type; dialogDirty.value = false; dialogVisible.value = true }
async function saveQuestion(payload: { content: string; question?: VerificationQuestion; question_type: VerificationQuestionType }) {
  if (!snapshot.value) return; saving.value = true
  try { if (payload.question) await meetingVerificationApi.updateQuestion(meetingId.value, payload.question.id, { content: payload.content, expected_version: payload.question.version }); else await meetingVerificationApi.createQuestion(meetingId.value, { content: payload.content, question_type: payload.question_type }); dialogDirty.value = false; dialogVisible.value = false; await load(); ElMessage.success('问题已即时保存') } catch (error) { ElMessage.error(toApiError(error).message) } finally { saving.value = false }
}
function saveDraft() { dialogRef.value?.submit() }
async function removeQuestion(question: VerificationQuestion) {
  if (!snapshot.value) return
  try { await ElMessageBox.confirm('删除后无法恢复，确定删除这条问题吗？', '确认删除', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }); saving.value = true; await meetingVerificationApi.removeQuestion(meetingId.value, question.id, question.version); await load(); ElMessage.success('问题已删除') } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error(toApiError(error).message) } finally { saving.value = false }
}
async function confirmVerification() {
  if (!snapshot.value) return
    try { await ElMessageBox.confirm('确认当前会议核验内容吗？确认后仍可继续编辑，编辑会重新进入核验中状态。', '二次确认', { type: 'warning', confirmButtonText: '确认核验', cancelButtonText: '继续编辑' }); saving.value = true; const previous = snapshot.value; const result = await meetingVerificationApi.confirm(meetingId.value, { expected_version: previous.verification_version }); snapshot.value = result.meeting?.id ? result : { ...previous, ...result, meeting: previous.meeting, cut_point_questions: result.cut_point_questions.length ? result.cut_point_questions : previous.cut_point_questions, open_ended_questions: result.open_ended_questions.length ? result.open_ended_questions : previous.open_ended_questions }; ElMessage.success('会议核验已确认') } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error(toApiError(error).message) } finally { saving.value = false }
}
async function submitAnalysis() {
  if (!snapshot.value) return
  try { await ElMessageBox.confirm('AI 分析将基于已确认的问题提交，确定提交吗？', '二次确认', { type: 'warning', confirmButtonText: '提交分析', cancelButtonText: '取消' }); saving.value = true; const result = await meetingVerificationApi.submitAnalysis(meetingId.value, { expected_version: snapshot.value.verification_version }); snapshot.value = result.verification; ElMessage.success(result.message || '会议核验已完成，AI 分析功能将在下一阶段接入。') } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error(toApiError(error).message) } finally { saving.value = false }
}
function onDraftChange(value: boolean) { dialogDirty.value = value }
function beforeUnload(event: BeforeUnloadEvent) { if (dirty.value) { event.preventDefault(); event.returnValue = '' } }
async function allowNavigation() {
  if (!dirty.value) return true
  try {
    await ElMessageBox.confirm('当前操作尚未完成，离开将丢失未保存内容。确定离开吗？', '提示', { type: 'warning', confirmButtonText: '离开', cancelButtonText: '留下' })
    return true
  } catch {
    return false
  }
}
watch(meetingId, () => {
  stopPolling()
  generationTask.value = null
  generationError.value = ''
  reloadedTaskId = null
  snapshot.value = undefined
  dialogVisible.value = false
  dialogDirty.value = false
  editingQuestion.value = null
  void (async () => { await load(); await refreshTask() })()
}, { immediate: true })
onMounted(() => window.addEventListener('beforeunload', beforeUnload))
onBeforeUnmount(() => { stopPolling(); window.removeEventListener('beforeunload', beforeUnload) })
onBeforeRouteUpdate(allowNavigation)
onBeforeRouteLeave(allowNavigation)
</script>

<template>
  <section v-loading="loading" class="verification-page">
    <el-alert v-if="loadError" type="error" :closable="false" :title="loadError" show-icon><template #default><el-button size="small" @click="load">重试</el-button></template></el-alert>
    <template v-if="snapshot">
      <div class="flow-hint">选择会议 <span>→</span> 核验基本信息 / 场次 / 切点问题 / 开放性问题 <span>→</span> AI 分析</div>
      <div class="page-header"><div><el-button link @click="router.push({ name: 'meeting-review' })">← 返回列表</el-button><p class="eyebrow">MEETING VERIFICATION</p><h1 class="page-title">{{ snapshot.meeting.title }}</h1><p class="page-subtitle">最近更新：{{ dayjs(snapshot.meeting.updated_at).format('YYYY-MM-DD HH:mm') }}</p></div><el-tag :type="displayStatusType" size="large">{{ displayStatus }}</el-tag></div>
      <div class="detail-stack">
        <MeetingInfoPanel :meeting="snapshot.meeting" :expanded="infoExpanded" @toggle="infoExpanded = !infoExpanded" />
        <el-card v-if="generationTask && generationInProgress" class="generation-card" shadow="never">
          <div class="generation-heading"><div><strong>{{ questionGenerationStatusLabels[generationTask.status as keyof typeof questionGenerationStatusLabels] || generationTask.status }}</strong><p>{{ generationTask.message || questionGenerationStageLabel(generationTask.current_stage) || '正在处理，请稍候…' }}</p></div><el-button text @click="refreshTask">刷新</el-button></div>
          <el-progress :percentage="generationTask.progress" :status="generationTask.status === 'RETRYING' ? undefined : undefined" />
          <p v-if="generationTask.current_stage" class="generation-stage">{{ questionGenerationStageLabel(generationTask.current_stage) }}</p>
        </el-card>
        <el-alert v-if="generationTask && ['FAILED', 'CANCELLED'].includes(String(generationTask.status).toUpperCase())" class="generation-alert" :type="generationTask.status === 'FAILED' ? 'error' : 'warning'" :closable="false" :title="questionGenerationStatusLabels[generationTask.status as keyof typeof questionGenerationStatusLabels] || generationTask.status"><template #default><p>{{ generationTask.error_message || (generationTask.status === 'CANCELLED' ? '任务已取消。' : '生成问题时遇到暂时性错误，请重试。') }}</p><div class="generation-actions"><el-button v-if="generationTask.status === 'FAILED'" size="small" type="primary" @click="retryGeneration">重新生成</el-button><el-button size="small" @click="router.push({ name: 'meeting-review' })">返回会议列表</el-button></div></template></el-alert>
        <el-alert v-if="generationError" class="generation-alert" type="warning" :closable="false" title="问题生成状态暂时不可用"><template #default><p>{{ generationError }}</p><el-button size="small" @click="refreshTask">刷新</el-button></template></el-alert>
        <QuestionList :cut-point-questions="snapshot.cut_point_questions" :open-ended-questions="snapshot.open_ended_questions" :readonly="actionReadonly" :saving="saving" @add="startAdd" @edit="startEdit" @remove="removeQuestion" @evidence="showEvidence" />
        <ReviewActionBar :eligibility="snapshot.eligibility" :confirmed="snapshot.meeting.verification_status === 'confirmed'" :readonly="actionReadonly" :dirty="dirty" :loading="busy" @confirm="confirmVerification" @submit-analysis="submitAnalysis" @save="saveDraft" />
        <p v-if="snapshot.eligibility.missing_conditions.length" class="missing-note">{{ missingConditionLabels(snapshot.eligibility.missing_conditions).join('；') }}</p>
      </div>
    </template>
    <el-empty v-else-if="!loading && !loadError" description="暂无核验数据" />
    <QuestionDialog ref="dialogRef" v-model="dialogVisible" :question="editingQuestion" :type="dialogType" :loading="saving" @save="saveQuestion" @draft-change="onDraftChange" />
    <el-drawer v-model="evidenceVisible" title="问题来源" size="min(560px, 94vw)">
      <template v-if="selectedQuestion"><p class="evidence-question">{{ selectedQuestion.content }}</p><el-skeleton v-if="evidenceLoading" :rows="5" animated /><el-alert v-else-if="evidenceError" type="warning" :closable="false" :title="evidenceError" /><el-empty v-else-if="!evidences.length" description="暂无可展示的来源" /><el-collapse v-else accordion><el-collapse-item v-for="(evidence, index) in evidences" :key="`${evidence.document_title || 'evidence'}-${index}`" :name="index"><template #title><span class="evidence-title">{{ evidence.document_title || '原始证据' }}<small v-if="evidence.section_title"> · {{ evidence.section_title }}</small></span></template><div class="evidence-body"><p class="evidence-label">原文</p><p class="quote-text">{{ evidence.quote || evidence.chunk_text || '暂无原文' }}</p><p v-if="evidence.evidence_summary && evidence.evidence_summary !== evidence.quote" class="evidence-summary">证据摘要：{{ evidence.evidence_summary }}</p><el-collapse><el-collapse-item title="展开完整片段" name="chunk"><p class="chunk-text">{{ evidence.chunk_text || evidence.quote || '暂无片段' }}</p></el-collapse-item></el-collapse><div class="evidence-scores"><span v-if="evidence.vector_score !== null">向量分数：{{ evidence.vector_score.toFixed(3) }}</span><span v-if="evidence.keyword_score !== null">关键词分数：{{ evidence.keyword_score.toFixed(3) }}</span><span v-if="evidence.rerank_score !== null">重排分数：{{ evidence.rerank_score.toFixed(3) }}</span></div></div></el-collapse-item></el-collapse></template>
    </el-drawer>
  </section>
</template>

<style scoped>
.verification-page { max-width: 1050px; margin: 0 auto; }.detail-stack { display: grid; gap: 18px; }.missing-note { margin: -9px 2px 0; color: #9b6b31; font-size: 12px; }.flow-hint { padding: 10px 14px; margin-bottom: 14px; border-radius: 8px; color: #52727c; background: #edf7f3; font-size: 13px; }.flow-hint span { padding: 0 8px; color: #168b82; font-weight: 700; }
.page-header { align-items: flex-start; }.page-header .el-button { padding-left: 0; }.page-header .el-tag { margin-top: 28px; }
.generation-card { border: 1px solid #b9dedd; background: #f4fbfa; }.generation-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; margin-bottom: 14px; }.generation-heading strong { color: #176b69; }.generation-heading p { margin: 5px 0 0; color: #5d7d81; font-size: 13px; }.generation-stage { margin: 8px 0 0; color: #7a9598; font-size: 12px; }.generation-alert p { margin: 0 0 10px; }.generation-actions { display: flex; gap: 8px; }.evidence-question { padding: 12px 14px; margin: 0 0 18px; border-radius: 8px; color: #2f5060; background: #f2f8f6; line-height: 1.55; }.evidence-title { color: #264f62; }.evidence-title small { color: #84989d; }.evidence-label { margin: 0 0 6px; color: #718a91; font-size: 12px; font-weight: 700; }.quote-text { padding: 12px; margin: 0 0 12px; border-left: 3px solid #168b82; border-radius: 4px; color: #254e5d; background: #f3faf7; white-space: pre-wrap; line-height: 1.65; }.evidence-summary { color: #456775; line-height: 1.6; }.chunk-text { max-height: 230px; overflow: auto; padding: 10px; margin: 0; border-radius: 6px; color: #557078; background: #f7faf9; white-space: pre-wrap; line-height: 1.55; }.evidence-scores { display: flex; flex-wrap: wrap; gap: 12px; padding-top: 10px; color: #819497; font-size: 12px; }
</style>
