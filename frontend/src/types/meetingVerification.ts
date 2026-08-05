import type { Meeting, MeetingInfo, VerificationStatus } from '@/types/meeting'

export type VerificationQuestionType = 'cut_point' | 'open_ended'

export type QuestionGenerationStatus = 'QUEUED' | 'RUNNING' | 'RETRYING' | 'PENDING_REVIEW' | 'SUCCEEDED' | 'FAILED' | 'CANCELLED'

export interface QuestionEvidence {
  document_title: string | null
  section_title: string | null
  quote: string
  evidence_summary: string
  chunk_text: string
  vector_score: number | null
  keyword_score: number | null
  rerank_score: number | null
}

export interface VerificationQuestion {
  id: string
  meeting_id?: string
  question_type: VerificationQuestionType
  content: string
  source?: string | null
  confidence?: number | null
  topic?: string | null
  rationale?: string | null
  origin?: 'AI_GENERATED' | 'USER_CREATED' | string | null
  review_status?: 'AI_DRAFT' | 'USER_EDITED' | 'CONFIRMED' | 'REJECTED' | string | null
  support_score?: number | null
  evidence_count?: number
  expected_answer_type?: string | null
  evidences?: QuestionEvidence[]
  display_order?: number
  version: number
  created_at?: string | null
  updated_at?: string | null
}

export interface VerificationEligibility {
  can_confirm: boolean
  can_submit_analysis: boolean
  missing_conditions: string[]
}

export interface MeetingVerificationSnapshot {
  meeting: Meeting
  cut_point_questions: VerificationQuestion[]
  open_ended_questions: VerificationQuestion[]
  verification_version: number
  eligibility: VerificationEligibility
  meeting_id?: string
  ai_task_id?: string | null
  question_generation_status?: QuestionGenerationStatus | string | null
}

export interface QuestionGenerationTask {
  task_id: string | null
  status: QuestionGenerationStatus | string
  current_stage: string | null
  progress: number
  message: string | null
  cutpoint_count: number
  open_question_count: number
  error_message: string | null
}

export interface VerificationQuestionCreatePayload {
  question_type: VerificationQuestionType
  content: string
}

export interface VerificationQuestionUpdatePayload {
  content: string
  expected_version: number
}

export interface VerificationActionPayload {
  expected_version: number
}

export interface AnalysisSubmissionResponse {
  verification: MeetingVerificationSnapshot
  message: string
}

export interface MeetingVerificationListItem extends Meeting {
  verification_status: VerificationStatus
}

export interface NormalizedMeetingInfo {
  meeting_purpose: string | null
  discussion_topics: string | null
  advisor_selection_criteria: string | null
  advisor_names: string[]
  internal_attendees: string[]
  recorder: string | null
}

export function normalizeMeetingInfo(info?: MeetingInfo | null): NormalizedMeetingInfo {
  const toNames = (value: unknown): string[] => {
    if (Array.isArray(value)) return value.map(String).map((item) => item.trim()).filter(Boolean)
    if (typeof value !== 'string') return []
    return value.split(/[;,，、\n]/).map((item) => item.trim()).filter(Boolean)
  }
  return {
    meeting_purpose: typeof info?.meeting_purpose === 'string' ? info.meeting_purpose : null,
    discussion_topics: typeof info?.discussion_topics === 'string' ? info.discussion_topics : null,
    advisor_selection_criteria: typeof info?.advisor_selection_criteria === 'string' ? info.advisor_selection_criteria : null,
    advisor_names: toNames(info?.advisor_names),
    internal_attendees: toNames(info?.internal_attendees),
    recorder: typeof info?.recorder === 'string' ? info.recorder : null,
  }
}

const question = (value: unknown, type: VerificationQuestionType, index: number): VerificationQuestion | null => {
  if (!value || typeof value !== 'object') return null
  const item = value as Record<string, unknown>
  const content = typeof item.content === 'string' ? item.content.trim() : ''
  const id = String(item.id ?? item.question_id ?? `${type}-${index}`)
  if (!content) return null
  const sourceValue = typeof item.source === 'string' ? item.source : null
  const originValue = typeof item.origin === 'string' ? item.origin : sourceValue === 'AI_GENERATED' || sourceValue === 'USER_CREATED' ? sourceValue : null
  const origin = originValue
  const reviewStatus = typeof item.review_status === 'string' ? item.review_status : null
  const evidences = Array.isArray(item.evidences) ? item.evidences as QuestionEvidence[] : undefined
  return {
    id,
    meeting_id: typeof item.meeting_id === 'string' ? item.meeting_id : undefined,
    question_type: (item.question_type === 'open_ended' || item.question_type === 'OPEN_ENDED' || item.question_type === 'OPEN' ? 'open_ended' : type),
    content,
    source: sourceValue && !['AI_GENERATED', 'USER_CREATED'].includes(sourceValue) ? sourceValue : origin,
    confidence: typeof item.confidence === 'number' ? item.confidence : null,
    topic: typeof item.topic === 'string' ? item.topic : null,
    rationale: typeof item.rationale === 'string' ? item.rationale : null,
    origin,
    review_status: reviewStatus,
    support_score: typeof item.support_score === 'number' ? item.support_score : null,
    evidence_count: typeof item.evidence_count === 'number' ? item.evidence_count : (evidences?.length ?? 0),
    expected_answer_type: typeof item.expected_answer_type === 'string' ? item.expected_answer_type : null,
    evidences,
    display_order: typeof item.display_order === 'number' ? item.display_order : index,
    version: typeof item.version === 'number' ? item.version : 1,
    created_at: typeof item.created_at === 'string' ? item.created_at : null,
    updated_at: typeof item.updated_at === 'string' ? item.updated_at : null,
  }
}

export function normalizeVerification(raw: unknown): MeetingVerificationSnapshot {
  const source = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
  const meeting = (source.meeting ?? {}) as Meeting
  const normalizeQuestions = (value: unknown, type: VerificationQuestionType) =>
    (Array.isArray(value) ? value : []).map((item, index) => question(item, type, index)).filter((item): item is VerificationQuestion => Boolean(item))
  const eligibility = (source.eligibility && typeof source.eligibility === 'object' ? source.eligibility : {}) as Record<string, unknown>
  return {
    meeting,
    cut_point_questions: normalizeQuestions(source.cut_point_questions ?? source.cutpoint_questions ?? source.cut_point, 'cut_point'),
    open_ended_questions: normalizeQuestions(source.open_ended_questions ?? source.open_questions ?? source.open, 'open_ended'),
    verification_version: typeof source.verification_version === 'number' ? source.verification_version : (meeting.verification_version ?? 1),
    eligibility: {
      can_confirm: eligibility.can_confirm === true,
      can_submit_analysis: eligibility.can_submit_analysis === true,
      missing_conditions: Array.isArray(eligibility.missing_conditions) ? eligibility.missing_conditions.map(String) : [],
    },
    meeting_id: typeof source.meeting_id === 'string' ? source.meeting_id : (typeof (meeting as Meeting).id === 'string' ? (meeting as Meeting).id : undefined),
    ai_task_id: typeof source.ai_task_id === 'string' ? source.ai_task_id : (typeof source.task_id === 'string' ? source.task_id : null),
    question_generation_status: typeof source.question_generation_status === 'string' ? source.question_generation_status.toUpperCase() : null,
  }
}

const asRecord = (value: unknown): Record<string, unknown> => value && typeof value === 'object' ? value as Record<string, unknown> : {}

export function normalizeQuestionGenerationTask(raw: unknown): QuestionGenerationTask {
  const source = asRecord(raw)
  const status = String(source.status ?? source.task_status ?? 'QUEUED').toUpperCase()
  const progressValue = Number(source.progress ?? source.progress_percent ?? 0)
  return {
    task_id: typeof source.task_id === 'string' ? source.task_id : (typeof source.taskId === 'string' ? source.taskId : null),
    status,
    current_stage: typeof source.current_stage === 'string' ? source.current_stage : (typeof source.currentStage === 'string' ? source.currentStage : null),
    progress: Number.isFinite(progressValue) ? Math.max(0, Math.min(100, progressValue)) : 0,
    message: typeof source.message === 'string' ? source.message : null,
    cutpoint_count: Number(source.cutpoint_count ?? source.cutPointCount ?? 0) || 0,
    open_question_count: Number(source.open_question_count ?? source.openQuestionCount ?? 0) || 0,
    error_message: typeof source.error_message === 'string' ? source.error_message : (typeof source.errorMessage === 'string' ? source.errorMessage : null),
  }
}

export function normalizeQuestionEvidence(raw: unknown): QuestionEvidence {
  const source = asRecord(raw)
  const score = (key: string, alias: string) => {
    const value = Number(source[key] ?? source[alias])
    return Number.isFinite(value) ? value : null
  }
  return {
    document_title: typeof source.document_title === 'string' ? source.document_title : (typeof source.documentTitle === 'string' ? source.documentTitle : null),
    section_title: typeof source.section_title === 'string' ? source.section_title : (typeof source.sectionTitle === 'string' ? source.sectionTitle : null),
    quote: typeof source.quote === 'string' ? source.quote : (typeof source.original_text === 'string' ? source.original_text : ''),
    evidence_summary: typeof source.evidence_summary === 'string' ? source.evidence_summary : (typeof source.summary === 'string' ? source.summary : ''),
    chunk_text: typeof source.chunk_text === 'string' ? source.chunk_text : (typeof source.chunk_content === 'string' ? source.chunk_content : (typeof source.chunk_summary === 'string' ? source.chunk_summary : (typeof source.text === 'string' ? source.text : ''))),
    vector_score: score('vector_score', 'vectorScore'),
    keyword_score: score('keyword_score', 'keywordScore'),
    rerank_score: score('rerank_score', 'rerankScore'),
  }
}
