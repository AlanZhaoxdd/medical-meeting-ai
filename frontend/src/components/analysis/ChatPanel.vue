<script setup lang="ts">
import { ArrowDown, Delete, MagicStick, Plus, RefreshLeft } from '@element-plus/icons-vue'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import ChatComposer from '@/components/analysis/ChatComposer.vue'
import ChatMessageItem from '@/components/analysis/ChatMessageItem.vue'
import { createChatTransport } from '@/api/meetingAnalysis'
import type {
  ChatHandlers,
  ChatMessage,
  ChatScope,
  MeetingAnalysisContext,
  MeetingChatRequest,
  RagSource,
} from '@/types/meetingAnalysis'
import { chatScopeLabels, generateMessageId, isAbortError, recommendedQuestions } from '@/utils/meetingAnalysis'
import { toApiError } from '@/utils/errors'

const props = defineProps<{
  context: MeetingAnalysisContext | null
}>()

const emit = defineEmits<{
  openSource: [source: RagSource]
}>()

const router = useRouter()
const messages = ref<ChatMessage[]>([])
const draft = ref('')
const conversationId = ref<string>()
const scope = ref<ChatScope>('MEETING_AND_KB')
const transport = createChatTransport()
const activeController = ref<AbortController | null>(null)
const listRef = ref<HTMLElement>()
const atBottom = ref(true)
const sessionExpired = ref(false)
const transportLabel = ref(transport.mode === 'mock' ? 'Mock 模式' : transport.mode === 'sse' ? 'SSE 模式' : 'JSON 模式')

const generating = computed(() =>
  messages.value.some((message) => message.role === 'assistant' && ['sending', 'streaming', 'regenerating'].includes(message.status)),
)
watch(
  () => props.context?.meeting.id,
  () => {
    abortActive()
    messages.value = []
    conversationId.value = undefined
    draft.value = ''
    sessionExpired.value = false
  },
  { immediate: true },
)

watch(
  () => [messages.value.length, generating.value] as const,
  async () => {
    if (!atBottom.value) return
    await nextTick()
    scrollToBottom()
  },
)

function scrollToBottom() {
  const element = listRef.value
  if (element) element.scrollTop = element.scrollHeight
}

function onScroll() {
  const element = listRef.value
  if (!element) return
  atBottom.value = element.scrollHeight - element.scrollTop - element.clientHeight < 48
}

function abortActive() {
  activeController.value?.abort()
  activeController.value = null
}

function resetConversation() {
  abortActive()
  messages.value = []
  conversationId.value = undefined
  draft.value = ''
  sessionExpired.value = false
}

async function send(text?: string) {
  const question = (text ?? draft.value).trim()
  if (!question || !props.context || generating.value) return
  const context = props.context
  draft.value = ''
  sessionExpired.value = false
  atBottom.value = true

  const userMessage: ChatMessage = {
    id: generateMessageId(),
    role: 'user',
    content: question,
    status: 'complete',
    sources: [],
    createdAt: new Date().toISOString(),
  }
  const assistantMessage: ChatMessage = {
    id: generateMessageId(),
    role: 'assistant',
    content: '',
    status: 'streaming',
    stage: 'IDLE',
    sources: [],
    createdAt: new Date().toISOString(),
  }
  messages.value.push(userMessage, assistantMessage)
  await nextTick()
  scrollToBottom()

  const controller = new AbortController()
  activeController.value = controller
  const payload: MeetingChatRequest = {
    meetingId: context.meeting.id,
    conversationId: conversationId.value,
    question,
    scope: scope.value,
  }

  const handlers: ChatHandlers = {
    onStage: (stage) => {
      assistantMessage.stage = stage
    },
    onDelta: (delta) => {
      assistantMessage.content += delta
    },
    onDone: (response) => {
      conversationId.value = response.conversationId || conversationId.value
      assistantMessage.content = response.answer
      assistantMessage.messageId = response.messageId
      assistantMessage.conversationId = response.conversationId || conversationId.value
      assistantMessage.sources = response.sources ?? []
      assistantMessage.stage = 'DONE'
      assistantMessage.status =
        response.status === 'INSUFFICIENT_CONTEXT'
          ? 'insufficient'
          : response.status === 'FAILED'
            ? 'failed'
            : 'complete'
    },
    onError: (error) => {
      if (isAbortError(error)) {
        assistantMessage.status = 'stopped'
        assistantMessage.stage = undefined
        return
      }
      const apiError = toApiError(error)
      if (apiError.status === 401) sessionExpired.value = true
      assistantMessage.status = 'failed'
      assistantMessage.stage = undefined
      assistantMessage.error = apiError.status === 401 ? '会话已过期，请重新登录后继续问答。' : apiError.message
    },
  }

  try {
    await transport.chat(payload, context, handlers, controller.signal)
  } finally {
    if (activeController.value === controller) activeController.value = null
  }
}

function regenerate(message: ChatMessage) {
  const index = messages.value.findIndex((item) => item.id === message.id)
  if (index <= 0 || generating.value) return
  const userMessage = messages.value[index - 1]
  if (userMessage.role !== 'user') return
  messages.value = messages.value.slice(0, index - 1)
  void send(userMessage.content)
}

function stopGenerating() {
  abortActive()
}

onBeforeUnmount(() => {
  abortActive()
})

defineExpose({
  getConversation: () =>
    messages.value.map((message) => ({
      role: message.role,
      content: message.content,
    })),
})
</script>

<template>
  <section class="chat-panel">
    <header class="panel-header">
      <div class="panel-title">
        <h2>会议智能问答</h2>
        <p>问答范围：<strong>{{ chatScopeLabels[scope] }}</strong></p>
      </div>
      <div class="panel-toolbar">
        <el-radio-group v-model="scope" size="small" aria-label="问答范围">
          <el-radio-button value="CURRENT_MEETING">当前会议</el-radio-button>
          <el-radio-button value="MEETING_AND_KB">+ 知识库</el-radio-button>
        </el-radio-group>
        <el-tooltip content="新建对话" placement="top">
          <el-button text circle :icon="Plus" size="small" aria-label="新建对话" @click="resetConversation" />
        </el-tooltip>
        <el-tooltip content="清空当前对话" placement="top">
          <el-button text circle :icon="Delete" size="small" aria-label="清空当前对话" @click="resetConversation" />
        </el-tooltip>
      </div>
    </header>

    <el-alert v-if="sessionExpired" type="error" :closable="false" show-icon class="session-alert">
      <template #default>
        会话已过期，请重新登录后继续问答。
        <el-button size="small" :icon="RefreshLeft" @click="router.push('/auth')">重新登录</el-button>
      </template>
    </el-alert>

    <div ref="listRef" class="chat-scroll" @scroll="onScroll">
      <template v-if="!context">
        <div class="panel-hint">正在加载会议上下文…</div>
      </template>
      <template v-else-if="!messages.length">
        <div class="welcome">
          <div class="welcome-icon"><el-icon><MagicStick /></el-icon></div>
          <h3>会议智能问答</h3>
          <p>你可以围绕本次会议内容、参会者观点及相关知识库进行提问。</p>
          <div class="recommendations">
            <button v-for="question in recommendedQuestions" :key="question" type="button" :disabled="generating" @click="send(question)">
              {{ question }}
            </button>
          </div>
          <p class="transport-note">当前问答传输：{{ transportLabel }} · 回答基于会议记录与知识库，无依据时不生成答案</p>
        </div>
      </template>
      <template v-else>
        <ChatMessageItem
          v-for="message in messages"
          :key="message.id"
          :message="message"
          @regenerate="regenerate"
          @open-source="emit('openSource', $event)"
        />
      </template>
    </div>

    <el-button v-if="!atBottom && messages.length" class="back-to-bottom" circle :icon="ArrowDown" aria-label="回到底部" @click="scrollToBottom" />

    <ChatComposer v-model="draft" :disabled="!context || sessionExpired" :generating="generating" @send="send" @stop="stopGenerating" />
  </section>
</template>

<style scoped>
.chat-panel { position: relative; display: flex; flex-direction: column; min-height: 0; overflow: hidden; border: 1px solid #e4e6f2; border-radius: 14px; background: white; box-shadow: 0 4px 18px rgba(28, 30, 70, .05); }
.panel-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding: 16px 18px 12px; border-bottom: 1px solid #edf0f8; }
.panel-title h2 { margin: 0; color: #2b2f66; font-size: 16px; }
.panel-title p { margin: 5px 0 0; color: #8b93a8; font-size: 12px; }
.panel-title strong { color: #6c4fd0; font-weight: 650; }
.panel-toolbar { display: flex; align-items: center; gap: 6px; }
.panel-toolbar :deep(.el-radio-button__inner) { font-size: 12px; }
.session-alert { margin: 10px 14px 0; }
.chat-scroll { flex: 1; min-height: 0; overflow-y: auto; padding: 16px 16px 8px; scroll-behavior: smooth; }
.panel-hint { padding: 40px 0; color: #9aa5b0; font-size: 13px; text-align: center; }
.welcome { display: grid; justify-items: center; gap: 10px; padding: 34px 12px 20px; text-align: center; }
.welcome-icon { display: grid; place-items: center; width: 46px; height: 46px; border-radius: 14px 14px 14px 5px; color: white; background: linear-gradient(135deg, #6c4fd0, #8a6ae8); font-size: 22px; }
.welcome h3 { margin: 2px 0 0; color: #2b2f66; font-size: 16px; }
.welcome p { margin: 0; color: #6f7a90; font-size: 13px; line-height: 1.6; }
.recommendations { display: grid; grid-template-columns: 1fr; gap: 8px; width: 100%; margin-top: 8px; }
.recommendations button { padding: 10px 12px; border: 1px solid #e2e4f2; border-radius: 10px; color: #3d3f78; background: #f8f7fd; font-size: 12px; line-height: 1.5; text-align: left; cursor: pointer; transition: border-color .15s, background .15s; }
.recommendations button:hover { border-color: #a99be0; background: #f0ecfc; }
.recommendations button:disabled { opacity: .55; cursor: not-allowed; }
.transport-note { color: #a2abb8 !important; font-size: 11px; }
.back-to-bottom { position: absolute; right: 22px; bottom: 108px; z-index: 2; box-shadow: 0 4px 12px rgba(28, 30, 70, .16); }
</style>
