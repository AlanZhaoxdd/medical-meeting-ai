<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowLeft, Delete, Refresh, Connection } from '@element-plus/icons-vue'
import { kbApi } from '@/api/kb'
import { useAuthStore } from '@/stores/auth'
import type { KbDocument } from '@/types/kb'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const kbId = String(route.params.id)
const documentId = String(route.params.documentId)
const document = ref<KbDocument>()
const blocks = ref<Record<string, unknown>[]>([])
const chunks = ref<Record<string, unknown>[]>([])
const active = ref('chunks')
const loading = ref(true)
const actionLoading = ref(false)
const canEdit = computed(() => ['owner', 'admin', 'editor'].includes(auth.user?.role || ''))

const documentStatusLabels: Record<string, string> = {
  UPLOADED: '已上传',
  PARSING: '解析中',
  PARSED: '已解析',
  CHUNKING: '切块中',
  EMBEDDING: '向量化中',
  EXTRACTING: '知识提取中',
  PUBLISHED: '已发布',
  FAILED: '处理失败',
  DELETED: '已删除',
}

function documentStatusLabel(status?: string) {
  return (status && documentStatusLabels[status]) || status || '未知'
}

async function load() {
  loading.value = true
  try {
    ;[document.value, blocks.value, chunks.value] = await Promise.all([
      kbApi.document(kbId, documentId),
      kbApi.blocks(kbId, documentId),
      kbApi.chunks(kbId, documentId),
    ])
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '文档详情加载失败')
  } finally {
    loading.value = false
  }
}

async function retry() {
  actionLoading.value = true
  try {
    await kbApi.retry(kbId, documentId)
    ElMessage.success('已从最近可恢复节点重试')
    await load()
  } finally {
    actionLoading.value = false
  }
}

async function reindex() {
  actionLoading.value = true
  try {
    await kbApi.reindex(kbId, documentId)
    ElMessage.success('已提交重新索引')
    await load()
  } finally {
    actionLoading.value = false
  }
}

async function remove() {
  await ElMessageBox.confirm('确认删除该文档？原件和正式数据将先软删除。', '删除文档', { type: 'warning' })
  await kbApi.removeDocument(kbId, documentId)
  ElMessage.success('文档已删除')
  await router.push(`/knowledge-bases/${kbId}`)
}

function sourceLabel(item: Record<string, unknown>) {
  if (item.page_number) return `第 ${item.page_number} 页`
  if (item.slide_number) return `第 ${item.slide_number} 张幻灯片`
  if (item.speaker) return `${item.speaker} · ${Math.round(Number(item.start_ms || 0) / 1000)}s`
  const locator = item.source_locator as Record<string, unknown> | undefined
  if (locator?.page_number) return `第 ${locator.page_number} 页`
  if (locator?.slide_number) return `第 ${locator.slide_number} 张幻灯片`
  return '原文定位'
}

onMounted(load)
</script>

<template>
  <section v-loading="loading">
    <el-button text :icon="ArrowLeft" @click="router.push(`/knowledge-bases/${kbId}`)">返回知识库</el-button>
    <header v-if="document" class="document-header">
      <div>
        <p class="eyebrow">DOCUMENT · VERSION {{ document.version }}</p>
        <h1>{{ document.filename }}</h1>
        <div class="document-meta">
          <span>{{ document.mime_type }}</span><span>SHA-256 {{ document.sha256.slice(0, 12) }}…</span><span>模板 v{{ document.template_version }}</span>
        </div>
      </div>
      <div v-if="canEdit" class="document-actions">
        <el-button v-if="document.status === 'FAILED'" :icon="Refresh" :loading="actionLoading" @click="retry">重试</el-button>
        <el-button :icon="Connection" :loading="actionLoading" @click="reindex">重建索引</el-button>
        <el-button type="danger" plain :icon="Delete" @click="remove">删除</el-button>
      </div>
    </header>
    <el-alert v-if="document?.error_message" type="error" :title="document.error_code || '处理失败'" :description="document.error_message" show-icon :closable="false" />
    <div v-if="document" class="status-ribbon">
      <span><small>状态</small><strong>{{ documentStatusLabel(document.status) }}</strong></span>
      <span><small>向量同步</small><strong>{{ document.vector_sync_status === 'SYNCED' ? '已完成' : '同步中' }}</strong></span>
    </div>
    <el-card class="content-card document-content" shadow="never">
      <el-tabs v-model="active">
        <el-tab-pane :label="`Chunks (${chunks.length})`" name="chunks">
          <article v-for="chunk in chunks" :key="String(chunk.chunk_id)" class="preview-block">
            <div><el-tag size="small">{{ chunk.content_type }}</el-tag><span>{{ sourceLabel(chunk) }}</span><span>{{ chunk.token_count }} tokens</span></div>
            <p>{{ chunk.content }}</p>
            <small>{{ chunk.chunk_id }}</small>
          </article>
          <el-empty v-if="!chunks.length" description="尚未生成 Chunk" />
        </el-tab-pane>
        <el-tab-pane :label="`Blocks (${blocks.length})`" name="blocks">
          <article v-for="block in blocks" :key="String(block.block_id)" class="preview-block">
            <div><el-tag size="small" effect="plain">{{ block.block_type }}</el-tag><span>{{ sourceLabel(block) }}</span></div>
            <p>{{ block.table_markdown || block.text }}</p>
            <small>{{ block.block_id }}</small>
          </article>
          <el-empty v-if="!blocks.length" description="尚未生成标准化 Block" />
        </el-tab-pane>
      </el-tabs>
    </el-card>
  </section>
</template>
