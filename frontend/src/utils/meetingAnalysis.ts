import DOMPurify from 'dompurify'
import { Marked } from 'marked'
import type {
  AnalysisModuleState,
  AnalysisModule,
  ChatMessageStatus,
  ChatScope,
  RagSource,
  RagSourceType,
  RagStage,
} from '@/types/meetingAnalysis'

export const ragSourceTypeLabels: Record<RagSourceType, string> = {
  transcript: '会议转写',
  meeting_summary: '会议纪要',
  historical_meeting: '历史会议',
  knowledge_base: '知识库文档',
  cutoff_question: '切点问题',
  open_question: '开放性问题',
}

export const ragSourceTypeTone: Record<RagSourceType, string> = {
  transcript: 'transcript',
  meeting_summary: 'summary',
  historical_meeting: 'history',
  knowledge_base: 'kb',
  cutoff_question: 'cutoff',
  open_question: 'open',
}

export const analysisModuleStateLabels: Record<AnalysisModuleState, string> = {
  loading: '正在生成',
  ready: '已生成',
  empty: '暂无内容',
  error: '生成失败',
}

export const chatScopeLabels: Record<ChatScope, string> = {
  CURRENT_MEETING: '当前会议',
  MEETING_AND_KB: '当前会议 + 知识库',
}

export const chatMessageStatusLabels: Record<ChatMessageStatus, string> = {
  sending: '正在发送',
  streaming: '正在生成',
  complete: '已完成',
  insufficient: '资料不足',
  failed: '生成失败',
  stopped: '已停止',
  regenerating: '正在重新生成',
}

export const ragStageLabels: Record<RagStage, string> = {
  IDLE: '准备中',
  RETRIEVING_MEETING: '正在检索会议内容',
  RETRIEVING_KB: '正在检索知识库',
  ORGANIZING: '正在组织回答',
  STREAMING: '正在输出答案',
  DONE: '完成',
}

export const recommendedQuestions: string[] = [
  '本次会议形成了哪些核心共识？',
  '各位参会者的主要观点有什么区别？',
  '哪些问题尚未得到明确结论？',
  '会议内容与知识库中的历史结论是否一致？',
]

export const insufficientContextNotice =
  '根据当前会议记录和已连接的知识库，暂时无法确认该问题。你可以调整问题，或者扩大检索范围。'

export function formatMilliseconds(ms: number | null | undefined): string | null {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return null
  const totalSeconds = Math.floor(ms / 1000)
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  const pad = (value: number) => String(value).padStart(2, '0')
  return hours > 0 ? `${pad(hours)}:${pad(minutes)}:${pad(seconds)}` : `${pad(minutes)}:${pad(seconds)}`
}

const marked = new Marked({
  gfm: true,
  breaks: false,
})

/**
 * Render untrusted LLM markdown to safe HTML. All raw HTML is removed by
 * DOMPurify, and citation markers like [1] become in-page anchors so the
 * "参考来源" section can be reached from the answer body.
 */
export function renderMarkdown(content: string, sources: RagSource[] = []): string {
  const withCitations = sources.length
    ? content.replace(/\[(\d+)\]/g, (match, index: string) => {
        const exists = sources.some((source) => String(source.index) === index)
        return exists ? `<a class="citation-anchor" href="#source-${index}">[${index}]</a>` : match
      })
    : content
  const rawHtml = marked.parse(withCitations, { async: false }) as string
  return DOMPurify.sanitize(rawHtml, {
    ADD_ATTR: ['target'],
    ADD_TAGS: ['a'],
  })
}

export function markdownToPlainText(content: string): string {
  return content
    .replace(/```[\s\S]*?```/g, (block) => block.replace(/^```.*$/gm, '').trim())
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[([^\]]*)\]\([^)]+\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/^>\s?/gm, '')
    .replace(/^[-*+]\s+/gm, '• ')
    .replace(/^\d+\.\s+/gm, '')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/\*([^*]+)\*/g, '$1')
    .replace(/\|/g, '')
    .trim()
}

export async function copyText(value: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(value)
    return true
  } catch {
    try {
      const textarea = document.createElement('textarea')
      textarea.value = value
      textarea.style.position = 'fixed'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      textarea.select()
      const copied = document.execCommand('copy')
      textarea.remove()
      return copied
    } catch {
      return false
    }
  }
}

export function sourceSubtitle(source: RagSource): string {
  const parts: string[] = []
  if (source.speakerName) parts.push(source.speakerName)
  if (source.timestamp) parts.push(source.timestamp)
  if (source.pageNumber != null) parts.push(`第 ${source.pageNumber} 页`)
  if (source.documentName) parts.push(source.documentName)
  if (source.knowledgeBaseName) parts.push(source.knowledgeBaseName)
  if (source.chunkId) parts.push(`Chunk ${source.chunkId}`)
  return parts.join(' · ')
}

const LEGACY_MODULE_ORDER = [
  'summary',
  'agenda',
  'viewpoints',
  'consensus',
  'divergence',
  'cutoff-questions',
  'open-questions',
  'actions',
  'ai-conclusion',
]

/**
 * Compose the single "AI 通读纪要" document shown on the analysis page.
 *
 * New-format results already contain one `minutes` module and are passed
 * through unchanged. Legacy multi-module results are merged into one markdown
 * document in a fixed section order (transcript is excluded), with references
 * unified into a single numbered list and in-body [n] markers renumbered.
 */
export function composeMinutesDocument(modules: AnalysisModule[]): AnalysisModule | null {
  const candidates = modules.filter((module) => module.id !== 'transcript')
  if (!candidates.length) return null
  if (candidates.length === 1 && candidates[0].id === 'minutes') return candidates[0]

  const ordered = [
    ...LEGACY_MODULE_ORDER.map((id) => candidates.find((module) => module.id === id)).filter(
      (module): module is AnalysisModule => Boolean(module),
    ),
    ...candidates.filter((module) => !LEGACY_MODULE_ORDER.includes(module.id)),
  ]
  const sections: string[] = []
  const mergedSources: RagSource[] = []
  const sourceIndexByKey = new Map<string, number>()
  let hasBody = false

  for (const module of ordered) {
    if (module.state !== 'ready') continue
    let body = module.content ?? ''
    if (module.items?.length) {
      const itemsMarkdown = module.items.map((item) => `- ${item}`).join('\n')
      body = body ? `${body}\n\n${itemsMarkdown}` : itemsMarkdown
    }
    if (!body.trim()) continue
    hasBody = true

    const indexMap = new Map<number, number>()
    for (const source of module.references) {
      let newIndex = sourceIndexByKey.get(source.id)
      if (newIndex == null) {
        newIndex = mergedSources.length + 1
        sourceIndexByKey.set(source.id, newIndex)
        mergedSources.push({ ...source, index: newIndex })
      }
      indexMap.set(source.index, newIndex)
    }
    const rewritten = body.replace(/\[(\d+)\]/g, (match, raw: string) => {
      const old = Number(raw)
      const mapped = indexMap.get(old)
      return mapped != null ? `[${mapped}]` : match
    })
    const description = module.description ? `> ${module.description}\n\n` : ''
    sections.push(`## ${module.title}\n\n${description}${rewritten.trim()}`)
  }

  if (!hasBody) {
    return {
      id: 'minutes',
      title: 'AI 通读纪要',
      category: 'ai',
      state: 'empty',
      references: mergedSources,
    }
  }
  return {
    id: 'minutes',
    title: 'AI 通读纪要',
    category: 'ai',
    state: 'ready',
    content: sections.join('\n\n---\n\n'),
    references: mergedSources,
  }
}

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

export function generateMessageId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 9)}`
}

interface AnalysisSourceRaw {
  index: number
  type: RagSource['type']
  title: string
  snippet?: string
  content?: string
  speaker_name?: string | null
  timestamp?: string | null
  page_number?: number | null
  chunk_id?: string | null
  document_id?: string | null
  document_title?: string | null
  knowledge_base_name?: string | null
  question_id?: string | null
  block_id?: string | null
}

interface AnalysisModuleRaw {
  id: string
  title: string
  description?: string | null
  content?: string | null
  items?: string[]
  citations?: number[]
  category?: string
}

export function normalizeAnalysisSources(raw: unknown): RagSource[] {
  if (!Array.isArray(raw)) return []
  return raw
    .map((item, index) => {
      if (!item || typeof item !== 'object') return null
      const source = item as AnalysisSourceRaw
      if (!source.title) return null
      return {
        id: `src-${source.chunk_id ?? source.question_id ?? index + 1}`,
        index: Number(source.index) || index + 1,
        type: source.type,
        title: source.title,
        snippet: source.snippet ?? '',
        content: source.content ?? undefined,
        speakerName: source.speaker_name ?? undefined,
        timestamp: source.timestamp ?? undefined,
        pageNumber: source.page_number ?? undefined,
        chunkId: source.chunk_id ?? undefined,
        documentId: source.document_id ?? undefined,
        documentName: source.document_title ?? undefined,
        knowledgeBaseName: source.knowledge_base_name ?? undefined,
        questionId: source.question_id ?? undefined,
        blockId: source.block_id ?? undefined,
      }
    })
    .filter((source): source is RagSource => Boolean(source))
}

export function normalizeAnalysisModules(
  modulesRaw: unknown,
  sources: RagSource[],
): AnalysisModule[] {
  if (!Array.isArray(modulesRaw)) return []
  return modulesRaw
    .map((item) => {
      if (!item || typeof item !== 'object') return null
      const module = item as AnalysisModuleRaw
      if (!module.id || !module.title) return null
      const citations = Array.isArray(module.citations)
        ? module.citations.filter((value): value is number => typeof value === 'number')
        : []
      const content = module.content ?? undefined
      const items = Array.isArray(module.items) ? module.items.map(String) : []
      const hasBody = Boolean((content ?? '').trim() || items.length)
      const category = module.category === 'transcript' || module.category === 'questions' || module.category === 'knowledge' || module.category === 'ai'
        ? module.category
        : 'meeting'
      return {
        id: module.id,
        title: module.title,
        description: module.description ?? undefined,
        state: hasBody ? 'ready' : 'empty',
        content,
        items: items.length ? items : undefined,
        references: sources.filter((source) => citations.includes(source.index)),
        category,
      }
    })
    .filter((module): module is AnalysisModule => Boolean(module))
}

export function selectionTypeCounts(
  questions: Array<{ id: string; question_type: string }>,
  selectedIds: Set<string>,
): { cutPoint: number; openEnded: number } {
  let cutPoint = 0
  let openEnded = 0
  for (const question of questions) {
    if (!selectedIds.has(question.id)) continue
    if (question.question_type === 'open_ended') openEnded += 1
    else cutPoint += 1
  }
  return { cutPoint, openEnded }
}
