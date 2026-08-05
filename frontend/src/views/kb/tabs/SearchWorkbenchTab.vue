<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { kbApi } from '@/api/kb'
import { useAuthStore } from '@/stores/auth'
import type { KbDocument, KnowledgeBase, SearchResult } from '@/types/kb'

const props = defineProps<{ kb: KnowledgeBase }>()
const auth = useAuthStore()
const documents = ref<KbDocument[]>([])
const results = ref<SearchResult[]>([])
const elapsed = ref<number>()
const loading = ref(false)
const form = reactive({
  query: '',
  top_k: 5,
  content_types: [] as string[],
  document_ids: [] as string[],
  include_drafts: false,
})
const canIncludeDrafts = computed(() => ['owner', 'admin', 'editor'].includes(auth.user?.role || ''))

type HighlightPart = { text: string; highlighted: boolean }
type SegmenterItem = { segment: string; isWordLike?: boolean }
type SegmenterInstance = { segment: (input: string) => Iterable<SegmenterItem> }
type SegmenterConstructor = new (
  locale: string,
  options: { granularity: 'word' },
) => SegmenterInstance

const queryStopWords = new Set([
  '的', '了', '是', '在', '和', '与', '及', '或', '对', '中', '有', '请问',
  '什么', '哪些', '如何', '是否', '为什么', '怎么',
])

function queryTerms(query: string) {
  const Segmenter = (Intl as unknown as { Segmenter?: SegmenterConstructor }).Segmenter
  const rawTerms: string[] = []
  if (Segmenter) {
    for (const item of new Segmenter('zh-CN', { granularity: 'word' }).segment(query)) {
      if (item.isWordLike) rawTerms.push(item.segment)
    }
  } else {
    rawTerms.push(...(query.match(/[A-Za-z0-9][A-Za-z0-9.+%/-]*|[\u3400-\u9fff]+/g) || []))
  }
  return [...new Set(rawTerms.map((term) => term.trim()).filter((term) => {
    if (!term || queryStopWords.has(term.toLocaleLowerCase())) return false
    return term.length > 1 || query.trim().length === 1
  }))].sort((left, right) => right.length - left.length)
}

function highlightParts(content: string): HighlightPart[] {
  const terms = queryTerms(form.query)
  if (!terms.length) return [{ text: content, highlighted: false }]
  const escaped = terms.map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  const pattern = new RegExp(escaped.join('|'), 'giu')
  const parts: HighlightPart[] = []
  let cursor = 0
  for (const match of content.matchAll(pattern)) {
    const index = match.index
    if (index > cursor) parts.push({ text: content.slice(cursor, index), highlighted: false })
    parts.push({ text: match[0], highlighted: true })
    cursor = index + match[0].length
  }
  if (cursor < content.length) parts.push({ text: content.slice(cursor), highlighted: false })
  return parts.length ? parts : [{ text: content, highlighted: false }]
}

async function search() {
  if (!form.query.trim()) return
  loading.value = true
  try {
    const response = await kbApi.search(props.kb.id, {
      ...form,
      meeting_ids: [],
    })
    results.value = response.items
    elapsed.value = response.took_ms
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '检索失败')
  } finally {
    loading.value = false
  }
}

function locator(result: SearchResult) {
  if (result.page_number) return `第 ${result.page_number} 页`
  if (result.slide_number) return `第 ${result.slide_number} 张幻灯片`
  if (result.speaker) {
    const time = result.time_range ? ` · ${Math.round(result.time_range.start_ms / 1000)}s–${Math.round(result.time_range.end_ms / 1000)}s` : ''
    return `${result.speaker}${time}`
  }
  return '文档原文'
}

onMounted(async () => {
  documents.value = await kbApi.documents(props.kb.id)
})
</script>

<template>
  <div class="search-workbench">
    <el-card class="search-console" shadow="never">
      <p class="eyebrow">HYBRID RETRIEVAL LAB</p>
      <h2>检索测试台</h2>
      <p>Dense 与 Sparse 双路召回，经 RRF 融合和 BGE Reranker 重排。此处只返回证据，不生成答案。</p>
      <el-input v-model="form.query" class="search-input" size="large" placeholder="输入需要核验的医学问题或概念" :prefix-icon="Search" @keyup.enter="search">
        <template #append><el-button type="primary" :loading="loading" @click="search">检索</el-button></template>
      </el-input>
      <div class="search-filters">
        <el-input-number v-model="form.top_k" :min="1" :max="50" controls-position="right" />
        <el-select v-model="form.content_types" multiple collapse-tags clearable placeholder="内容类型">
          <el-option v-for="type in ['paragraph', 'table', 'list', 'speech']" :key="type" :label="type" :value="type" />
        </el-select>
        <el-select v-model="form.document_ids" multiple collapse-tags clearable placeholder="限定文档">
          <el-option v-for="document in documents" :key="document.id" :label="`${document.filename} v${document.version}`" :value="document.id" />
        </el-select>
        <el-checkbox v-if="canIncludeDrafts" v-model="form.include_drafts">包含暂存内容</el-checkbox>
      </div>
    </el-card>

    <div v-if="elapsed !== undefined" class="result-summary">返回 {{ results.length }} 条证据 · {{ elapsed }} ms</div>
    <div v-loading="loading" class="result-list">
      <article v-for="(result, index) in results" :key="result.chunk_id" class="result-card">
        <div class="result-rank">{{ String(index + 1).padStart(2, '0') }}</div>
        <div class="result-body">
          <div class="result-source">
            <strong>{{ result.filename }}</strong>
            <span>v{{ result.document_version }}</span>
            <span>{{ locator(result) }}</span>
            <el-tag size="small" :type="result.publication_status === 'PUBLISHED' ? 'success' : 'warning'">{{ result.publication_status }}</el-tag>
          </div>
          <p class="result-evidence">
            <template v-for="(part, partIndex) in highlightParts(result.content)" :key="`${result.chunk_id}-${partIndex}`">
              <mark v-if="part.highlighted" class="query-highlight">{{ part.text }}</mark>
              <span v-else>{{ part.text }}</span>
            </template>
          </p>
          <div class="score-grid">
            <span>Dense <b>{{ result.dense_score.toFixed(4) }}</b></span>
            <span>Sparse <b>{{ result.sparse_score.toFixed(4) }}</b></span>
            <span>RRF <b>{{ result.fused_score.toFixed(4) }}</b></span>
            <span class="rerank">Rerank <b>{{ result.rerank_score.toFixed(4) }}</b></span>
          </div>
        </div>
      </article>
      <el-empty v-if="elapsed !== undefined && !results.length && !loading" description="没有找到符合过滤条件的证据" />
    </div>
  </div>
</template>
