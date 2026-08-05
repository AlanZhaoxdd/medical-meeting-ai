<!-- eslint-disable vue/no-v-html, vue/attributes-order -->
<script setup lang="ts">
/* eslint-disable vue/no-v-html, vue/attributes-order */
import { ArrowLeft, Document, InfoFilled, Search } from '@element-plus/icons-vue'
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { meetingImportsApi } from '@/api/meetingImports'
import { useAuthStore } from '@/stores/auth'
import type { MeetingImportReview, MeetingImportVectorization, ReviewBlock, ReviewMetadata, ReviewMetadataField, ReviewRevision } from '@/types/meetingImport'
import { toApiError } from '@/utils/errors'
import { canEditReview, cleanTranscriptText, escapeHighlightHtml, findLiteralMatches, highlightLiteral, isTableBlock, isVectorizationSynced, nextMatchIndex, parseMarkdownTable, tableBlockText, validateReviewMetadata, vectorizationProgress, vectorizationStatusLabel } from '@/utils/meetingReview'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const importId = computed(() => String(route.params.importId))
const LAST_MEETING_IMPORT_ID = 'latest_meeting_import_id'
const review = ref<MeetingImportReview>()
const loading = ref(true)
const saving = ref(false)
const errorMessage = ref('')
const selectedHistory = ref<ReviewRevision>()
const historyOpen = ref(false)
const localBlocks = ref<ReviewBlock[]>([])
const dirtyBlocks = reactive(new Map<string, string>())
const dirtyMetadata = reactive(new Set<string>())
const pendingTimers = new Map<string, ReturnType<typeof setTimeout>>()
let saveChain: Promise<void> = Promise.resolve()
const conflictMessage = ref('')
const sourceBlockId = ref('')
const editorFocused = ref(false)
const searchOpen = ref(false)
const searchQuery = ref('')
const searchCaseSensitive = ref(false)
const searchScope = ref<'FULL' | 'BLOCK'>('FULL')
const searchBlockId = ref('')
const currentMatch = ref(0)
const lastOperationId = ref('')
const lastReplacementSnapshot = ref<Record<string, { text: string; table_markdown?: string | null }>>({})
const undoVersion = ref(0)
const metadataVersion = ref(1)
const metadata = reactive<ReviewMetadata>({} as ReviewMetadata)
const metadataErrors = ref<Record<string, string>>({})
const confirmLoading = ref(false)
const metadataSaving = ref(false)
const idempotencyKey = ref('')
const lastSavedAt = ref<Date>()
let findTimer: ReturnType<typeof setTimeout> | undefined
let vectorizationTimer: ReturnType<typeof setTimeout> | undefined
let vectorizationPollToken = 0
let vectorizationPollDelay = 1800

const meetingInfoFields = [
  { key: 'title', label: '会议名称', multiline: false },
  { key: 'meeting_purpose', label: '会议目的', multiline: true },
  { key: 'discussion_topics', label: '讨论题目', multiline: true },
  { key: 'meeting_date', label: '会议日期', multiline: false },
  { key: 'advisor_selection_criteria', label: '顾问选择标准', multiline: true },
  { key: 'advisor_names', label: '参会顾问姓名', multiline: true },
  { key: 'internal_attendees', label: '诺和诺德内部参会人及参会原因', multiline: true },
  { key: 'recorder', label: '记录人', multiline: false },
] as const

const editable = computed(() => canEditReview(auth.user?.role))
const revisionEditable = computed(() => editable.value && review.value?.current_revision?.status?.toUpperCase() === 'DRAFT')
const status = computed(() => String(review.value?.status || 'UNKNOWN').toUpperCase())
const isReady = computed(() => status.value === 'READY_FOR_REVIEW')
const hasMeetingDate = computed(() => Boolean(metadata.meeting_date?.value?.trim()))
const blocksForDisplay = computed(() => localBlocks.value)
const matches = computed(() => findLiteralMatches(blocksForDisplay.value, searchQuery.value, { caseSensitive: searchCaseSensitive.value, scope: searchScope.value, blockId: searchBlockId.value }))
const totalMatches = computed(() => matches.value.length)
const current = computed(() => matches.value[currentMatch.value])
const title = computed(() => metadata.title?.value || review.value?.file?.filename || review.value?.import.filename || '会议导入')
const filename = computed(() => review.value?.file?.filename || review.value?.import.filename || '未命名文件')
const needsConfirmation = computed(() => review.value?.needs_confirmation_count || Object.values(metadata).filter((field) => field?.needs_confirmation).length)
const saveStatus = computed(() => saving.value || metadataSaving.value
  ? '保存中…'
  : dirtyBlocks.size || dirtyMetadata.size
    ? '有未保存修改'
    : lastSavedAt.value
      ? `已保存 ${lastSavedAt.value.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
      : '尚未保存')
const vectorization = computed(() => review.value?.vectorization)
const vectorizationVersion = computed(() => review.value?.current_revision?.version)
const vectorizationSynced = computed(() => isVectorizationSynced(vectorization.value, vectorizationVersion.value))
const vectorizationStatus = computed(() => String(vectorization.value?.status || 'PENDING').toUpperCase())
const vectorizationPercent = computed(() => vectorizationProgress(vectorization.value?.progress))
const canConfirm = computed(() => editable.value && isReady.value && Boolean(review.value?.current_revision) && vectorizationSynced.value && !dirtyBlocks.size && !dirtyMetadata.size && !saving.value && !metadataSaving.value)

function normalizeField(field?: ReviewMetadataField) {
  return field || { value: null }
}

function fillReview(data: MeetingImportReview) {
  review.value = data
  localBlocks.value = (data.current_revision?.blocks || []).map((block) => ({ ...block }))
  metadataVersion.value = data.metadata_version
  for (const key of Object.keys(data.meeting_metadata)) metadata[key] = { ...normalizeField(data.meeting_metadata[key]) }
  dirtyBlocks.clear()
  dirtyMetadata.clear()
  lastSavedAt.value = data.import.updated_at ? new Date(data.import.updated_at) : undefined
  conflictMessage.value = ''
  if (data.needs_confirmation_count > 0) void nextTick(() => document.querySelector<HTMLInputElement>('.metadata-form input')?.focus())
}

function setVectorization(value: MeetingImportVectorization) {
  if (review.value) review.value.vectorization = value
}

function stopVectorizationPolling() {
  vectorizationPollToken += 1
  if (vectorizationTimer) clearTimeout(vectorizationTimer)
  vectorizationTimer = undefined
}

function scheduleVectorizationPolling() {
  if (vectorizationTimer || vectorizationStatus.value === 'SYNCED' || vectorizationStatus.value === 'FAILED' || vectorizationStatus.value === 'STALE') return
  const token = vectorizationPollToken
  vectorizationTimer = setTimeout(() => {
    vectorizationTimer = undefined
    if (token === vectorizationPollToken) void refreshVectorization()
  }, vectorizationPollDelay)
}

async function refreshVectorization() {
  if (!review.value?.current_revision) return
  try {
    const latest = await meetingImportsApi.vectorization(importId.value)
    if (!review.value) return
    vectorizationPollDelay = 1800
    setVectorization(latest)
    if (isVectorizationSynced(latest, review.value.current_revision.version) || ['FAILED', 'STALE'].includes(String(latest.status).toUpperCase())) stopVectorizationPolling()
    else scheduleVectorizationPolling()
  } catch {
    vectorizationPollDelay = Math.min(vectorizationPollDelay * 2, 12_000)
    scheduleVectorizationPolling()
  }
}

function ensureVectorization(expectedVersion: number) {
  return meetingImportsApi.vectorize(importId.value, { expected_version: expectedVersion }).then((value) => {
    setVectorization(value)
    return value
  })
}

async function waitForVectorization(expectedVersion: number) {
  stopVectorizationPolling()
  for (let attempt = 0; attempt < 60; attempt += 1) {
    if (isVectorizationSynced(vectorization.value, expectedVersion)) return true
    if (vectorizationStatus.value === 'FAILED' && vectorization.value?.retryable === false) return false
    if (attempt > 0) {
      try {
        const latest = await meetingImportsApi.vectorization(importId.value)
        setVectorization(latest)
        if (isVectorizationSynced(latest, expectedVersion)) return true
        if (['FAILED', 'STALE'].includes(String(latest.status).toUpperCase())) return false
      } catch { /* continue polling; the next attempt may recover */ }
    }
    await new Promise((resolve) => setTimeout(resolve, 1000))
  }
  return false
}

async function retryVectorization() {
  if (!(await flushEdits())) return
  const revision = review.value?.current_revision
  if (!revision) return
  confirmLoading.value = true
  try {
    await ensureVectorization(revision.version)
    if (!(await waitForVectorization(revision.version))) ElMessage.warning('向量化尚未完成，请稍后重试。')
  } catch (error) {
    ElMessage.error(toApiError(error).message)
  } finally {
    confirmLoading.value = false
    if (!vectorizationSynced.value) scheduleVectorizationPolling()
  }
}

async function load() {
  loading.value = true
  errorMessage.value = ''
  try {
    const data = await meetingImportsApi.review(importId.value)
    fillReview(data)
    scheduleVectorizationPolling()
    if (String(data.status).toUpperCase() === 'CONFIRMED' && data.meeting_id) {
      await router.replace({ name: 'meeting-detail', params: { id: data.meeting_id } })
    }
  } catch (error) {
    const parsed = toApiError(error)
    errorMessage.value = parsed.message
    if (parsed.status === 403) errorMessage.value = '当前账号无权访问此导入的审核页面。'
  } finally {
    loading.value = false
  }
}

function isConflict(error: unknown) {
  return toApiError(error).status === 409
}

function scheduleBlockSave(block: ReviewBlock) {
  dirtyBlocks.set(block.id, block.text)
  const oldTimer = pendingTimers.get(block.id)
  if (oldTimer) clearTimeout(oldTimer)
  pendingTimers.set(block.id, setTimeout(() => void saveBlock(block), 700))
}

async function persistBlock(block: ReviewBlock) {
  const timer = pendingTimers.get(block.id)
  if (timer) clearTimeout(timer)
  pendingTimers.delete(block.id)
  if (!revisionEditable.value || !review.value?.current_revision || !dirtyBlocks.has(block.id)) return
  const currentRevision = review.value.current_revision
  const savedText = block.text
  saving.value = true
  try {
    const result = await meetingImportsApi.patchRevision(importId.value, currentRevision.id, { expected_version: currentRevision.version, block_edits: [{ block_id: block.id, text: savedText, table_markdown: (block.type || block.block_type) === 'table' ? savedText : undefined }] })
    currentRevision.version = result.version
    currentRevision.blocks = result.blocks.length ? result.blocks : localBlocks.value
    if (dirtyBlocks.get(block.id) === savedText) dirtyBlocks.delete(block.id)
    else void saveBlock(block)
    lastSavedAt.value = new Date()
  } catch (error) {
    if (isConflict(error)) conflictMessage.value = '服务器已有更新。请重新加载最新版本，或复制并保留本地内容后再继续。'
    else ElMessage.error(toApiError(error).message)
  } finally {
    saving.value = false
  }
}

function saveBlock(block: ReviewBlock) {
  const task = saveChain.then(() => persistBlock(block))
  saveChain = task.catch(() => undefined)
  return task
}

async function flushEdits() {
  for (const timer of pendingTimers.values()) clearTimeout(timer)
  pendingTimers.clear()
  await saveChain
  for (const block of localBlocks.value.filter((item) => dirtyBlocks.has(item.id))) await saveBlock(block)
  await saveChain
  return dirtyBlocks.size === 0
}

function setMetadataValue(key: string, value: string | null) {
  metadata[key] = { ...normalizeField(metadata[key]), value, user_modified: true, needs_confirmation: false }
  dirtyMetadata.add(key)
  if (key === 'meeting_date' && value?.trim()) {
    // Date-only imports should never retain stale/default time values from an
    // older parser or a previous draft.
    for (const timeKey of ['starts_at', 'ends_at']) {
      metadata[timeKey] = { ...normalizeField(metadata[timeKey]), value: null, user_modified: true, needs_confirmation: false }
      dirtyMetadata.add(timeKey)
    }
  }
}

async function saveMetadata() {
  if (!review.value || !editable.value) return true
  if (!dirtyMetadata.size) return true
  metadataSaving.value = true
  try {
    const payload = Object.fromEntries([...dirtyMetadata].map((key) => [key, metadata[key]?.value ?? null]))
    const result = await meetingImportsApi.patchMetadata(importId.value, { expected_version: metadataVersion.value, ...payload })
    metadataVersion.value = result.metadata_version ?? metadataVersion.value + 1
    dirtyMetadata.clear()
    lastSavedAt.value = new Date()
    ElMessage.success('会议信息已保存')
    return true
  } catch (error) {
    if (isConflict(error)) conflictMessage.value = '会议信息已被其他人修改，请重新加载最新版本。'
    else ElMessage.error(toApiError(error).message)
    return false
  } finally {
    metadataSaving.value = false
  }
}

function validateMetadata() {
  metadataErrors.value = validateReviewMetadata(metadata)
  return Object.keys(metadataErrors.value).length === 0
}

function hasMetadataSource(field?: ReviewMetadataField) {
  if (!field?.source) return false
  if (Array.isArray(field.source)) return field.source.some((source) => Boolean(source?.block_id))
  return Boolean(field.source.block_id)
}

function locateSource(field: ReviewMetadataField) {
  const source = Array.isArray(field.source) ? field.source[0] : field.source
  const blockId = source?.block_id
  if (!blockId || !review.value?.original_blocks.some((block) => block.id === blockId)) return
  sourceBlockId.value = blockId
  void nextTick(() => document.getElementById(`block-${blockId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }))
}

function openSearch() {
  searchOpen.value = true
  void nextTick(() => document.querySelector<HTMLInputElement>('.review-search-input input')?.focus())
}

function moveMatch(direction: 1 | -1) {
  currentMatch.value = nextMatchIndex(totalMatches.value, currentMatch.value, direction)
  void scrollToCurrentMatch()
}

function replaceLiteralText(text: string, query: string, replacement: string, caseSensitive: boolean) {
  if (caseSensitive) return text.split(query).join(replacement)
  return text.replace(new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi'), () => replacement)
}

function updateLocalBlockText(block: ReviewBlock, text: string) {
  block.text = text
  if ((block.type || block.block_type) === 'table') block.table_markdown = text
}

async function scrollToCurrentMatch() {
  await nextTick()
  const currentMark = document.querySelector<HTMLElement>('.review-match.is-current')
  if (currentMark) {
    currentMark.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' })
    return
  }
  document.getElementById(`block-${current.value?.blockId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

async function replace(mode: 'CURRENT' | 'ALL') {
  if (!revisionEditable.value || !review.value?.current_revision || !searchQuery.value || !searchQuery.value.trim()) return
  const currentRevision = review.value.current_revision
  const preview = totalMatches.value
  if (!preview) return ElMessage.info('没有找到匹配内容')
  if (mode === 'ALL') {
    try {
      await ElMessageBox.confirm(`将替换 ${preview} 处匹配内容（${searchScope.value === 'FULL' ? '全文' : '当前段落'}）。该操作可批量撤销。`, '确认替换全部', { confirmButtonText: '替换全部', cancelButtonText: '取消', type: 'warning' })
    } catch { return }
  }
  try {
    const selected = mode === 'CURRENT' ? current.value : undefined
    const selectedBlock = selected ? blocksForDisplay.value.find((block) => block.id === selected.blockId) : undefined
    const blockMatches = selectedBlock
      ? findLiteralMatches([selectedBlock], searchQuery.value, { caseSensitive: searchCaseSensitive.value })
      : []
    const selectedBlockMatchIndex = selected
      ? blockMatches.findIndex((match) => match.start === selected.start && match.end === selected.end)
      : -1
    const currentScope = mode === 'CURRENT' && selected && selectedBlockMatchIndex >= 0 ? 'BLOCK' : searchScope.value
    const result = await meetingImportsApi.replace(importId.value, {
      query: searchQuery.value,
      replacement: replacementText.value,
      scope: currentScope,
      block_id: currentScope === 'BLOCK' ? (selected?.blockId || searchBlockId.value) : undefined,
      match_index: mode === 'CURRENT' ? (selectedBlockMatchIndex >= 0 ? selectedBlockMatchIndex : currentMatch.value) : undefined,
      case_sensitive: searchCaseSensitive.value,
      expected_version: currentRevision.version,
      mode,
    })
    const snapshots: Record<string, { text: string; table_markdown?: string | null }> = {}
    const targetBlocks = mode === 'CURRENT'
      ? (selectedBlock ? [selectedBlock] : [])
      : blocksForDisplay.value.filter((block) => currentScope !== 'BLOCK' || block.id === searchBlockId.value)
    for (const block of targetBlocks) {
      const oldText = block.text
      const nextText = mode === 'CURRENT' && selected && block.id === selected.blockId
        ? `${oldText.slice(0, selected.start)}${replacementText.value}${oldText.slice(selected.end)}`
        : replaceLiteralText(oldText, searchQuery.value, replacementText.value, searchCaseSensitive.value)
      if (nextText !== oldText) {
        snapshots[block.id] = { text: oldText, table_markdown: block.table_markdown }
        updateLocalBlockText(block, nextText)
      }
    }
    lastReplacementSnapshot.value = snapshots
    lastOperationId.value = result.operation_id
    undoVersion.value = result.new_version
    currentRevision.version = result.new_version
    if (result.revision_id) currentRevision.id = String(result.revision_id)
    currentRevision.blocks = localBlocks.value.map((block) => ({ ...block }))
    ElMessage.success(`已替换 ${result.replacement_count} 处内容`)
  } catch (error) {
    if (isConflict(error)) conflictMessage.value = '替换失败：内容版本已变化，请重新加载最新版本。'
    else ElMessage.error(toApiError(error).message)
  }
}

const replacementText = ref('')
async function undoReplace() {
  if (!lastOperationId.value) return
  try {
    const result = await meetingImportsApi.undoReplace(importId.value, lastOperationId.value, { expected_version: undoVersion.value })
    if (review.value?.current_revision) {
      review.value.current_revision.version = result.new_version
      if (result.revision_id) review.value.current_revision.id = String(result.revision_id)
      for (const block of localBlocks.value) {
        const snapshot = lastReplacementSnapshot.value[block.id]
        if (snapshot) updateLocalBlockText(block, snapshot.text)
      }
      review.value.current_revision.blocks = localBlocks.value.map((block) => ({ ...block }))
    }
    lastReplacementSnapshot.value = {}
    lastOperationId.value = ''
    ElMessage.success('已撤销批量替换')
  } catch (error) { ElMessage.error(toApiError(error).message) }
}

async function openHistory(item: ReviewRevision) {
  try { selectedHistory.value = await meetingImportsApi.revision(importId.value, item.id); historyOpen.value = true } catch (error) { ElMessage.error(toApiError(error).message) }
}

function idempotency() {
  if (!idempotencyKey.value) {
    const key = `meeting-confirm:${importId.value}`
    idempotencyKey.value = sessionStorage.getItem(key) || crypto.randomUUID()
    sessionStorage.setItem(key, idempotencyKey.value)
  }
  return idempotencyKey.value
}

async function confirmImport() {
  if (!(await flushEdits())) { ElMessage.warning('仍有正文修改尚未保存，请重试或复制保留本地内容。'); return }
  if (!validateMetadata()) { ElMessage.warning('请先补全或修正会议信息'); focusFirstMetadataField(); return }
  if (!(await saveMetadata())) return
  try {
    await ElMessageBox.confirm('确认后将冻结此次导入的校对版本，并开始知识库处理。确认继续吗？', '冻结并确认导入', { confirmButtonText: '确认导入', cancelButtonText: '返回修改', type: 'warning' })
  } catch { return }
  confirmLoading.value = true
  try {
    const currentRevision = review.value?.current_revision
    if (!currentRevision) { ElMessage.error('校对草稿尚未准备好，请重新加载。'); return }
    await ensureVectorization(currentRevision.version)
    if (!(await waitForVectorization(currentRevision.version))) {
      ElMessage.warning(vectorizationStatus.value === 'FAILED' ? (vectorization.value?.error_message || '向量化失败，请重试。') : '向量化尚未完成，请稍后重试。')
      return
    }
    const payload = Object.fromEntries(Object.entries(metadata).map(([key, field]) => [key, field.value || null]))
    const result = await meetingImportsApi.confirm(importId.value, { expected_version: currentRevision.version, expected_metadata_version: metadataVersion.value, ...payload }, idempotency())
    if (result.meeting_id) await router.replace({ name: 'meeting-detail', params: { id: result.meeting_id } })
  } catch (error) {
    if (isConflict(error)) conflictMessage.value = '确认失败：服务器版本已变化，请重新加载最新版本。'
    else ElMessage.error(toApiError(error).message)
  } finally {
    confirmLoading.value = false
    if (!vectorizationSynced.value) scheduleVectorizationPolling()
  }
}

function retryOrBack() {
  if (status.value === 'FAILED') void meetingImportsApi.retry(importId.value).then(load).catch((error) => (errorMessage.value = toApiError(error).message))
  // Do not carry the ready-for-review import id back to the upload page. The
  // upload page polls that id and immediately redirects here again, which
  // made the "返回导入" button appear to do nothing.
  else void router.replace({ name: 'meeting-import' })
}

function onKeydown(event: KeyboardEvent) {
  if (!editorFocused.value || !(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== 'f') return
  event.preventDefault(); openSearch()
}

function focusFirstMetadataField() {
  document.querySelector<HTMLInputElement>('.metadata-form input, .metadata-form textarea')?.focus()
}

function beforeUnload(event: BeforeUnloadEvent) {
  if (!dirtyBlocks.size && !dirtyMetadata.size && !saving.value && !metadataSaving.value) return
  event.preventDefault()
  event.returnValue = ''
}

watch([searchQuery, searchCaseSensitive, searchScope, searchBlockId], () => {
  currentMatch.value = 0
  if (findTimer) clearTimeout(findTimer)
  if (searchQuery.value && review.value?.current_revision) findTimer = setTimeout(() => {
    void meetingImportsApi.find(importId.value, { query: searchQuery.value, scope: searchScope.value, block_id: searchScope.value === 'BLOCK' ? searchBlockId.value : undefined, case_sensitive: searchCaseSensitive.value }).catch(() => undefined)
  }, 300)
  if (searchQuery.value) void scrollToCurrentMatch()
})
onBeforeRouteLeave(async () => {
  if (!dirtyBlocks.size && !dirtyMetadata.size) return true
  const bodySaved = await flushEdits()
  const metadataSaved = bodySaved ? await saveMetadata() : false
  if (bodySaved && metadataSaved) return true
  ElMessage.warning('仍有修改未保存，页面已保留本地内容，请复制内容或重试保存后再离开。')
  return false
})
onMounted(() => {
  window.localStorage.setItem(LAST_MEETING_IMPORT_ID, importId.value)
  window.addEventListener('keydown', onKeydown)
  window.addEventListener('beforeunload', beforeUnload)
  void load()
})
onBeforeUnmount(() => { window.removeEventListener('keydown', onKeydown); window.removeEventListener('beforeunload', beforeUnload); pendingTimers.forEach((timer) => clearTimeout(timer)); if (findTimer) clearTimeout(findTimer); stopVectorizationPolling() })
</script>

<template>
  <section class="review-page" @focusin="editorFocused = true" @focusout="editorFocused = false">
    <div class="review-header">
      <div>
        <el-button text :icon="ArrowLeft" @click="retryOrBack">返回导入</el-button>
        <el-breadcrumb separator="/" class="review-breadcrumb"><el-breadcrumb-item>我的会议</el-breadcrumb-item><el-breadcrumb-item>新建会议</el-breadcrumb-item></el-breadcrumb>
        <h1 class="review-title">会议纪要确认</h1>
        <p class="review-subtitle">上传后系统会在后台开始向量化；请核对并校对纪要正文，向量同步完成后才能建立会议。</p>
        <div class="review-meta"><Document /> <span>{{ filename }}</span><span>·</span><span aria-live="polite">{{ saveStatus }}</span><el-tag size="small" effect="plain">{{ status }}</el-tag></div>
      </div>
      <div class="review-actions"><el-button :disabled="!editable" :loading="saving || metadataSaving" @click="flushEdits().then((saved) => saved && saveMetadata())">保存草稿</el-button><el-button type="primary" :disabled="!canConfirm" :loading="confirmLoading" @click="confirmImport">确认并建立会议</el-button></div>
    </div>
    <div class="review-steps" aria-label="导入流程"><span class="done">1 · 上传文件</span><i /> <span class="active">2 · 校对并确认</span></div>
    <el-alert v-if="conflictMessage" class="conflict-alert" type="warning" show-icon :closable="false" title="检测到版本冲突"><template #default><span>{{ conflictMessage }}</span><el-button link type="primary" @click="load">重新加载最新版本</el-button></template></el-alert>
    <el-skeleton v-if="loading" :rows="10" animated />
    <el-alert v-else-if="errorMessage" type="error" show-icon :closable="false" :title="errorMessage"><template #default><el-button link type="primary" @click="load">重新加载</el-button></template></el-alert>
    <el-result v-else-if="!isReady" icon="warning" :title="status === 'FAILED' ? '导入处理失败' : status === 'CANCELLED' || status === 'CANCELED' ? '导入已取消' : status === 'CONFIRMED' ? '导入已确认' : '导入尚未准备好'" :sub-title="review?.import.error_message || status"><template #extra><el-button type="primary" @click="retryOrBack">{{ status === 'FAILED' ? '重试导入' : '返回导入进度' }}</el-button></template></el-result>
    <el-result v-else-if="!review?.current_revision" icon="warning" title="校对草稿尚未准备好" sub-title="请重新加载；系统不会使用虚拟版本覆盖服务器内容。"><template #extra><el-button type="primary" @click="load">重新加载</el-button></template></el-result>
    <div v-else class="review-layout">
      <section class="vectorization-card" aria-live="polite">
        <div class="vectorization-card-heading"><div><p class="section-kicker">KNOWLEDGE BASE</p><h2>知识库向量化</h2></div><el-tag :type="vectorizationStatus === 'FAILED' ? 'danger' : vectorizationSynced ? 'success' : 'warning'" effect="plain">{{ vectorizationStatusLabel(vectorizationStatus) }}</el-tag></div>
        <p class="vectorization-copy">上传完成后会自动在后台生成向量；确认建会会等待当前校对版本同步完成。</p>
        <el-progress v-if="['PENDING', 'RUNNING'].includes(vectorizationStatus)" :percentage="vectorizationPercent" :status="vectorizationStatus === 'RUNNING' ? undefined : 'warning'" />
        <p class="vectorization-meta" v-if="vectorization?.current_revision_version != null">当前版本 v{{ vectorization.current_revision_version }} · 已同步版本 {{ vectorization.vectorized_revision_version == null ? '—' : `v${vectorization.vectorized_revision_version}` }}<span v-if="vectorization.current_node"> · {{ vectorization.current_node }}</span></p>
        <el-alert v-if="vectorizationStatus === 'FAILED'" type="error" :closable="false" show-icon :title="vectorization?.error_message || '向量化失败'" />
        <el-alert v-else-if="vectorizationStatus === 'STALE' || (vectorizationStatus === 'SYNCED' && !vectorizationSynced)" type="warning" :closable="false" show-icon title="校对版本已有更新，需要重新同步向量。" />
        <div class="vectorization-actions"><el-button v-if="vectorizationStatus === 'FAILED' || vectorizationStatus === 'STALE' || !vectorizationSynced" size="small" :loading="confirmLoading" @click="retryVectorization">重试向量化</el-button><span v-if="!vectorizationSynced" class="vectorization-hint">向量同步完成前，“确认并建立会议”不可用。</span></div>
      </section>
      <main class="editor-panel">
        <div class="document-paper">
          <h1 class="paper-title">{{ title }}</h1>
          <p class="paper-subtitle">会议纪要 · {{ filename }}</p>

          <section class="metadata-section" aria-labelledby="metadata-heading">
            <div class="section-heading"><div><p class="section-kicker">MEETING DETAILS</p><h2 id="metadata-heading">会议信息</h2></div><el-tag v-if="needsConfirmation" type="warning" effect="plain">待确认 {{ needsConfirmation }}</el-tag></div>
            <el-form label-position="left" label-width="180px" class="metadata-form">
              <el-form-item v-for="field in meetingInfoFields" :key="field.key" :label="field.label" :error="field.key === 'meeting_date' && !hasMeetingDate ? (metadataErrors.starts_at || metadataErrors.ends_at) : metadataErrors[field.key]">
                <template v-if="field.key === 'meeting_date'">
                  <el-input :model-value="metadata.meeting_date?.value || ''" :readonly="!editable" @update:model-value="(value: string) => setMetadataValue('meeting_date', value)" />
                  <div v-if="!hasMeetingDate" class="date-range-fields">
                    <span>开始</span><el-date-picker :model-value="metadata.starts_at?.value || ''" type="datetime" value-format="YYYY-MM-DDTHH:mm:ssZ" :readonly="!editable" @update:model-value="(value: string | null) => setMetadataValue('starts_at', value)" />
                    <span>至</span><el-date-picker :model-value="metadata.ends_at?.value || ''" type="datetime" value-format="YYYY-MM-DDTHH:mm:ssZ" :readonly="!editable" @update:model-value="(value: string | null) => setMetadataValue('ends_at', value)" />
                  </div>
                </template>
                <el-input v-else-if="field.multiline" type="textarea" :rows="3" :model-value="metadata[field.key]?.value || ''" :readonly="!editable" @update:model-value="(value: string) => setMetadataValue(field.key, value)" />
                <el-input v-else :model-value="metadata[field.key]?.value || ''" :readonly="!editable" @update:model-value="(value: string) => setMetadataValue(field.key, value)" />
                <div class="suggestion" v-if="metadata[field.key]?.suggested_value && metadata[field.key]?.suggested_value !== metadata[field.key]?.value"><InfoFilled />建议：{{ metadata[field.key].suggested_value }} <span v-if="metadata[field.key]?.confidence_label">· {{ metadata[field.key]?.confidence_label }}</span></div>
                <div class="field-footer" v-if="hasMetadataSource(metadata[field.key]) || metadata[field.key]?.user_modified"><el-button v-if="hasMetadataSource(metadata[field.key])" text size="small" @click="locateSource(metadata[field.key])">查看原文来源</el-button><span v-if="metadata[field.key]?.user_modified">已手动修改</span></div>
              </el-form-item>
            </el-form>
            <div class="metadata-footer"><span>会议信息与正文均会自动保存</span><el-button class="metadata-save" :disabled="!editable" :loading="metadataSaving" @click="saveMetadata">保存会议信息</el-button></div>
          </section>

          <section class="transcript-section" aria-labelledby="transcript-heading">
            <div class="section-heading transcript-heading"><div><p class="section-kicker">TRANSCRIPT</p><h2 id="transcript-heading">纪要正文</h2></div><span class="transcript-note">连续文本窗口，原始文件保持不变</span></div>
            <div class="transcript-window">
              <div class="transcript-window-toolbar"><div><strong>会议纪要正文</strong><span>{{ revisionEditable ? '可直接编辑' : '当前账号无编辑权限' }}</span></div><div class="transcript-window-actions"><el-button class="find-replace-button" type="primary" :icon="Search" aria-label="打开查找替换" @click="openSearch">查找替换</el-button><el-button text size="small" @click="historyOpen = true">版本记录</el-button></div></div>
              <div v-if="searchOpen" class="search-bar" role="search"><el-input v-model="searchQuery" class="review-search-input" placeholder="查找原文（字面匹配）" clearable /><el-input v-model="replacementText" placeholder="替换为" /><el-checkbox v-model="searchCaseSensitive">区分大小写</el-checkbox><span class="match-count" aria-live="polite">{{ totalMatches ? currentMatch + 1 : 0 }}/{{ totalMatches }}</span><el-button text @click="moveMatch(-1)">上一个</el-button><el-button text @click="moveMatch(1)">下一个</el-button><el-button :disabled="!revisionEditable || !totalMatches" @click="replace('CURRENT')">替换当前</el-button><el-button type="warning" plain :disabled="!revisionEditable || !totalMatches" @click="replace('ALL')">替换全部</el-button><el-button v-if="lastOperationId" text @click="undoReplace">批量撤销</el-button><el-button text @click="searchOpen = false">关闭</el-button></div>
              <div class="block-list" aria-label="连续会议纪要编辑窗口">
                <article v-for="(block, index) in blocksForDisplay" :id="`block-${block.id}`" :key="block.id" class="transcript-block" :class="{ 'is-source': sourceBlockId === block.id, 'is-current': current?.blockId === block.id }">
                  <textarea v-if="revisionEditable && !searchQuery" v-model="block.text" rows="1" :aria-label="`编辑纪要内容 ${index + 1}`" @input="scheduleBlockSave(block)" @blur="saveBlock(block)" />
                  <template v-else-if="isTableBlock(block) && !searchQuery"><table class="table-preview"><tbody><tr v-for="(row, rowIndex) in parseMarkdownTable(tableBlockText(block))" :key="rowIndex"><td v-for="(cell, cellIndex) in row" :key="cellIndex">{{ cell }}</td></tr></tbody></table></template>
                  <!-- eslint-disable-next-line vue/no-v-html -->
                  <p v-else class="block-text" v-html="searchQuery ? highlightLiteral(cleanTranscriptText(block.text, block.type || block.block_type), searchQuery, current?.blockId === block.id ? current.start : -1, searchCaseSensitive) : escapeHighlightHtml(cleanTranscriptText(block.text, block.type || block.block_type))" />
                </article>
                <el-empty v-if="!blocksForDisplay.length" description="没有可显示的纪要内容" />
              </div>
            </div>
          </section>
        </div>
      </main>
    </div>
    <el-dialog v-model="historyOpen" title="版本记录" width="min(680px, 92vw)">
      <div class="history-list"><el-card v-for="item in review?.revisions" :key="item.id" shadow="never" class="history-card"><div><strong>修订 {{ item.revision_number }}</strong><el-tag size="small">{{ item.status || 'DRAFT' }}</el-tag><p>{{ item.creator || item.created_by || '系统' }} · {{ item.updated_at || item.created_at || '未知时间' }}</p></div><el-button @click="openHistory(item)">打开只读版本</el-button></el-card><el-empty v-if="!review?.revisions?.length" description="暂无历史修订" /></div>
      <div v-if="selectedHistory" class="history-preview"><p class="history-preview-title">修订 {{ selectedHistory.revision_number }}（只读）</p><p v-for="block in selectedHistory.blocks" :key="block.id" class="history-preview-block">{{ cleanTranscriptText(block.text, block.type || block.block_type) }}</p></div>
    </el-dialog>
  </section>
</template>

<style scoped>
.review-page { min-width: 0; padding-bottom: 40px; }
.review-header { display: flex; justify-content: space-between; align-items: center; gap: 24px; margin-bottom: 12px; }
.review-breadcrumb { margin: 8px 0; }
.review-title { margin: 4px 0 6px; color: var(--navy); font-size: clamp(24px, 3vw, 34px); }
.review-subtitle { margin: 0 0 10px; color: #718395; font-size: 14px; line-height: 1.6; }
.review-meta { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; color: #7a8b92; font-size: 12px; }
.review-meta svg { width: 15px; }
.review-steps { display: flex; align-items: center; gap: 10px; margin: 4px 0 14px; color: #9aa8aa; font-size: 12px; }
.review-steps i { width: 34px; height: 1px; background: var(--line); }
.review-steps .done { color: #5d988e; }
.review-steps .active { color: var(--teal); font-weight: 700; }
.review-actions { display: flex; gap: 8px; flex-shrink: 0; }
.conflict-alert { margin-bottom: 14px; }
.vectorization-card { max-width: 960px; margin: 0 auto 16px; padding: 16px 20px; border: 1px solid #d7e5df; border-radius: 8px; background: #f7fbf9; }
.vectorization-card-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.vectorization-card-heading h2 { margin: 0; color: var(--navy); font-size: 17px; }
.vectorization-copy { margin: 6px 0 12px; color: #687f84; font-size: 12px; line-height: 1.5; }
.vectorization-meta { margin: 8px 0 0; color: #829699; font-size: 11px; }
.vectorization-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; margin-top: 10px; }
.vectorization-hint { color: #87999a; font-size: 11px; }
.review-layout { display: block; }
.editor-panel { min-width: 0; }
.find-replace-button { min-height: 38px; padding-inline: 16px; font-weight: 700; box-shadow: 0 3px 10px rgba(22, 125, 111, .18); }
.search-bar { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; padding: 10px 14px; border-bottom: 1px solid #dce8e5; background: #f7faf9; }
.search-bar .el-input { width: 180px; }
.match-count { min-width: 44px; color: #64808a; font-size: 12px; text-align: center; }
.document-paper { max-width: 960px; margin: 0 auto; padding: 58px clamp(24px, 6vw, 90px) 72px; border: 1px solid #dce5e5; border-radius: 4px; background: #fff; box-shadow: 0 10px 32px rgba(17, 67, 93, .1); }
.paper-title { margin: 0; color: var(--navy); font-size: clamp(24px, 3vw, 34px); font-weight: 750; line-height: 1.3; text-align: center; }
.paper-subtitle { margin: 9px 0 42px; color: #8a999b; font-size: 12px; text-align: center; }
.metadata-section { margin-bottom: 44px; }
.section-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding-bottom: 12px; border-bottom: 2px solid #284b83; }
.section-heading h2 { margin: 0; color: #284b83; font-size: 21px; }
.section-kicker { margin: 0 0 3px; color: #8aa0a2; font-size: 10px; font-weight: 700; letter-spacing: .14em; }
.metadata-form { margin-top: 6px; }
.metadata-form :deep(.el-form-item) { align-items: flex-start; margin-bottom: 0; padding: 12px 0; border-bottom: 1px dashed #dbe4e3; }
.metadata-form :deep(.el-form-item__label) { flex: 0 0 180px; color: #687b82; font-weight: 650; line-height: 32px; }
.metadata-form :deep(.el-form-item__content) { display: block; min-height: 32px; line-height: 32px; }
.metadata-form :deep(.el-input__wrapper), .metadata-form :deep(.el-textarea__inner) { border-radius: 4px; box-shadow: none; background: #f5f7f8; }
.metadata-form :deep(.el-input__wrapper.is-focus), .metadata-form :deep(.el-textarea__inner:focus) { box-shadow: 0 0 0 1px var(--teal) inset; }
.metadata-form :deep(.el-date-editor) { width: 100%; }
.suggestion { display: flex; align-items: center; gap: 4px; margin-top: 5px; color: #71888a; font-size: 11px; line-height: 1.4; }
.suggestion svg { width: 13px; color: #3d9c8e; }
.field-footer { display: flex; align-items: center; justify-content: space-between; min-height: 22px; color: #97a3a4; font-size: 11px; }
.metadata-footer { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding-top: 14px; color: #8b999a; font-size: 11px; }
.metadata-save { flex-shrink: 0; }
.date-range-fields { display: flex; align-items: center; gap: 8px; }
.date-range-fields .el-date-editor { flex: 1; min-width: 0; }
.source-date { margin-top: 5px; color: #71888a; font-size: 11px; line-height: 1.4; }
.transcript-section { margin-top: 16px; }
.transcript-heading { margin-bottom: 14px; }
.transcript-note { color: #8b999a; font-size: 11px; }
.transcript-window { overflow: hidden; border: 1px solid #dce5e5; border-radius: 8px; background: #fff; }
.transcript-window-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 14px; border-bottom: 1px solid #dce5e5; background: #f8faf9; }
.transcript-window-toolbar > div:first-child { display: flex; align-items: baseline; gap: 9px; }
.transcript-window-toolbar strong { color: var(--navy); font-size: 14px; }
.transcript-window-toolbar span { color: #8b999a; font-size: 11px; }
.transcript-window-actions { display: flex; align-items: center; gap: 7px; }
.block-list { max-height: min(54vh, 560px); overflow-y: auto; overscroll-behavior: contain; padding: 22px 24px; scroll-behavior: smooth; }
.transcript-block { padding: 0 0 14px; scroll-margin: 18px; }
.transcript-block:last-child { padding-bottom: 0; }
.transcript-block.is-source { margin: -4px -8px 10px; padding: 4px 8px 10px; border-radius: 5px; background: #f2fbf8; box-shadow: 0 0 0 2px #6bc5b1; }
.transcript-block.is-current { background: #fffdf2; }
.block-text { margin: 0; white-space: pre-wrap; color: #334f5c; line-height: 1.8; }
.table-preview { width: 100%; border-collapse: collapse; color: #334f5c; line-height: 1.6; }
.table-preview td { padding: 8px 10px; border: 1px solid #dfe9e7; vertical-align: top; }
.table-preview tr:first-child td { color: var(--navy); background: #f2f8f6; font-weight: 700; }
.transcript-block textarea { width: 100%; min-height: 1.8em; field-sizing: content; resize: none; padding: 0; border: 0; outline: 0; color: #334f5c; background: transparent; font: inherit; line-height: 1.8; }
.transcript-block textarea:focus { background: #f8fbfa; box-shadow: 0 0 0 4px #f8fbfa; }
.transcript-block :deep(mark.review-match) { padding: 1px 2px; border-radius: 3px; background: #ffe6a2; }
.transcript-block :deep(mark.review-match.is-current) { color: #17354a; background: #f4a340; box-shadow: 0 0 0 2px rgba(244,163,64,.25); }
.history-list { display: grid; gap: 9px; padding: 14px 18px; border-top: 1px solid var(--line); }
.history-card :deep(.el-card__body) { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.history-card p { margin: 6px 0 0; color: #88979b; font-size: 12px; }
.history-preview { margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--line); }
.history-preview-title { margin: 0 0 8px; color: var(--navy); font-weight: 700; }
.history-preview-block { margin: 0; padding: 8px 0; border-bottom: 1px solid #e9efed; white-space: pre-wrap; line-height: 1.65; }
@media (max-width: 920px) { .review-header { align-items: flex-start; flex-direction: column; } .review-actions { width: 100%; } .review-actions .el-button { flex: 1; } .document-paper { padding-inline: clamp(20px, 5vw, 60px); } .vectorization-card { margin-inline: 0; } }
@media (max-width: 600px) { .search-bar .el-input { width: 100%; } .review-actions { flex-direction: column; } .review-actions .el-button { width: 100%; } .document-paper { padding: 34px 16px 44px; } .paper-subtitle { margin-bottom: 28px; } .metadata-form :deep(.el-form-item) { display: block; } .metadata-form :deep(.el-form-item__label) { display: block; line-height: 24px; } .date-range-fields { align-items: stretch; flex-wrap: wrap; } .date-range-fields .el-date-editor { width: 100%; flex-basis: 100%; } .metadata-footer { align-items: flex-start; flex-direction: column; } .metadata-save { width: 100%; } .transcript-window-toolbar { align-items: flex-start; flex-direction: column; } .transcript-window-actions { width: 100%; justify-content: space-between; } .block-list { max-height: 480px; padding: 16px; } }
@media (prefers-color-scheme: dark) { .document-paper, .transcript-window { border-color: #2b4c56; background: #142b36; } .review-title, .paper-title, .section-heading h2, .history-preview-title, .transcript-window-toolbar strong { color: #d8ecea; } .review-subtitle, .paper-subtitle, .block-text, .transcript-block textarea, .history-preview-block { color: #d8ecea; } .transcript-window-toolbar, .search-bar { border-color: #2b4c56; background: #1c333c; } .metadata-form :deep(.el-form-item__label) { color: #b8cece; } .metadata-form :deep(.el-input__wrapper), .metadata-form :deep(.el-textarea__inner) { background: #1c3a43; } .table-preview { color: #d8ecea; } .table-preview td { border-color: #2b4c56; } .table-preview tr:first-child td { color: #d8ecea; background: #1c3a43; } .transcript-block.is-current { background: #3b3827; } .transcript-block textarea:focus { background: #1c3a43; box-shadow: 0 0 0 4px #1c3a43; } }
</style>
