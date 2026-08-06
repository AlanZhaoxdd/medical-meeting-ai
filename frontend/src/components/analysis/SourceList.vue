<script setup lang="ts">
import type { RagSource } from '@/types/meetingAnalysis'
import { ragSourceTypeLabels, ragSourceTypeTone, sourceSubtitle } from '@/utils/meetingAnalysis'

defineProps<{
  sources: RagSource[]
  title?: string
}>()

const emit = defineEmits<{
  open: [source: RagSource]
}>()
</script>

<template>
  <section class="source-list" :aria-label="title ?? '参考来源'">
    <h4 v-if="title">{{ title }}</h4>
    <ol>
      <li v-for="source in sources" :id="`source-${source.index}`" :key="source.id" class="source-item">
        <button type="button" class="source-button" @click="emit('open', source)">
          <span class="source-index">{{ source.index }}</span>
          <span class="source-body">
            <span class="source-line">
              <span :class="['source-tag', `source-tag--${ragSourceTypeTone[source.type]}`]">{{ ragSourceTypeLabels[source.type] }}</span>
              <strong>{{ source.title }}</strong>
            </span>
            <span class="source-snippet">{{ source.snippet }}</span>
            <span v-if="sourceSubtitle(source)" class="source-meta">{{ sourceSubtitle(source) }}</span>
          </span>
        </button>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.source-list { display: grid; gap: 8px; }
.source-list h4 { margin: 0 0 2px; color: #5d7380; font-size: 13px; font-weight: 700; }
.source-list ol { display: grid; gap: 8px; margin: 0; padding: 0; list-style: none; }
.source-item { margin: 0; }
.source-item:target { border-radius: 10px; outline: 2px solid #b9a7e8; outline-offset: 2px; }
.source-button { width: 100%; display: grid; grid-template-columns: 26px minmax(0, 1fr); gap: 10px; align-items: start; padding: 10px 12px; border: 1px solid #e6e9f2; border-radius: 9px; color: inherit; background: #fafafd; text-align: left; cursor: pointer; transition: border-color .15s, background .15s; }
.source-button:hover { border-color: #b9a7e8; background: #f6f3fd; }
.source-index { display: grid; place-items: center; width: 24px; height: 24px; border-radius: 6px; color: #5b3fa8; background: #ece6fb; font-size: 12px; font-weight: 700; }
.source-body { display: grid; gap: 4px; min-width: 0; }
.source-line { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; }
.source-line strong { color: #2b4360; font-size: 13px; }
.source-tag { padding: 1px 7px; border-radius: 999px; font-size: 11px; font-weight: 600; }
.source-tag--transcript { color: #0e6f6a; background: #e0f4f0; }
.source-tag--summary { color: #24579b; background: #e3eefc; }
.source-tag--history { color: #7a4d1d; background: #f6ead8; }
.source-tag--kb { color: #5b3fa8; background: #ece6fb; }
.source-tag--cutoff { color: #0e6f6a; background: #e0f4f0; }
.source-tag--open { color: #9a4a1f; background: #fbe9e0; }
.source-snippet { display: -webkit-box; overflow: hidden; color: #55697a; font-size: 12px; line-height: 1.55; -webkit-line-clamp: 3; -webkit-box-orient: vertical; white-space: pre-wrap; }
.source-meta { overflow: hidden; color: #97a3ac; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
</style>
