<script setup lang="ts">
import { Check, Close, Plus } from '@element-plus/icons-vue'
import { computed } from 'vue'
import type { QuestionCandidate } from '@/types/meetingVerification'

const props = defineProps<{
  cutPoints: QuestionCandidate[]
  openEnded: QuestionCandidate[]
  selectedIds: Set<string>
  swapLoadingId: string | null
  poolExhausted: { cut_point: boolean; open_ended: boolean }
  showMoreAvailable: { cut_point: boolean; open_ended: boolean }
  showMoreCount: { cut_point: number; open_ended: number }
  readonly?: boolean
  saving?: boolean
  groups?: Array<'cut_point' | 'open_ended'>
}>()

const emit = defineEmits<{
  select: [question: QuestionCandidate, selected: boolean]
  swap: [question: QuestionCandidate]
  showMore: [type: 'cut_point' | 'open_ended']
  add: [type: 'cut_point' | 'open_ended']
  edit: [question: QuestionCandidate]
  remove: [question: QuestionCandidate]
  evidence: [question: QuestionCandidate]
}>()

const isManual = (question: QuestionCandidate) => question.source !== 'ai' || question.rank == null
const groupDefs = computed(() => [
  {
    key: 'cut_point' as const,
    label: '切点问题',
    description: '点击选中带入分析的关键决策点；不满意可点右上角“换掉”从候选池补位。',
    questions: props.cutPoints,
  },
  {
    key: 'open_ended' as const,
    label: '开放性问题',
    description: '点击选中带入分析的讨论性问题；换掉会从候选池取排名更低的候选。',
    questions: props.openEnded,
  },
].filter((group) => !props.groups || props.groups.includes(group.key)))

function toggle(question: QuestionCandidate) {
  if (props.readonly) return
  emit('select', question, !props.selectedIds.has(question.id))
}
</script>

<template>
  <div class="question-selection-panels">
    <section
      v-for="group in groupDefs"
      :key="group.key"
      class="selection-group"
    >
      <div class="group-heading">
        <div>
          <h2>{{ group.label }}</h2>
          <p>{{ group.description }}</p>
        </div>
        <div class="group-actions">
          <span class="selection-count">已选 {{ [...selectedIds].filter((id) => group.questions.some((q) => q.id === id)).length }} 条</span>
          <el-button v-if="showMoreAvailable[group.key]" size="small" plain @click="emit('showMore', group.key)">查看后 {{ showMoreCount[group.key] }} 条候选</el-button>
          <el-button v-if="!readonly" size="small" type="primary" plain :icon="Plus" :loading="saving" @click="emit('add', group.key)">新增问题</el-button>
        </div>
      </div>

      <div v-if="group.questions.length" class="candidate-list">
        <article
          v-for="question in group.questions"
          :key="question.id"
          class="candidate-card"
          :class="{ selected: selectedIds.has(question.id), manual: isManual(question) }"
          @click="toggle(question)"
        >
          <div class="candidate-main">
            <span class="candidate-rank">{{ question.rank ?? (question.source === 'manual' ? '人工' : '备选') }}</span>
            <div class="candidate-body">
              <p>{{ question.content }}</p>
              <div class="candidate-meta">
                <el-tag v-if="selectedIds.has(question.id)" size="small" type="success" effect="light">已选中</el-tag>
                <span v-if="question.support_score != null">支持度 {{ Math.round(question.support_score * 100) }}%</span>
                <span v-if="question.evidence_count">证据 {{ question.evidence_count }} 条</span>
                <span v-if="question.topic">主题：{{ question.topic }}</span>
              </div>
            </div>
          </div>
          <div class="candidate-actions" @click.stop>
            <el-tooltip v-if="!isManual(question) && !readonly" :content="poolExhausted[group.key] ? '候选池已无更多候选' : '换掉，取排名更低的候选'" placement="top">
              <el-button text circle :icon="Close" size="small" :loading="swapLoadingId === question.id" :disabled="readonly || poolExhausted[group.key]" @click="emit('swap', question)" />
            </el-tooltip>
            <el-button v-if="question.evidence_count" text size="small" @click="emit('evidence', question)">查看来源</el-button>
            <template v-if="!readonly">
              <el-button text size="small" @click="emit('edit', question)">编辑</el-button>
              <el-button text type="danger" size="small" @click="emit('remove', question)">删除</el-button>
            </template>
          </div>
          <el-icon v-if="selectedIds.has(question.id)" class="selected-mark"><Check /></el-icon>
        </article>
      </div>
      <el-empty v-else :image-size="54" description="暂无候选，可新增一条" />
    </section>
  </div>
</template>

<style scoped>
.question-selection-panels { display: grid; gap: 18px; }
.selection-group { position: relative; padding: 22px; border: 1px solid var(--line); border-radius: 12px; background: white; }
.group-heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 16px; }
.group-heading h2 { margin: 0 0 5px; color: var(--navy); font-size: 17px; }
.group-heading p { margin: 0; color: #82929a; font-size: 13px; line-height: 1.6; }
.group-actions { display: flex; align-items: center; gap: 8px; white-space: nowrap; }
.selection-count { color: #168378; font-size: 12px; font-weight: 650; }
.candidate-list { display: grid; gap: 10px; }
.candidate-card { position: relative; display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding: 14px 16px; border: 1px solid #e5eeeb; border-radius: 10px; background: #fafcfb; cursor: pointer; transition: border-color .15s, background .15s, box-shadow .15s; }
.candidate-card:hover { border-color: #9fcfc5; }
.candidate-card.selected { border-color: #34a853; background: #effaf1; box-shadow: 0 0 0 1px #34a853 inset; }
.candidate-card.selected:hover { border-color: #2b8f46; }
.candidate-main { display: flex; gap: 12px; min-width: 0; }
.candidate-rank { display: grid; place-items: center; flex: 0 0 30px; height: 30px; border-radius: 8px; color: #11746c; background: #e1f3ed; font-size: 12px; font-weight: 700; }
.candidate-card.selected .candidate-rank { color: #1d7a33; background: #d5edd9; }
.candidate-card.manual .candidate-rank { color: #7a6a3a; background: #f3ecd8; }
.candidate-body { min-width: 0; }
.candidate-body p { margin: 1px 0 6px; color: #2f5060; font-size: 13px; line-height: 1.55; white-space: pre-wrap; }
.candidate-meta { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; color: #9aa7ac; font-size: 11px; }
.candidate-actions { display: flex; align-items: center; gap: 2px; flex: 0 0 auto; }
.selected-mark { position: absolute; top: -9px; right: -7px; display: grid; place-items: center; width: 22px; height: 22px; border-radius: 50%; color: white; background: #34a853; font-size: 13px; box-shadow: 0 2px 6px rgba(52, 168, 83, .35); }
@media (max-width: 700px) {
  .group-heading { flex-direction: column; }
  .candidate-card { flex-direction: column; }
  .candidate-actions { align-self: flex-end; }
}
</style>
