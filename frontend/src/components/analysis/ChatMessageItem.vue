<script setup lang="ts">
import { ChatLineRound, CopyDocument, MagicStick, RefreshRight } from '@element-plus/icons-vue'
import { computed, ref } from 'vue'
import SourceList from '@/components/analysis/SourceList.vue'
import type { ChatMessage, RagSource } from '@/types/meetingAnalysis'
import {
  chatMessageStatusLabels,
  chatRouteLabels,
  copyText,
  insufficientContextNotice,
  markdownToPlainText,
  ragStageLabels,
  renderMarkdown,
} from '@/utils/meetingAnalysis'

const props = defineProps<{
  message: ChatMessage
}>()

const emit = defineEmits<{
  copy: [message: ChatMessage]
  regenerate: [message: ChatMessage]
  like: [message: ChatMessage]
  dislike: [message: ChatMessage]
  openSource: [source: RagSource]
}>()

const sourcesVisible = ref(false)
const feedback = ref<'like' | 'dislike' | null>(null)
const isAssistant = computed(() => props.message.role === 'assistant')
const isActive = computed(() => props.message.status === 'streaming' || props.message.status === 'sending' || props.message.status === 'regenerating')
const markdownHtml = computed(() => (props.message.content ? renderMarkdown(props.message.content, props.message.sources) : ''))

async function copyAnswer() {
  const copied = await copyText(markdownToPlainText(props.message.content))
  if (copied) window.dispatchEvent(new CustomEvent('app-toast', { detail: '回答已复制' }))
}

function onContentClick(event: MouseEvent) {
  const target = (event.target as HTMLElement | null)?.closest('.citation-anchor')
  if (!target) return
  const index = target.getAttribute('href')?.replace('#source-', '')
  if (!index) return
  event.preventDefault()
  sourcesVisible.value = true
  nextTickScroll(`source-${index}`)
}

function nextTickScroll(id: string) {
  window.setTimeout(() => {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, 60)
}
</script>

<template>
  <article class="chat-message" :class="message.role">
    <div v-if="isAssistant" class="message-brand">
      <el-icon><MagicStick /></el-icon>
    </div>
    <div class="message-main">
      <div v-if="isAssistant" class="message-meta">
        <strong>AI 纪要助手</strong>
        <el-tag v-if="message.route && !isActive" size="small" effect="plain">{{ chatRouteLabels[message.route] }}</el-tag>
        <el-tag v-if="isActive && message.stage" size="small" effect="light" type="warning">{{ ragStageLabels[message.stage] }}</el-tag>
        <el-tag v-else-if="isActive" size="small" effect="light" type="warning">{{ chatMessageStatusLabels[message.status] }}</el-tag>
      </div>

      <div class="bubble">
        <div v-if="message.role === 'user'" class="user-content">{{ message.content }}</div>
        <template v-else>
          <div v-if="markdownHtml && message.status !== 'insufficient'" class="markdown-body" @click="onContentClick">
            <!-- eslint-disable-next-line vue/no-v-html -->
            <div v-html="markdownHtml" />
            <span v-if="isActive" class="stream-cursor" />
          </div>
          <div v-else-if="isActive" class="stream-placeholder">
            <span class="stream-cursor" />
          </div>

          <div v-if="message.status === 'insufficient'" class="notice notice--insufficient">
            <p>{{ insufficientContextNotice }}</p>
          </div>
          <div v-else-if="message.status === 'failed'" class="notice notice--error">
            <p>{{ message.error || '回答生成失败，请重试。' }}</p>
          </div>
          <div v-else-if="message.status === 'stopped'" class="notice notice--stopped">
            <p>生成已停止。你可以点击重新生成，或继续提问。</p>
          </div>
        </template>
      </div>

      <div v-if="isAssistant && message.sources.length" class="message-sources">
        <el-button text size="small" :icon="ChatLineRound" @click="sourcesVisible = !sourcesVisible">
          查看引用来源（{{ message.sources.length }}）
        </el-button>
        <SourceList v-if="sourcesVisible" :sources="message.sources" class="sources-body" @open="emit('openSource', $event)" />
      </div>

      <div v-if="isAssistant && message.status !== 'sending' && message.status !== 'streaming'" class="message-actions">
        <el-tooltip content="复制答案" placement="top"><el-button text circle :icon="CopyDocument" size="small" @click="copyAnswer" /></el-tooltip>
        <el-tooltip content="重新生成" placement="top"><el-button text circle :icon="RefreshRight" size="small" @click="emit('regenerate', message)" /></el-tooltip>
        <el-tooltip content="有帮助" placement="top"><el-button text circle size="small" :type="feedback === 'like' ? 'primary' : ''" @click="feedback = feedback === 'like' ? null : 'like'">赞</el-button></el-tooltip>
        <el-tooltip content="无帮助" placement="top"><el-button text circle size="small" :type="feedback === 'dislike' ? 'danger' : ''" @click="feedback = feedback === 'dislike' ? null : 'dislike'">踩</el-button></el-tooltip>
      </div>
    </div>
  </article>
</template>

<style scoped>
.chat-message { display: grid; grid-template-columns: 30px minmax(0, 1fr); gap: 10px; }
.chat-message.user { grid-template-columns: minmax(0, 1fr); }
.message-brand { display: grid; place-items: center; width: 28px; height: 28px; border-radius: 9px 9px 9px 3px; color: white; background: linear-gradient(135deg, #6c4fd0, #8a6ae8); }
.message-main { display: grid; gap: 6px; min-width: 0; }
.chat-message.user .message-main { justify-items: end; }
.message-meta { display: flex; align-items: center; gap: 8px; }
.message-meta strong { color: #2b4360; font-size: 12px; }
.bubble { max-width: 100%; padding: 11px 14px; border-radius: 12px; }
.chat-message.assistant .bubble { border: 1px solid #e6e9f2; color: #314e62; background: white; }
.chat-message.user .bubble { color: white; background: #2b5f8f; }
.user-content { color: inherit; font-size: 13px; line-height: 1.6; white-space: pre-wrap; }
.markdown-body { font-size: 13px; line-height: 1.75; }
.markdown-body h1, .markdown-body h2, .markdown-body h3, .markdown-body h4 { margin: 12px 0 7px; color: #1d3b55; }
.markdown-body h1:first-child, .markdown-body h2:first-child, .markdown-body h3:first-child { margin-top: 0; }
.markdown-body p, .markdown-body ul, .markdown-body ol, .markdown-body blockquote, .markdown-body pre, .markdown-body table { margin: 8px 0; }
.markdown-body ul, .markdown-body ol { padding-left: 20px; }
.markdown-body blockquote { padding: 6px 12px; border-left: 3px solid #c4b7ee; color: #5d6f7d; background: #f7f5fc; }
.markdown-body code { padding: 1px 5px; border-radius: 4px; color: #4c3a8f; background: #efeafb; font-size: 12px; }
.markdown-body pre { padding: 10px 12px; overflow: auto; border-radius: 8px; color: #dbe8ef; background: #17354a; }
.markdown-body pre code { padding: 0; color: inherit; background: transparent; }
.markdown-body table { width: 100%; border-collapse: collapse; }
.markdown-body th, .markdown-body td { padding: 6px 9px; border: 1px solid #dde6e3; text-align: left; }
.markdown-body th { color: #1d3b55; background: #f0f6f4; }
.markdown-body .citation-anchor { color: #6c4fd0; font-weight: 700; text-decoration: none; }
.markdown-body .citation-anchor:hover { text-decoration: underline; }
.stream-placeholder { min-height: 24px; }
.stream-cursor { display: inline-block; width: 7px; height: 15px; margin-left: 2px; border-radius: 2px; background: #7c5fd8; vertical-align: text-bottom; animation: blink 0.9s steps(2, start) infinite; }
@keyframes blink { 50% { opacity: 0; } }
.notice { padding: 10px 12px; margin-top: 10px; border-radius: 8px; font-size: 12px; line-height: 1.6; }
.notice p { margin: 0; }
.notice--insufficient { color: #7a5a12; background: #fdf6e3; border: 1px solid #f2e3b3; }
.notice--error { color: #a13c3c; background: #fdeeee; border: 1px solid #f4cccc; }
.notice--stopped { color: #5d6f7d; background: #f2f5f7; border: 1px solid #e0e6ea; }
.message-sources { display: grid; gap: 8px; }
.message-actions { display: flex; align-items: center; gap: 2px; margin-top: 2px; }
:deep(.sources-body) { padding: 10px; border: 1px solid #e6e9f2; border-radius: 10px; background: #fafafd; }
</style>
