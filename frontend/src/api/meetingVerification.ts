import { http } from '@/api/client'
import { normalizeQuestionEvidence, normalizeQuestionGenerationTask, normalizeVerification } from '@/types/meetingVerification'
export { normalizeVerification } from '@/types/meetingVerification'
import { toApiError } from '@/utils/errors'
import type {
  AnalysisSubmissionResponse,
  MeetingVerificationSnapshot,
  VerificationActionPayload,
  VerificationQuestion,
  VerificationQuestionCreatePayload,
  VerificationQuestionUpdatePayload,
  QuestionEvidence,
  QuestionGenerationTask,
} from '@/types/meetingVerification'

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
  async submitAnalysis(meetingId: string, payload: VerificationActionPayload): Promise<AnalysisSubmissionResponse> {
    const { data } = await http.post(`/api/v1/meetings/${meetingId}/analysis-submissions`, payload)
    return { verification: normalizeVerification(data?.verification ?? data), message: typeof data?.message === 'string' ? data.message : '会议核验已完成，AI 分析功能将在下一阶段接入。' }
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
