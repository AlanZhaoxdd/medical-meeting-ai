<script setup lang="ts">
import { computed } from 'vue'
import type { VerificationQuestion, VerificationQuestionType } from '@/types/meetingVerification'
import { questionGroups, sortQuestionsBySupport } from '@/utils/meetingVerification'

const props = defineProps<{
  cutPointQuestions: VerificationQuestion[]
  openEndedQuestions: VerificationQuestion[]
  readonly?: boolean
  saving?: boolean
}>()
const emit = defineEmits<{
  add: [type: VerificationQuestionType]
  edit: [question: VerificationQuestion]
  remove: [question: VerificationQuestion]
  evidence: [question: VerificationQuestion]
}>()

const originLabel = (question: VerificationQuestion) => question.origin === 'USER_CREATED' || question.source === 'manual' ? '人工新增' : question.origin === 'AI_GENERATED' ? 'AI 生成' : question.source || '系统提取'
const reviewStatusLabel = (status?: string | null) => ({ AI_DRAFT: 'AI 草稿', USER_EDITED: '已人工修改', CONFIRMED: '已确认', REJECTED: '已驳回' }[status ?? ''] ?? '')
const sortedGroups = computed(() => questionGroups({ cut_point_questions: props.cutPointQuestions, open_ended_questions: props.openEndedQuestions }).map((group) => ({
  ...group,
  questions: sortQuestionsBySupport(group.questions),
})))
</script>

<template>
  <div class="question-groups">
    <section v-for="group in sortedGroups" :key="group.key" class="question-group">
      <div class="group-heading"><div><h2>{{ group.label }}</h2><p>{{ group.key === 'cut_point' ? '用于确认会议讨论中的关键决策点。' : '用于补充背景、观点和后续行动。' }}</p></div><el-button v-if="!readonly" size="small" plain :loading="saving" @click="emit('add', group.key)">新增问题</el-button></div>
      <div v-if="group.questions.length" class="question-items">
        <article v-for="(question, index) in group.questions" :key="question.id" class="question-item">
          <div class="question-index">{{ index + 1 }}</div>
          <div class="question-content"><p>{{ question.content }}</p><small>版本 {{ question.version }} · {{ originLabel(question) }}<template v-if="question.topic"> · 主题：{{ question.topic }}</template><template v-if="reviewStatusLabel(question.review_status)"> · {{ reviewStatusLabel(question.review_status) }}</template><template v-if="question.support_score !== null && question.support_score !== undefined"> · 支持度 {{ Math.round(question.support_score * 100) }}%</template><template v-else-if="question.confidence !== null && question.confidence !== undefined"> · 置信度 {{ Math.round(question.confidence * 100) }}%</template><template v-if="question.evidence_count"> · {{ question.evidence_count }} 条证据</template></small></div>
          <div v-if="!readonly" class="question-actions"><el-button v-if="question.evidence_count || question.origin === 'AI_GENERATED'" text size="small" :disabled="saving" @click="emit('evidence', question)">查看来源</el-button><el-button text size="small" :disabled="saving" @click="emit('edit', question)">编辑</el-button><el-button text type="danger" size="small" :disabled="saving" @click="emit('remove', question)">删除</el-button></div>
          <div v-else-if="question.evidence_count || question.origin === 'AI_GENERATED'" class="question-actions"><el-button text size="small" :disabled="saving" @click="emit('evidence', question)">查看来源</el-button></div>
        </article>
      </div>
      <el-empty v-else :image-size="54" description="暂无问题，请新增一条" />
    </section>
  </div>
</template>

<style scoped>
.question-groups { display: grid; gap: 18px; }
.question-group { padding: 22px; border: 1px solid var(--line); border-radius: 12px; background: white; }
.group-heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 16px; }
.group-heading h2 { margin: 0 0 5px; color: var(--navy); font-size: 17px; }
.group-heading p { margin: 0; color: #82929a; font-size: 13px; }
.question-items { display: grid; gap: 10px; }
.question-item { display: grid; grid-template-columns: 30px minmax(0, 1fr) auto; gap: 12px; align-items: start; padding: 14px; border: 1px solid #e5eeeb; border-radius: 9px; }
.question-index { display: grid; place-items: center; width: 26px; height: 26px; border-radius: 50%; color: #11746c; background: #e1f3ed; font-size: 12px; font-weight: 700; }
.question-content p { margin: 1px 0 5px; color: #2f5060; line-height: 1.55; white-space: pre-wrap; }
.question-content small { color: #9aa7ac; font-size: 11px; }
.question-actions { display: flex; gap: 2px; }
@media (max-width: 600px) { .question-item { grid-template-columns: 30px minmax(0, 1fr); } .question-actions { grid-column: 2; } }
</style>
