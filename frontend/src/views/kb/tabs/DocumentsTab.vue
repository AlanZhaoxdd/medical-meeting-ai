<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import { kbApi } from '@/api/kb'
import { getAccessToken, useAuthStore } from '@/stores/auth'
import type { ExtractionTemplate, Job, KbDocument, KnowledgeBase } from '@/types/kb'

const props = defineProps<{ kb: KnowledgeBase }>()
const router = useRouter()
const auth = useAuthStore()
const documents = ref<KbDocument[]>([])
const templates = ref<ExtractionTemplate[]>([])
const file = ref<File>()
const templateId = ref(props.kb.default_template_id || '')
const forceNewVersion = ref(false)
const uploadPercent = ref(0)
const uploading = ref(false)
const loading = ref(false)
const jobs = ref<Record<string, Job>>({})
const sockets = new Set<WebSocket>()
const pollers = new Set<number>()
const canEdit = computed(() => ['owner', 'admin', 'editor'].includes(auth.user?.role || ''))

const statusLabels: Record<string, string> = {
  validate_source: '校验原件',
  parse_document: '解析',
  normalize_blocks: '标准化',
  build_chunks: '切块',
  embed_chunks: '向量化',
  extract_knowledge: '知识提取',
  validate_evidence: '证据验证',
  save_draft: '保存暂存',
  review_gate: '等待审核',
  publish_document: '发布',
  finalize: '已完成',
  UPLOADED: '已上传',
  PARSING: '解析中',
  PARSED: '已解析',
  CHUNKING: '切块中',
  EMBEDDING: '向量化中',
  EXTRACTING: '知识提取中',
  AWAITING_REVIEW: '待审核',
  IN_REVIEW: '审核中',
  PUBLISHED: '已发布',
  FAILED: '处理失败',
  DELETED: '已删除',
}

async function load() {
  loading.value = true
  try {
    ;[documents.value, templates.value] = await Promise.all([kbApi.documents(props.kb.id), kbApi.templates(props.kb.id)])
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '文档加载失败')
  } finally {
    loading.value = false
  }
}

function pick(uploadFile: { raw?: File }) {
  file.value = uploadFile.raw
}

function startPolling(jobId: string) {
  const timer = window.setInterval(async () => {
    try {
      const job = await kbApi.job(jobId)
      jobs.value[job.document_id] = job
      if (['COMPLETED', 'FAILED', 'WAITING_REVIEW'].includes(job.status)) {
        window.clearInterval(timer)
        pollers.delete(timer)
        await load()
      }
    } catch {
      // Keep polling: REST is the deliberate WebSocket fallback.
    }
  }, 3000)
  pollers.add(timer)
}

function monitor(jobId: string, documentId: string) {
  const token = getAccessToken()
  if (!token) return
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const socket = new WebSocket(`${protocol}//${window.location.host}/api/v1/ws/jobs/${jobId}?token=${encodeURIComponent(token)}`)
  sockets.add(socket)
  let opened = false
  socket.onopen = () => {
    opened = true
  }
  socket.onmessage = (event) => {
    const data = JSON.parse(event.data) as Job & { type?: string }
    if (data.type !== 'heartbeat') jobs.value[documentId] = data
    if (['COMPLETED', 'FAILED', 'WAITING_REVIEW'].includes(data.status)) {
      socket.close()
      load()
    }
  }
  socket.onerror = () => socket.close()
  socket.onclose = () => {
    sockets.delete(socket)
    if (!opened || !['COMPLETED', 'FAILED', 'WAITING_REVIEW'].includes(jobs.value[documentId]?.status)) startPolling(jobId)
  }
}

async function upload() {
  if (!file.value) return
  uploading.value = true
  uploadPercent.value = 0
  try {
    const form = new FormData()
    form.append('file', file.value)
    if (templateId.value) form.append('template_id', templateId.value)
    form.append('force_new_version', String(forceNewVersion.value))
    const result = await kbApi.upload(props.kb.id, form, (percent) => (uploadPercent.value = percent))
    if (result.duplicate) {
      ElMessage.info('检测到同一文件，已返回现有版本')
    } else {
      ElMessage.success('原件已保存，正在处理')
      if (result.job_id) monitor(result.job_id, result.document.id)
    }
    file.value = undefined
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '上传失败')
  } finally {
    uploading.value = false
  }
}

function jobFor(document: KbDocument) {
  return jobs.value[document.id]
}

onMounted(load)
onBeforeUnmount(() => {
  sockets.forEach((socket) => socket.close())
  pollers.forEach((timer) => window.clearInterval(timer))
})
</script>

<template>
  <div class="tab-stack">
    <el-card v-if="canEdit" class="upload-card" shadow="never">
      <div class="upload-layout">
        <el-upload drag :auto-upload="false" :limit="1" :show-file-list="Boolean(file)" accept=".pdf,.docx,.pptx,.txt,.md,.markdown,.json" :on-change="pick">
          <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
          <div class="el-upload__text">拖入文档，或 <em>点击选择</em></div>
          <template #tip><span>PDF、DOCX、PPTX、TXT、Markdown 或逐字稿 JSON，最大大小由服务端配置</span></template>
        </el-upload>
        <div class="upload-options">
          <label>字段模板</label>
          <el-select v-model="templateId" placeholder="使用知识库默认模板">
            <el-option v-for="template in templates" :key="template.id" :label="`${template.name} · v${template.version}`" :value="template.id" />
          </el-select>
          <el-checkbox v-model="forceNewVersion">即使内容重复也创建新版本</el-checkbox>
          <el-progress v-if="uploading" :percentage="uploadPercent" />
          <el-button type="primary" :loading="uploading" :disabled="!file" @click="upload">保存并开始处理</el-button>
        </div>
      </div>
    </el-card>

    <el-card class="content-card" shadow="never">
      <template #header><div class="card-heading"><strong>项目文档</strong><span>{{ documents.length }} 份</span></div></template>
      <el-table v-loading="loading" :data="documents" empty-text="尚未上传文档">
        <el-table-column label="文件" min-width="260">
          <template #default="{ row }">
            <button class="link-title" @click="router.push(`/knowledge-bases/${kb.id}/documents/${row.id}`)">{{ row.filename }}</button>
            <small>v{{ row.version }} · {{ row.mime_type }}</small>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="190">
          <template #default="{ row }">
            <div v-if="jobFor(row)" class="job-progress">
              <span>{{ statusLabels[jobFor(row).current_node] || jobFor(row).current_node }}</span>
              <el-progress :percentage="jobFor(row).progress" :stroke-width="5" :show-text="false" />
            </div>
            <el-tag v-else :type="row.status === 'FAILED' ? 'danger' : row.status === 'PUBLISHED' ? 'success' : ['AWAITING_REVIEW', 'IN_REVIEW'].includes(row.status) ? 'warning' : 'info'" effect="light">
              {{ statusLabels[row.status] || row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="说明" width="220">
          <template #default="{ row }">
            <span v-if="row.error_message" class="status-hint error-hint">{{ row.error_message }}</span>
            <span v-else-if="row.status === 'PUBLISHED' && row.published_at">已于 {{ new Date(row.published_at).toLocaleDateString('zh-CN') }} 发布</span>
            <span v-else-if="['AWAITING_REVIEW', 'IN_REVIEW'].includes(row.status)" class="status-hint">知识项审核通过后即可发布</span>
            <span v-else-if="row.status === 'FAILED'" class="status-hint">可重试处理</span>
            <span v-else class="status-hint">向量同步{{ row.vector_sync_status === 'SYNCED' ? '完成' : '中' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="180">
          <template #default="{ row }">{{ new Date(row.created_at).toLocaleString('zh-CN') }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>
