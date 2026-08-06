import { http } from '@/api/client'
import { normalizeQuestionEvidence, normalizeQuestionGenerationTask, normalizeVerification } from '@/types/meetingVerification'
export { normalizeVerification } from '@/types/meetingVerification'
import { toApiError } from '@/utils/errors'
import type {
  AnalysisSelectionPayload,
  AnalysisTask,
  AnalysisSubmissionResponse,
  MeetingVerificationSnapshot,
  QuestionCandidate,
  QuestionCandidatePage,
  VerificationActionPayload,
  VerificationQuestion,
  VerificationQuestionCreatePayload,
  VerificationQuestionUpdatePayload,
  QuestionEvidence,
  QuestionGenerationTask,
} from '@/types/meetingVerification'

export function normalizeCandidate(raw: unknown): QuestionCandidate | null {
  if (!raw || typeof raw !== 'object') return null
  const item = raw as Record<string, unknown>
  const id = String(item.id ?? item.question_id ?? '')
  const content = typeof item.content === 'string' ? item.content.trim() : ''
  if (!id || !content) return null
  const type = item.question_type === 'open_ended' || item.question_type === 'OPEN_ENDED' ? 'open_ended' : 'cut_point'
  return {
    id,
    question_type: type,
    rank: typeof item.rank === 'number' ? item.rank : (typeof item.candidate_rank === 'number' ? item.candidate_rank : null),
    content,
    topic: typeof item.topic === 'string' ? item.topic : null,
    rationale: typeof item.rationale === 'string' ? item.rationale : null,
    expected_answer_type: typeof item.expected_answer_type === 'string' ? item.expected_answer_type : null,
    support_score: typeof item.support_score === 'number' ? item.support_score : null,
    evidence_count: typeof item.evidence_count === 'number' ? item.evidence_count : 0,
    selected: item.selected === true || item.analysis_selected === true,
    source: typeof item.source === 'string' ? item.source : 'ai',
    version: typeof item.version === 'number' ? item.version : 1,
  }
}

export function normalizeAnalysisTask(raw: unknown): AnalysisTask | null {
  if (!raw || typeof raw !== 'object') return null
  const item = raw as Record<string, unknown>
  const taskId = String(item.task_id ?? item.taskId ?? '')
  if (!taskId) return null
  return {
    task_id: taskId,
    status: String(item.status ?? 'QUEUED'),
    current_stage: String(item.current_stage ?? item.currentStage ?? 'queued'),
    progress: Number(item.progress ?? 0) || 0,
    message: typeof item.message === 'string' ? item.message : null,
    error_message: typeof item.error_message === 'string' ? item.error_message : null,
    retry_count: Number(item.retry_count ?? item.retryCount ?? 0) || 0,
  }
}

const endpoint = (meetingId: string) => `/api/v1/meetings/${meetingId}/verification`
const questionsEndpoint = (meetingId: string) => `/api/v1/meetings/${meetingId}/questions`
const questionGenerationEndpoint = (meetingId: string) => `/api/v1/meetings/${meetingId}/question-generation`

export const meetingVerificationApi = {
  async get(meetingId: string): Promise<MeetingVerificationSnapshot> {
    const { data } = await http.get(endpoint(meetingId))
    return normalizeVerification(data)
  },
  async createQuestion(meetingId: string, payload: VerificationQuestionCreatePayload): Promise<VerificationQuestion> {
    const { data } = await http.post(questionsEndpoint(meetingId), payload)
    return data
  },
  async updateQuestion(meetingId: string, questionId: string, payload: VerificationQuestionUpdatePayload): Promise<VerificationQuestion> {
    const { data } = await http.patch(`${questionsEndpoint(meetingId)}/${questionId}`, payload)
    return data
  },
  async removeQuestion(meetingId: string, questionId: string, expected_version: number): Promise<void> {
    await http.delete(`${questionsEndpoint(meetingId)}/${questionId}`, { params: { expected_version } })
  },
  async confirm(meetingId: string, payload: VerificationActionPayload): Promise<MeetingVerificationSnapshot> {
    const { data } = await http.post(`${endpoint(meetingId)}/confirm`, payload)
    const normalized = normalizeVerification(data?.verification ?? data)
    return normalized.meeting?.id ? normalized : { ...normalized, meeting_id: normalized.meeting_id ?? meetingId }
  },
  async submitAnalysis(meetingId: string, payload: AnalysisSelectionPayload): Promise<AnalysisSubmissionResponse> {
    const { data } = await http.post(`/api/v1/meetings/${meetingId}/analysis-submissions`, payload)
    return {
      verification: normalizeVerification(data?.verification ?? data),
      message: typeof data?.message === 'string' ? data.message : 'AI 分析已提交。',
      task_id: typeof data?.task_id === 'string' ? data.task_id : null,
      task_status: typeof data?.task_status === 'string' ? data.task_status : null,
    }
  },
  async getQuestionCandidates(meetingId: string, questionType: 'cut_point' | 'open_ended', offset = 0, limit = 5): Promise<QuestionCandidatePage> {
    const { data } = await http.get<unknown>(`/api/v1/meetings/${meetingId}/question-candidates`, {
      params: { question_type: questionType, offset, limit },
    })
    const value = (data && typeof data === 'object' ? data : {}) as Record<string, unknown>
    return {
      items: (Array.isArray(value.items) ? value.items : []).map(normalizeCandidate).filter((item): item is QuestionCandidate => Boolean(item)),
      total: Number(value.total ?? 0) || 0,
      offset: Number(value.offset ?? offset) || 0,
      limit: Number(value.limit ?? limit) || limit,
    }
  },
  async saveAnalysisSelection(meetingId: string, payload: AnalysisSelectionPayload): Promise<MeetingVerificationSnapshot> {
    const { data } = await http.put(`/api/v1/meetings/${meetingId}/analysis-selection`, payload)
    return normalizeVerification(data)
  },
  async getAnalysisTask(meetingId: string): Promise<AnalysisTask | null> {
    try {
      const { data } = await http.get(`/api/v1/meetings/${meetingId}/analysis-task`)
      return normalizeAnalysisTask(data)
    } catch (error) {
      if (toApiError(error).status === 404) return null
      throw error
    }
  },
  async reanalyzeAnalysis(meetingId: string, payload: AnalysisSelectionPayload): Promise<AnalysisTask> {
    const { data } = await http.post(`/api/v1/meetings/${meetingId}/analysis/reanalyze`, payload)
    const task = normalizeAnalysisTask(data)
    if (!task) throw new Error('重新分析任务格式不正确。')
    return task
  },
  async getQuestionGeneration(meetingId: string): Promise<QuestionGenerationTask | null> {
    try {
      const { data } = await http.get(questionGenerationEndpoint(meetingId))
      return normalizeQuestionGenerationTask(data)
    } catch (error) {
      // Older meetings were created before question-generation tasks existed.
      if (toApiError(error).status === 404) return null
      throw error
    }
  },
  async retryQuestionGeneration(meetingId: string): Promise<QuestionGenerationTask> {
    const { data } = await http.post(`${questionGenerationEndpoint(meetingId)}/retry`)
    return normalizeQuestionGenerationTask(data)
  },
  async getQuestionEvidences(meetingId: string, questionId: string): Promise<QuestionEvidence[]> {
    try {
      const { data } = await http.get(`${questionsEndpoint(meetingId)}/${questionId}/evidences`)
      const values = Array.isArray(data) ? data : Array.isArray(data?.evidences) ? data.evidences : Array.isArray(data?.items) ? data.items : []
      return values.map(normalizeQuestionEvidence)
    } catch (error) {
      if (toApiError(error).status === 404) return []
      throw error
    }
  },
}
