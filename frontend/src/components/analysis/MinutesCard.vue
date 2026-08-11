<script setup lang="ts">
import { CopyDocument } from '@element-plus/icons-vue'
import { computed } from 'vue'
import SourceList from '@/components/analysis/SourceList.vue'
import type { AnalysisModule, RagSource } from '@/types/meetingAnalysis'
import { copyText, renderMarkdown } from '@/utils/meetingAnalysis'

const props = defineProps<{
  module: AnalysisModule
}>()

const emit = defineEmits<{
  openSource: [source: RagSource]
}>()

const markdownHtml = computed(() => (props.module.content ? renderMarkdown(props.module.content, props.module.references) : ''))

async function copyMinutes() {
  const parts = [props.module.title]
  if (props.module.content) parts.push(props.module.content)
  if (props.module.references.length) {
    parts.push(props.module.references.map((source) => `[${source.index}] ${source.title}：${source.snippet}`).join('\n'))
  }
  const copied = await copyText(parts.join('\n\n'))
  if (copied) window.dispatchEvent(new CustomEvent('app-toast', { detail: '纪要内容已复制' }))
}

function onContentClick(event: MouseEvent) {
  const target = (event.target as HTMLElement | null)?.closest('.citation-anchor')
  if (!target) return
  const index = target.getAttribute('href')?.replace('#source-', '')
  if (!index) return
  event.preventDefault()
  const element = document.getElementById(`source-${index}`)
  element?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}
</script>

<template>
  <el-card class="minutes-card" shadow="never">
    <div class="minutes-head">
      <div class="minutes-title">
        <p class="eyebrow">AI FULL-ARTICLE MINUTES</p>
        <h3>{{ module.title }}</h3>
        <p v-if="module.description">{{ module.description }}</p>
      </div>
      <el-tooltip content="复制纪要" placement="top">
        <el-button text circle :icon="CopyDocument" :disabled="module.state !== 'ready'" @click="copyMinutes" />
      </el-tooltip>
    </div>

    <div class="minutes-body">
      <el-empty v-if="module.state === 'empty'" :image-size="44" description="暂无可用内容" />
      <div v-else-if="markdownHtml" class="markdown-body" @click="onContentClick">
        <!-- Rendered by marked + DOMPurify; raw HTML is sanitized before injection. -->
        <!-- eslint-disable-next-line vue/no-v-html -->
        <div v-html="markdownHtml" />
      </div>
      <el-empty v-else :image-size="44" description="暂无可用内容" />

      <div v-if="module.references.length" class="minutes-references">
        <SourceList :sources="module.references" title="引用依据" @open="emit('openSource', $event)" />
      </div>
    </div>
  </el-card>
</template>

<style scoped>
.minutes-card { border: 1px solid var(--line); border-radius: 12px; }
.minutes-card :deep(.el-card__body) { padding: 24px 26px; }
.minutes-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.minutes-title { display: grid; gap: 4px; }
.minutes-title .eyebrow { margin: 0; color: #6c4fd0; font-size: 11px; font-weight: 700; letter-spacing: .12em; }
.minutes-title h3 { margin: 0; color: #173f58; font-size: 20px; }
.minutes-title p:last-child { margin: 2px 0 0; color: #84949c; font-size: 12px; line-height: 1.5; }
.minutes-body { padding-top: 16px; margin-top: 16px; border-top: 1px solid #edf2f0; }
.minutes-references { margin-top: 20px; padding-top: 16px; border-top: 1px dashed #e2e8f0; }
:deep(.markdown-body) { color: #314e62; font-size: 14px; line-height: 1.8; }
:deep(.markdown-body h1), :deep(.markdown-body h2), :deep(.markdown-body h3), :deep(.markdown-body h4) { margin: 18px 0 8px; color: #1d3b55; font-weight: 700; }
:deep(.markdown-body h1) { font-size: 20px; }
:deep(.markdown-body h2) { font-size: 18px; }
:deep(.markdown-body h3) { font-size: 16px; }
:deep(.markdown-body h4) { font-size: 15px; }
:deep(.markdown-body h1:first-child), :deep(.markdown-body h2:first-child), :deep(.markdown-body h3:first-child) { margin-top: 0; }
:deep(.markdown-body p), :deep(.markdown-body ul), :deep(.markdown-body ol), :deep(.markdown-body blockquote), :deep(.markdown-body pre), :deep(.markdown-body table) { margin: 10px 0; }
:deep(.markdown-body ul), :deep(.markdown-body ol) { padding-left: 22px; }
:deep(.markdown-body blockquote) { padding: 8px 14px; border-left: 3px solid #c4b7ee; color: #5d6f7d; background: #f7f5fc; }
:deep(.markdown-body code) { padding: 1px 5px; border-radius: 4px; color: #4c3a8f; background: #efeafb; font-size: 12px; }
:deep(.markdown-body pre) { padding: 10px 12px; overflow: auto; border-radius: 8px; color: #dbe8ef; background: #17354a; }
:deep(.markdown-body pre code) { padding: 0; color: inherit; background: transparent; }
:deep(.markdown-body table) { width: 100%; border-collapse: collapse; }
:deep(.markdown-body th), :deep(.markdown-body td) { padding: 6px 9px; border: 1px solid #dde6e3; text-align: left; }
:deep(.markdown-body th) { color: #1d3b55; background: #f0f6f4; }
:deep(.markdown-body .citation-anchor) { color: #6c4fd0; font-weight: 700; text-decoration: none; }
:deep(.markdown-body .citation-anchor:hover) { text-decoration: underline; }
@media (max-width: 640px) {
  .minutes-card :deep(.el-card__body) { padding: 18px 16px; }
}
</style>
