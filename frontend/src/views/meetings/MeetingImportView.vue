<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import { kbApi } from '@/api/kb'
import { meetingImportsApi } from '@/api/meetingImports'
import { ApiRequestError, toApiError } from '@/utils/errors'
import { canImport, canSubmitImport, formatBytes, isActiveImportStatus, meetingImportStatusLabel, validateImportFile } from '@/utils/meetingImport'
import { useAuthStore } from '@/stores/auth'
import type { KnowledgeBase } from '@/types/kb'
import type { DuplicateImportDetails, MeetingImportConfig, MeetingImportRead } from '@/types/meetingImport'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const allowed = computed(() => canImport(auth.user?.role))
const knowledgeBases = ref<KnowledgeBase[]>([])
const knowledgeBaseId = ref('')
const config = ref<MeetingImportConfig>()
const file = ref<File>()
const importRecord = ref<MeetingImportRead>()
const configLoading = ref(false)
const submitting = ref(false)
const retrying = ref(false)
const cancelling = ref(false)
const uploadPercent = ref(0)
const errorMessage = ref('')
const validationMessage = ref('')
const pollingTimer = ref<number>()
const destroyed = ref(false)
const LAST_MEETING_IMPORT_ID = 'latest_meeting_import_id'

const currentStatus = computed(() => importRecord.value?.status)
const active = computed(() => isActiveImportStatus(currentStatus.value))
const canSubmit = computed(() => canSubmitImport(auth.user?.role, knowledgeBaseId.value, file.value) && !active.value && !submitting.value && !retrying.value && !cancelling.value)
const acceptAttr = computed(() => config.value?.allowed_extensions?.join(',') || undefined)
const uploadRules = computed(() => {
  if (!config.value) return '正在读取服务端上传规则…'
  return `支持 ${config.value.allowed_extensions.join('、')}，单个文件最大 ${formatBytes(config.value.max_upload_bytes)}。`
})

function importIdOf(record?: MeetingImportRead) {
  return record?.id || record?.import_id
}

function rememberImport(importId?: string) {
  if (importId) window.localStorage.setItem(LAST_MEETING_IMPORT_ID, importId)
}

function clearPolling() {
  if (pollingTimer.value) window.clearInterval(pollingTimer.value)
  pollingTimer.value = undefined
}

async function pollImport(importId: string) {
  try {
    const latest = await meetingImportsApi.get(importId)
    if (destroyed.value) return
    importRecord.value = latest
    errorMessage.value = ''
    const status = latest.status
    if (status === 'READY_FOR_REVIEW') {
      clearPolling()
      rememberImport(importId)
      await router.replace({ name: 'meeting-import-review', params: { importId } })
    } else if (!isActiveImportStatus(status)) {
      clearPolling()
    }
  } catch (error) {
    const apiError = error instanceof ApiRequestError ? error : toApiError(error)
    if (apiError.status && [401, 403, 404].includes(apiError.status)) {
      clearPolling()
      importRecord.value = undefined
      errorMessage.value = apiError.message
      return
    }
    // A transient GET error must not turn a server-side import into FAILED.
    errorMessage.value = '暂时无法获取最新状态，系统将继续重试。'
  }
}

function startPolling(importId: string) {
  clearPolling()
  void pollImport(importId)
  pollingTimer.value = window.setInterval(() => void pollImport(importId), 2500)
}

async function load() {
  if (!allowed.value) return
  configLoading.value = true
  errorMessage.value = ''
  try {
    const [loadedConfig, bases] = await Promise.all([meetingImportsApi.config(), kbApi.list()])
    config.value = loadedConfig
    knowledgeBases.value = bases
    const preferred = String(route.query.knowledgeBaseId || '')
    if (preferred && bases.some((base) => base.id === preferred)) knowledgeBaseId.value = preferred
  } catch (error) {
    errorMessage.value = toApiError(error).message
  } finally {
    configLoading.value = false
  }
  const importId = String(route.query.importId || '')
  if (importId) {
    importRecord.value = { id: importId, knowledge_base_id: '', status: 'UPLOADED' }
    startPolling(importId)
  }
}

async function onFileChange(uploadFile: { raw?: File }) {
  const next = uploadFile.raw
  if (!next) return
  validationMessage.value = ''
  if (!config.value) {
    validationMessage.value = '上传规则尚未加载，请稍后重试。'
    return
  }
  const error = await validateImportFile(next, config.value, file.value)
  if (error) {
    validationMessage.value = error
    return
  }
  file.value = next
}

function removeFile() {
  file.value = undefined
  validationMessage.value = ''
}

function duplicateDetails(error: ApiRequestError): DuplicateImportDetails {
  const details = error.details
  if (details && typeof details === 'object') {
    const normalized = details as DuplicateImportDetails & { duplicate_document_id?: string }
    return { ...normalized, existing_document_id: normalized.existing_document_id || normalized.duplicate_document_id }
  }
  return {}
}

async function submit(confirmDuplicate = false, documentId?: string) {
  if (!canSubmit.value || !knowledgeBaseId.value) return
  if (file.value && config.value) {
    const validation = await validateImportFile(file.value, config.value)
    if (validation) {
      validationMessage.value = validation
      return
    }
  }
  submitting.value = true
  uploadPercent.value = 0
  errorMessage.value = ''
  try {
    const result = await meetingImportsApi.create({ knowledgeBaseId: knowledgeBaseId.value, file: file.value, documentId, confirmDuplicate }, (percent) => (uploadPercent.value = percent))
    importRecord.value = result
    const id = importIdOf(result)
    if (id) {
      rememberImport(id)
      await router.replace({ query: { ...route.query, importId: id } })
      if (result.status === 'READY_FOR_REVIEW') await router.replace({ name: 'meeting-import-review', params: { importId: id } })
      else if (isActiveImportStatus(result.status)) startPolling(id)
    }
    ElMessage.success('导入任务已创建')
  } catch (error) {
    const apiError = toApiError(error)
    if (apiError.status === 409) {
      const details = duplicateDetails(apiError)
      if (details.existing_document_id) {
        try {
          await ElMessageBox.confirm('检测到相同原件。是否关联已有文档并继续？', '重复原件', { confirmButtonText: '关联已有文档', cancelButtonText: '取消', type: 'warning' })
          submitting.value = false
          await submit(true, details.existing_document_id)
        } catch {
          // User cancelled the explicit duplicate choice.
        }
      } else if (details.existing_import_id) {
        try {
          await ElMessageBox.confirm('该原件已有导入任务。是否查看现有任务？', '重复原件', { confirmButtonText: '查看任务', cancelButtonText: '取消', type: 'warning' })
          await router.replace({ query: { ...route.query, importId: details.existing_import_id } })
          startPolling(details.existing_import_id)
        } catch {
          // User cancelled the explicit duplicate choice.
        }
      } else errorMessage.value = '检测到重复原件，请选择关联方式后重试。'
    } else {
      errorMessage.value = apiError.message
    }
  } finally {
    submitting.value = false
  }
}

async function retry() {
  const id = importIdOf(importRecord.value)
  if (!id || retrying.value) return
  retrying.value = true
  errorMessage.value = ''
  try {
    importRecord.value = await meetingImportsApi.retry(id)
    startPolling(id)
    ElMessage.success('已重新提交处理')
  } catch (error) {
    errorMessage.value = toApiError(error).message
  } finally {
    retrying.value = false
  }
}

async function cancelImport() {
  const id = importIdOf(importRecord.value)
  if (!id || cancelling.value) return
  try {
    await ElMessageBox.confirm('取消后将停止当前导入任务，确定继续吗？', '取消导入', { confirmButtonText: '确认取消', cancelButtonText: '继续处理', type: 'warning' })
  } catch {
    return
  }
  cancelling.value = true
  try {
    importRecord.value = await meetingImportsApi.cancel(id)
    clearPolling()
    ElMessage.success('导入任务已取消')
  } catch (error) {
    errorMessage.value = toApiError(error).message
  } finally {
    cancelling.value = false
  }
}

function beforeUnload(event: BeforeUnloadEvent) {
  if (active.value || submitting.value) {
    event.preventDefault()
    event.returnValue = ''
  }
}

onBeforeRouteLeave(async () => {
  if (!active.value && !submitting.value) return true
  try {
    await ElMessageBox.confirm('导入仍在进行，离开后可从导入会议页面恢复。确定离开吗？', '离开导入页面', { confirmButtonText: '离开', cancelButtonText: '留下', type: 'warning' })
    return true
  } catch {
    return false
  }
})

onMounted(() => {
  if (allowed.value) {
    void load()
    window.addEventListener('beforeunload', beforeUnload)
  }
})
watch(() => route.query.importId, (value) => {
  if (allowed.value && value && String(value) !== importIdOf(importRecord.value)) startPolling(String(value))
})
onBeforeUnmount(() => {
  destroyed.value = true
  clearPolling()
  window.removeEventListener('beforeunload', beforeUnload)
})
</script>

<template>
  <section class="meeting-import-page">
    <div class="page-header"><div><p class="eyebrow">MEETING IMPORT</p><h1 class="page-title">导入会议</h1><p class="page-subtitle">上传会议原件，系统会在后台解析并准备审核。</p></div></div>
    <el-alert v-if="!allowed" title="当前账号没有导入会议的权限" description="请联系组织管理员申请 editor、admin 或 owner 角色。" type="warning" show-icon :closable="false" />
    <template v-else>
      <el-alert v-if="errorMessage" class="import-alert" :title="errorMessage" type="error" show-icon :closable="false">
        <el-button v-if="!active" link type="primary" :loading="configLoading" @click="load">重新加载</el-button>
      </el-alert>
      <el-empty v-if="!configLoading && config && knowledgeBases.length === 0" description="当前组织暂无可写知识库，请联系管理员先完成知识库设置。" />
      <el-card v-else v-loading="configLoading" class="content-card import-card" shadow="never">
        <el-form label-position="top" @submit.prevent="submit()">
          <el-form-item label="知识库" required>
            <el-select v-model="knowledgeBaseId" placeholder="选择要归档的知识库" class="import-select" :disabled="submitting || Boolean(importRecord && active)">
              <el-option v-for="base in knowledgeBases" :key="base.id" :label="base.name" :value="base.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="会议原件" required>
            <el-upload drag :auto-upload="false" :show-file-list="false" :accept="acceptAttr" :disabled="submitting || Boolean(importRecord && active)" :on-change="onFileChange">
              <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
              <div class="el-upload__text">拖入文件，或 <em>点击选择</em></div>
              <template #tip><div class="el-upload__tip">{{ uploadRules }} JSON 逐字稿必须是合法 JSON。</div></template>
            </el-upload>
            <div v-if="file" class="selected-file"><span>{{ file.name }}（{{ formatBytes(file.size) }}）</span><span class="selected-file-actions"><el-tag size="small" effect="plain">等待上传</el-tag><el-button text type="primary" @click="removeFile">移除</el-button></span></div>
            <div v-if="validationMessage" class="upload-error" role="alert">{{ validationMessage }}</div>
          </el-form-item>
          <div class="import-notices">
            <el-alert title="自动提取会议信息" description="系统会自动识别会议主题、时间和参会人等基础信息，并在校对页供你确认。" type="info" show-icon :closable="false" />
            <el-alert title="原始文件永久保留" description="后续校对和编辑不会覆盖上传的原件。" type="info" show-icon :closable="false" />
          </div>
          <div v-if="submitting" class="upload-progress" role="status" aria-live="polite">
            <span>正在安全保存原始文件</span>
            <el-progress :percentage="uploadPercent" />
          </div>
          <div class="import-actions">
            <el-button type="primary" native-type="submit" :loading="submitting" :disabled="!canSubmit || !config">上传并解析</el-button>
            <el-button v-if="active" type="danger" plain :loading="cancelling" :disabled="submitting || retrying" @click="cancelImport">取消导入</el-button>
          </div>
        </el-form>
      </el-card>

      <el-card v-if="importRecord" class="content-card status-card" shadow="never" aria-live="polite">
        <div class="status-heading"><div><p class="eyebrow">IMPORT STATUS</p><h2>{{ meetingImportStatusLabel(importRecord.status) }}</h2></div><el-tag :type="importRecord.status === 'FAILED' ? 'danger' : importRecord.status === 'READY_FOR_REVIEW' ? 'success' : 'info'" effect="light">{{ importRecord.status }}</el-tag></div>
        <p v-if="importRecord.error_message" class="upload-error" role="alert">{{ importRecord.error_message }}</p>
        <el-progress v-if="active || importRecord.progress !== undefined" :percentage="Math.min(100, Math.max(0, Number(importRecord.progress || 0)))" :status="importRecord.status === 'FAILED' ? 'exception' : undefined" />
        <p class="status-caption">{{ meetingImportStatusLabel(importRecord.status) }}。状态会自动刷新；网络暂时不可用时系统将继续查询服务器。</p>
        <div class="status-actions"><el-button v-if="importRecord.status === 'FAILED' && importRecord.can_retry" type="primary" :loading="retrying" @click="retry">重试处理</el-button><el-button v-if="importRecord.status === 'READY_FOR_REVIEW'" type="primary" @click="router.replace({ name: 'meeting-import-review', params: { importId: importIdOf(importRecord) } })">进入审核准备</el-button></div>
      </el-card>
    </template>
  </section>
</template>

<style scoped>
.meeting-import-page { width: 100%; max-width: 980px; margin: 0 auto; }
.import-card { max-width: 760px; margin-top: 18px; }
.import-select { width: min(100%, 520px); }
.import-actions, .status-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 8px; }
.import-notices { display: grid; gap: 10px; margin: 4px 0 18px; }
.upload-progress { display: grid; gap: 8px; margin-bottom: 16px; color: var(--navy); font-size: 13px; }
.selected-file { display: flex; align-items: center; justify-content: space-between; gap: 12px; width: 100%; max-width: 620px; padding: 9px 12px; margin-top: 10px; border: 1px solid var(--line); border-radius: 8px; color: #4d6672; overflow-wrap: anywhere; }
.selected-file-actions { display: flex; align-items: center; gap: 4px; flex: none; }
.upload-error { margin-top: 8px; color: #b84c4c; font-size: 13px; }
.import-alert { margin: 16px 0; }
.status-card { max-width: 760px; margin-top: 18px; }
.status-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.status-heading h2 { margin: 0; color: var(--navy); font-size: 20px; }
.status-caption { color: #718395; font-size: 13px; }
:deep(.el-upload-dragger) { width: 100%; max-width: 620px; }
:deep(.el-upload) { width: 100%; }
@media (max-width: 768px) { .meeting-import-page { max-width: none; } .status-heading { align-items: flex-start; flex-direction: column; } :deep(.el-upload__text) { padding: 0 12px; white-space: normal; } }
</style>
