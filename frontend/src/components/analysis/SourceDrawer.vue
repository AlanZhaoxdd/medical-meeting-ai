<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import type { RagSource } from '@/types/meetingAnalysis'
import { copyText, ragSourceTypeLabels, ragSourceTypeTone } from '@/utils/meetingAnalysis'

const props = defineProps<{
  source: RagSource | null
}>()

const emit = defineEmits<{
  close: []
}>()

const router = useRouter()

const visible = computed(() => Boolean(props.source))
const location = computed(() => props.source?.content || props.source?.snippet || '该来源暂无可用原文。')

const metadata = computed(() => {
  const source = props.source
  if (!source) return []
  const items: Array<{ label: string; value: string }> = [
    { label: '来源编号', value: `[${source.index}]` },
    { label: '来源类型', value: ragSourceTypeLabels[source.type] },
    { label: '来源标题', value: source.title },
  ]
  if (source.speakerName) items.push({ label: '说话人', value: source.speakerName })
  const timestamp = source.timestamp
  if (timestamp) items.push({ label: '时间点', value: timestamp })
  if (source.pageNumber != null) items.push({ label: '文档页码', value: `第 ${source.pageNumber} 页` })
  if (source.knowledgeBaseName) items.push({ label: '知识库名称', value: source.knowledgeBaseName })
  if (source.documentName) items.push({ label: '文档名称', value: source.documentName })
  if (source.chunkId) items.push({ label: 'Chunk ID', value: source.chunkId })
  if (source.meetingId) items.push({ label: '会议 ID', value: source.meetingId })
  return items
})

async function copyLocation() {
  const copied = await copyText(location.value)
  if (copied) window.dispatchEvent(new CustomEvent('app-toast', { detail: '来源原文已复制' }))
}

function openDocument() {
  const source = props.source
  if (!source) return
  if (source.type === 'knowledge_base' && source.knowledgeBaseId && source.documentId) {
    void router.push({ name: 'kb-document-detail', params: { id: source.knowledgeBaseId, documentId: source.documentId } })
    emit('close')
  }
}
</script>

<template>
  <el-drawer v-model="visible" :title="source ? `来源 [${source.index}]` : '来源详情'" size="min(520px, 92vw)" @close="emit('close')">
    <template v-if="source">
      <div class="drawer-head">
        <span :class="['source-tag', `source-tag--${ragSourceTypeTone[source.type]}`]">{{ ragSourceTypeLabels[source.type] }}</span>
        <h3>{{ source.title }}</h3>
      </div>
      <dl v-if="metadata.length" class="meta-grid">
        <template v-for="item in metadata" :key="item.label">
          <dt>{{ item.label }}</dt>
          <dd>{{ item.value }}</dd>
        </template>
      </dl>
      <div class="content-block">
        <div class="content-label">原文 / 摘要</div>
        <p>{{ location }}</p>
      </div>
      <div class="drawer-actions">
        <el-button @click="copyLocation">复制原文</el-button>
        <el-button v-if="source.type === 'knowledge_base' && source.knowledgeBaseId && source.documentId" type="primary" plain @click="openDocument">查看文档详情</el-button>
      </div>
    </template>
  </el-drawer>
</template>

<style scoped>
.drawer-head { display: grid; gap: 8px; margin-bottom: 16px; }
.drawer-head h3 { margin: 0; color: #1d3b55; font-size: 17px; line-height: 1.4; }
.source-tag { justify-self: start; padding: 2px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }
.source-tag--transcript, .source-tag--cutoff { color: #0e6f6a; background: #e0f4f0; }
.source-tag--summary { color: #24579b; background: #e3eefc; }
.source-tag--history { color: #7a4d1d; background: #f6ead8; }
.source-tag--kb { color: #5b3fa8; background: #ece6fb; }
.source-tag--open { color: #9a4a1f; background: #fbe9e0; }
.meta-grid { display: grid; grid-template-columns: 96px minmax(0, 1fr); gap: 8px 14px; margin: 0 0 16px; padding: 14px 16px; border: 1px solid #e6e9f2; border-radius: 10px; background: #fafafd; }
.meta-grid dt { color: #8b98a5; font-size: 12px; }
.meta-grid dd { margin: 0; overflow: hidden; color: #2f4a60; font-size: 13px; text-overflow: ellipsis; }
.content-block { padding: 14px 16px; border: 1px solid #e6e9f2; border-radius: 10px; }
.content-label { margin-bottom: 8px; color: #8b98a5; font-size: 12px; font-weight: 700; }
.content-block p { margin: 0; color: #314e62; font-size: 13px; line-height: 1.75; white-space: pre-wrap; }
.drawer-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 18px; }
</style>
