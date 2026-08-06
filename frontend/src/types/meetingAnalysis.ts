import type { Meeting } from '@/types/meeting'
import type { MeetingVerificationSnapshot } from '@/types/meetingVerification'

export type RagSourceType =
  | 'transcript'
  | 'meeting_summary'
  | 'historical_meeting'
  | 'knowledge_base'
  | 'cutoff_question'
  | 'open_question'

/**
 * A verifiable retrieval source attached to an analysis module or a chat answer.
 * Only the fields relevant to the source type are populated; the UI renders
 * fields conditionally.
 */
export interface RagSource {
  id: string
  index: number
  type: RagSourceType
  title: string
  snippet: string
  documentName?: string
  knowledgeBaseName?: string
  speakerName?: string
  timestamp?: string
  pageNumber?: number
  chunkId?: string
  /** Transcript block id used to scroll to the exact utterance. */
  blockId?: string
  /** Analysis module id used to locate a module on the left column. */
  moduleId?: string
  meetingId?: string
  documentId?: string
  knowledgeBaseId?: string
  questionId?: string
  /** Full original content shown in the source drawer. */
  content?: string
  [key: string]: unknown
}

export type AnalysisModuleState = 'loading' | 'ready' | 'empty' | 'error'

export interface AnalysisModule {
  id: string
  title: string
  description?: string
  state: AnalysisModuleState
  /** Markdown-rendered body when the module has prose content. */
  content?: string
  /** Plain list items (e.g. questions, attendees, agenda items). */
  items?: string[]
  references: RagSource[]
  error?: string
  /** Category used to decide the module icon/theme. */
  category: 'meeting' | 'transcript' | 'questions' | 'knowledge' | 'ai'
}

export interface TranscriptSegment {
  id: string
  order: number
  speaker: string | null
  startMs: number | null
  endMs: number | null
  text: string
  documentId?: string
}

export interface AnalysisDocument {
  id: string
  knowledgeBaseId: string
  meetingId?: string | null
  filename: string
  sourceType: string
  status?: string
}

/** All real data the analysis page needs, loaded from existing endpoints. */
export interface MeetingAnalysisContext {
  meeting: Meeting
  verification: MeetingVerificationSnapshot
  knowledgeBaseId: string | null
  knowledgeBaseName: string | null
  documents: AnalysisDocument[]
  transcript: TranscriptSegment[]
  transcriptDocumentId: string | null
}

export type ChatScope = 'CURRENT_MEETING' | 'MEETING_AND_KB'
export type ChatStatus = 'COMPLETED' | 'INSUFFICIENT_CONTEXT' | 'FAILED'

export interface MeetingChatRequest {
  meetingId: string
  conversationId?: string
  question: string
  scope: ChatScope
}

export interface MeetingChatResponse {
  conversationId: string
  messageId: string
  answer: string
  status: ChatStatus
  sources: RagSource[]
  suggestedQuestions?: string[]
}

export type RagStage =
  | 'IDLE'
  | 'RETRIEVING_MEETING'
  | 'RETRIEVING_KB'
  | 'ORGANIZING'
  | 'STREAMING'
  | 'DONE'

export type ChatMessageRole = 'user' | 'assistant'
export type ChatMessageStatus =
  | 'sending'
  | 'streaming'
  | 'complete'
  | 'insufficient'
  | 'failed'
  | 'stopped'
  | 'regenerating'

export interface ChatMessage {
  id: string
  role: ChatMessageRole
  content: string
  status: ChatMessageStatus
  stage?: RagStage
  conversationId?: string
  messageId?: string
  sources: RagSource[]
  error?: string
  createdAt: string
}

export interface ChatHandlers {
  onStage?: (stage: RagStage) => void
  onDelta?: (delta: string) => void
  onDone?: (response: MeetingChatResponse) => void
  onError?: (error: unknown) => void
}

export interface MeetingChatTransport {
  readonly mode: 'sse' | 'json' | 'mock'
  chat(
    payload: MeetingChatRequest,
    context: MeetingAnalysisContext | null,
    handlers: ChatHandlers,
    signal?: AbortSignal,
  ): Promise<void>
}

export type MeetingChatMode = MeetingChatTransport['mode']

export const isChatTransportMode = (value: string | undefined): value is MeetingChatMode =>
  value === 'sse' || value === 'json' || value === 'mock'
