<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { kbApi } from '@/api/kb'
import { useAuthStore } from '@/stores/auth'
import type { KbDocument, KnowledgeBase, KnowledgeItem, ReviewStatus } from '@/types/kb'

const props = defineProps<{ kb: KnowledgeBase }>()
const auth = useAuthStore()
const items = ref<KnowledgeItem[]>([])
const documents = ref<KbDocument[]>([])
const loading = ref(false)
const selected = ref<KnowledgeItem>()
const drawer = ref(false)
const documentFilter = ref('')
const statusFilter = ref<ReviewStatus | ''>('')
const editContent = ref('')
const comment = ref('')
const canReview = computed(() => ['owner', 'admin', 'editor', 'reviewer'].includes(auth.user?.role || ''))
const filtered = computed(() => items.value.filter((item) =>
  (!documentFilter.value || item.document_id === documentFilter.value) &&
  (!statusFilter.value || item.review_status === statusFilter.value),
))

async function load() {
  loading.value = true
  try {
    ;[items.value, documents.value] = await Promise.all([kbApi.knowledgeItems(props.kb.id), kbApi.documents(props.kb.id)])
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '审核数据加载失败')
  } finally {
    loading.value = false
  }
}

function open(item: KnowledgeItem) {
  selected.value = item
  editContent.value = item.normalized_content
  comment.value = item.review_comment || ''
  drawer.value = true
}

async function saveEdit() {
  if (!selected.value) return
  selected.value = await kbApi.updateKnowledgeItem(props.kb.id, selected.value.id, {
    normalized_content: editContent.value,
  })
  ElMessage.success('修订已保存，状态已回到待审核')
  await load()
}

async function review(status: ReviewStatus) {
  if (!selected.value) return
  try {
    selected.value = await kbApi.review(props.kb.id, selected.value.id, status, comment.value)
    ElMessage.success('审核结果已记录')
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '审核失败')
  }
}

function unresolved(documentId: string) {
  return items.value.some((item) => item.document_id === documentId && ['PENDING', 'NEEDS_CHANGES'].includes(item.review_status))
}

async function publish(document: KbDocument) {
  await ElMessageBox.confirm(`确认发布《${document.filename}》？发布后正式检索将可见。`, '发布文档', { type: 'warning' })
  try {
    await kbApi.publish(props.kb.id, document.id)
    ElMessage.success('文档已发布，向量索引正在同步')
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '发布门禁未通过')
  }
}

onMounted(load)
</script>

<template>
  <div class="tab-stack">
    <div class="review-toolbar">
      <el-select v-model="documentFilter" clearable placeholder="全部文档">
        <el-option v-for="document in documents" :key="document.id" :label="`${document.filename} · v${document.version}`" :value="document.id" />
      </el-select>
      <el-select v-model="statusFilter" clearable placeholder="全部审核状态">
        <el-option v-for="status in ['PENDING', 'APPROVED', 'REJECTED', 'NEEDS_CHANGES']" :key="status" :label="status" :value="status" />
      </el-select>
      <span>{{ filtered.length }} 条知识</span>
    </div>
    <el-card class="content-card" shadow="never">
      <el-table v-loading="loading" :data="filtered" empty-text="尚无待审核知识">
        <el-table-column prop="item_type" label="类型" width="150" />
        <el-table-column label="知识内容" min-width="360">
          <template #default="{ row }"><button class="link-title" @click="open(row)">{{ row.title }}</button><p class="line-clamp">{{ row.normalized_content }}</p></template>
        </el-table-column>
        <el-table-column label="置信度" width="110"><template #default="{ row }">{{ Math.round(row.confidence * 100) }}%</template></el-table-column>
        <el-table-column label="状态" width="140"><template #default="{ row }"><el-tag :type="row.review_status === 'APPROVED' ? 'success' : row.review_status === 'REJECTED' ? 'danger' : 'warning'">{{ row.review_status }}</el-tag></template></el-table-column>
        <el-table-column label="证据" width="90"><template #default="{ row }">{{ row.source_refs.length }} 处</template></el-table-column>
      </el-table>
    </el-card>
    <el-card v-if="canReview && documents.length" class="content-card publish-panel" shadow="never">
      <div><strong>文档发布</strong><p>所有知识项审核完成且向量同步成功后可发布。</p></div>
      <div class="publish-actions">
        <el-button v-for="document in documents.filter((item) => ['AWAITING_REVIEW', 'IN_REVIEW'].includes(item.status))" :key="document.id" type="primary" plain :disabled="unresolved(document.id)" @click="publish(document)">
          发布 {{ document.filename }} v{{ document.version }}
        </el-button>
      </div>
    </el-card>

    <el-drawer v-model="drawer" size="72%" title="知识项审核">
      <div v-if="selected" class="review-split">
        <section>
          <p class="eyebrow">{{ selected.item_type }} · REVISION {{ selected.revision }}</p>
          <h2>{{ selected.title }}</h2>
          <el-input v-model="editContent" type="textarea" :rows="9" :disabled="!canReview" />
          <el-input v-model="comment" class="review-comment" type="textarea" :rows="3" placeholder="审核意见" />
          <div v-if="canReview" class="review-buttons">
            <el-button @click="saveEdit">保存修订</el-button>
            <el-button type="success" @click="review('APPROVED')">批准</el-button>
            <el-button type="warning" @click="review('NEEDS_CHANGES')">要求修改</el-button>
            <el-button type="danger" plain @click="review('REJECTED')">拒绝</el-button>
          </div>
        </section>
        <aside class="evidence-panel">
          <h3>来源证据</h3>
          <article v-for="(source, index) in selected.source_refs" :key="index" class="evidence-card">
            <div class="source-meta">
              <span v-if="source.page_number">第 {{ source.page_number }} 页</span>
              <span v-if="source.slide_number">第 {{ source.slide_number }} 张</span>
              <span v-if="source.speaker">{{ source.speaker }}</span>
              <span v-if="source.start_ms">{{ Math.round(source.start_ms / 1000) }}s–{{ Math.round((source.end_ms || 0) / 1000) }}s</span>
            </div>
            <blockquote>{{ source.quote }}</blockquote>
            <small>{{ source.chunk_id || source.block_id }}</small>
          </article>
        </aside>
      </div>
    </el-drawer>
  </div>
</template>
