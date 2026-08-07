import { http } from '@/api/client'
import { kbApi } from '@/api/kb'
import { meetingsApi } from '@/api/meetings'
import { meetingVerificationApi } from '@/api/meetingVerification'
import { mockChatTransport } from '@/api/meetingAnalysis.mock'
import { getAccessToken } from '@/stores/auth'
import type {
  AnalysisDocument,
  AnalysisModule,
  ChatHandlers,
  MeetingAnalysisContext,
  MeetingChatMode,
  MeetingChatRequest,
  MeetingChatResponse,
  MeetingChatTransport,
  RagStage,
  TranscriptSegment,
} from '@/types/meetingAnalysis'
import { isChatTransportMode } from '@/types/meetingAnalysis'
import { attendeeCount } from '@/utils/meetingVerification'
import { normalizeAnalysisModules, normalizeAnalysisSources } from '@/utils/meetingAnalysis'

const analysisEndpoint = (meetingId: string) => `/api/v1/meetings/${meetingId}/analysis`
const chatEndpoint = (meetingId: string) => `/api/v1/meetings/${meetingId}/ai-chat`
// CPU-only embedding/reranking can take more than one minute before the LLM
// is called. Keep the client alive long enough for the grounded answer
// pipeline to finish instead of aborting a valid request at 60 seconds.
const CHAT_TIMEOUT_MS = 180_000

function normalizeDocument(raw: unknown): AnalysisDocument | null {
  if (!raw || typeof raw !== 'object') return null
  const value = raw as Record<string, unknown>
  const id = typeof value.id === 'string' ? value.id : ''
  const knowledgeBaseId = typeof value.knowledge_base_id === 'string' ? value.knowledge_base_id : ''
  if (!id || !knowledgeBaseId) return null
  return {
    id,
    knowledgeBaseId,
    meetingId: typeof value.meeting_id === 'string' ? value.meeting_id : null,
    filename: typeof value.filename === 'string' ? value.filename : '未命名文档',
    sourceType: typeof value.source_type === 'string' ? value.source_type : 'document',
    status: typeof value.status === 'string' ? value.status : undefined,
  }
}

function normalizeTranscriptBlocks(raw: unknown, documentId: string): TranscriptSegment[] {
  if (!Array.isArray(raw)) return []
  return raw
    .map((item, index) => {
      if (!item || typeof item !== 'object') return null
      const value = item as Record<string, unknown>
      const text = typeof value.text === 'string' ? value.text : typeof value.table_markdown === 'string' ? value.table_markdown : ''
      if (!text.trim()) return null
      return {
        id: typeof value.block_id === 'string' ? value.block_id : `block-${index + 1}`,
        order: typeof value.order === 'number' ? value.order : index + 1,
        speaker: typeof value.speaker === 'string' ? value.speaker : null,
        startMs: typeof value.start_ms === 'number' ? value.start_ms : null,
        endMs: typeof value.end_ms === 'number' ? value.end_ms : null,
        text: text.trim(),
        documentId,
      }
    })
    .filter((segment): segment is TranscriptSegment => Boolean(segment))
    .sort((a, b) => a.order - b.order)
}

/**
 * Load the real meeting data the analysis page depends on:
 * meeting info, verification questions, linked KB documents and transcript.
 */
export async function loadAnalysisContext(meetingId: string): Promise<MeetingAnalysisContext> {
  const [meeting, verification] = await Promise.all([meetingsApi.get(meetingId), meetingVerificationApi.get(meetingId)])
  const knowledgeBaseId = meeting.knowledge_base_id ?? null
  if (!knowledgeBaseId) {
    return {
      meeting,
      verification,
      knowledgeBaseId: null,
      knowledgeBaseName: null,
      documents: [],
      transcript: [],
      transcriptDocumentId: null,
    }
  }

  let knowledgeBaseName: string | null = null
  let documents: AnalysisDocument[] = []
  let transcript: TranscriptSegment[] = []
  let transcriptDocumentId: string | null = null
  try {
    const knowledgeBase = await kbApi.get(knowledgeBaseId)
    knowledgeBaseName = knowledgeBase.name ?? null
  } catch {
    knowledgeBaseName = null
  }

  try {
    const rawDocuments = await kbApi.documents(knowledgeBaseId)
    documents = rawDocuments
      .map(normalizeDocument)
      .filter((document): document is AnalysisDocument => Boolean(document) && (!document.meetingId || document.meetingId === meetingId))
  } catch {
    documents = []
  }

  const transcriptDocument = documents.find(
    (document) => document.sourceType === 'transcript' || document.filename.toLowerCase().endsWith('.json'),
  )
  if (transcriptDocument) {
    try {
      const blocks = await kbApi.blocks(knowledgeBaseId, transcriptDocument.id)
      transcript = normalizeTranscriptBlocks(blocks, transcriptDocument.id)
      transcriptDocumentId = transcriptDocument.id
    } catch {
      transcript = []
      transcriptDocumentId = null
    }
  }

  return { meeting, verification, knowledgeBaseId, knowledgeBaseName, documents, transcript, transcriptDocumentId }
}

/**
 * Load the real persisted AI analysis result from the backend. The page then
 * composes the modules into a single "AI 通读纪要" document.
 */
export async function getAnalysisModules(meetingId: string): Promise<AnalysisModule[]> {
  const { data } = await http.get<unknown>(analysisEndpoint(meetingId), { timeout: 20_000 })
  const value = (data && typeof data === 'object' ? data : {}) as Record<string, unknown>
  const sources = normalizeAnalysisSources(value.sources)
  return normalizeAnalysisModules(value.modules, sources)
}

/**
 * Re-run the analysis task with the previously saved selection. Returns the
 * analysis task so the caller can poll until completion.
 */
export async function reanalyzeMeeting(
  meetingId: string,
  context: MeetingAnalysisContext,
): Promise<{ task_id: string; status: string }> {
  const task = await meetingVerificationApi.reanalyzeAnalysis(meetingId, {
    expected_version: context.meeting.verification_version ?? 1,
    selected_question_ids: [],
  })
  return { task_id: task.task_id, status: task.status }
}

export function readChatResponse(data: unknown): MeetingChatResponse {
  let payload = data
  if (typeof payload === 'string') {
    try {
      payload = JSON.parse(payload) as unknown
    } catch {
      throw new Error('问答接口返回的 JSON 无法解析。')
    }
  }
  if (!payload || typeof payload !== 'object') {
    throw new Error('问答接口返回格式不正确。')
  }
  const rawPayload = payload as Record<string, unknown>
  const nested = rawPayload.data ?? rawPayload.result ?? rawPayload.response
  if (nested && typeof nested === 'object') payload = nested
  const value = payload as Record<string, unknown>
  const rawSources = Array.isArray(value.sources)
    ? value.sources
    : Array.isArray(value.citations)
      ? value.citations
      : []
  return {
    conversationId: typeof value.conversationId === 'string' ? value.conversationId : typeof value.conversation_id === 'string' ? value.conversation_id : '',
    messageId: typeof value.messageId === 'string' ? value.messageId : typeof value.message_id === 'string' ? value.message_id : `msg-${Date.now()}`,
    answer: typeof value.answer === 'string' ? value.answer : '',
    status: value.status === 'INSUFFICIENT_CONTEXT' || value.status === 'FAILED' ? value.status : 'COMPLETED',
    sources: normalizeAnalysisSources(rawSources),
    suggestedQuestions: Array.isArray(value.suggestedQuestions) ? value.suggestedQuestions.map(String) : Array.isArray(value.suggested_questions) ? value.suggested_questions.map(String) : undefined,
  }
}

const jsonChatTransport: MeetingChatTransport = {
  mode: 'json',
  async chat(payload, _context, handlers, signal) {
    const { data } = await http.post<unknown>(chatEndpoint(payload.meetingId), payload, {
      timeout: CHAT_TIMEOUT_MS,
      signal,
    })
    handlers.onDone?.(readChatResponse(data))
  },
}

function createAbortCombiner(signal?: AbortSignal): AbortController {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), CHAT_TIMEOUT_MS)
  const onAbort = () => controller.abort()
  signal?.addEventListener('abort', onAbort, { once: true })
  const originalAbort = controller.abort.bind(controller)
  controller.abort = () => {
    window.clearTimeout(timeout)
    signal?.removeEventListener('abort', onAbort)
    originalAbort()
  }
  return controller
}

const sseChatTransport: MeetingChatTransport = {
  mode: 'sse',
  async chat(payload, _context, handlers, signal) {
    const controller = createAbortCombiner(signal)
    const token = getAccessToken()
    const response = await fetch(chatEndpoint(payload.meetingId), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(payload),
      signal: controller.signal,
    })
    if (!response.ok || !response.body) {
      const message = response.ok ? '服务未返回流式内容。' : `问答请求失败（${response.status}）。`
      throw new Error(message)
    }
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let sawEvent = false
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const events = buffer.split('\n\n')
      buffer = events.pop() ?? ''
      for (const event of events) {
        sawEvent = true
        const dataLine = event.split('\n').find((line) => line.startsWith('data:'))
        if (!dataLine) continue
        const raw = dataLine.slice(5).trim()
        if (!raw || raw === '[DONE]') continue
        let parsed: Record<string, unknown>
        try {
          parsed = JSON.parse(raw) as Record<string, unknown>
        } catch {
          continue
        }
        if (parsed.type === 'stage' && typeof parsed.stage === 'string') {
          handlers.onStage?.(parsed.stage as RagStage)
        } else if (parsed.type === 'delta' && typeof parsed.delta === 'string') {
          handlers.onDelta?.(parsed.delta)
        } else if (parsed.type === 'done') {
          handlers.onDone?.(readChatResponse(parsed))
        } else if (parsed.type === 'error') {
          throw new Error(typeof parsed.message === 'string' ? parsed.message : '问答生成失败。')
        }
      }
    }
    // Backend answers as one JSON document (no SSE framing yet); parse the
    // remaining body as a plain response so this mode never hangs silently.
    if (!sawEvent && buffer.trim()) {
      try {
        handlers.onDone?.(readChatResponse(JSON.parse(buffer.trim()) as unknown))
      } catch {
        // fall through; ChatPanel surfaces the missing response as an error
      }
    }
  },
}

function chatMode(): MeetingChatMode {
  const value = import.meta.env.VITE_MEETING_CHAT_MODE
  return isChatTransportMode(value) ? value : 'sse'
}

/**
 * Pick the chat transport. Defaults to the real backend SSE endpoint; set
 * VITE_MEETING_CHAT_MODE=json for the compatibility path or mock for the
 * offline demo.
 */
export function createChatTransport(): MeetingChatTransport {
  return chatMode() === 'json' ? jsonChatTransport : chatMode() === 'mock' ? mockChatTransport : sseChatTransport
}

export type { ChatHandlers, MeetingChatRequest, MeetingChatResponse, MeetingChatTransport }

export async function exportMeetingMinutes(
  context: MeetingAnalysisContext,
  modules: AnalysisModule[],
  messages: Array<{ role: string; content: string }>,
): Promise<void> {
  const lines: string[] = [
    `# ${context.meeting.title} · AI 纪要分析`,
    '',
    `- 会议日期：${context.meeting.starts_at ? new Date(context.meeting.starts_at).toLocaleDateString('zh-CN') : '未提供'}`,
    `- 会议时间：${context.meeting.starts_at ? new Date(context.meeting.starts_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '未提供'}`,
    `- 参会人数：${attendeeCount(context.meeting) || 0}`,
    `- 分析状态：${context.meeting.analysis_status}`,
    '',
  ]
  for (const module of modules) {
    lines.push(`## ${module.title}`)
    if (module.description) lines.push(`> ${module.description}`)
    lines.push('')
    if (module.content) lines.push(module.content, '')
    if (module.items?.length) lines.push(...module.items.map((item) => `- ${item}`), '')
    if (module.references.length) {
      lines.push('**参考来源**', '')
      for (const source of module.references) {
        lines.push(`- [${source.index}] ${source.title}：${source.snippet}`)
      }
      lines.push('')
    }
  }
  if (messages.length) {
    lines.push('## 智能问答记录', '')
    for (const message of messages) {
      lines.push(`**${message.role === 'user' ? '提问' : '回答'}**`, message.content, '')
    }
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  const safeName = context.meeting.title.replace(/[\\/:*?"<>|\s]+/g, '-').slice(0, 60) || 'meeting'
  anchor.href = url
  anchor.download = `${safeName}-AI纪要分析-${new Date().toISOString().slice(0, 10)}.md`
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}
