import type { Role } from '@/types/kb'
import type { Meeting, VerificationStatus } from '@/types/meeting'
import type { MeetingVerificationSnapshot, QuestionGenerationStatus, VerificationQuestion } from '@/types/meetingVerification'

export const verificationStatusLabels: Record<VerificationStatus, string> = {
  pending: '待核验',
  in_progress: '核验中',
  confirmed: '已确认',
}
export const verificationStatusLabel = (status?: VerificationStatus | null) => status ? verificationStatusLabels[status] : '待核验'

export const verificationStatusType = (status: VerificationStatus) => {
  if (status === 'confirmed') return 'success'
  if (status === 'in_progress') return 'warning'
  return 'info'
}

export const canEditVerification = (role?: Role | string | null) => role === 'owner' || role === 'admin' || role === 'editor'
export const canMutateVerification = canEditVerification

export function uniqueNames(values: Array<string | string[] | null | undefined>): string[] {
  const result: string[] = []
  const seen = new Set<string>()
  values.flatMap((value) => Array.isArray(value) ? value : typeof value === 'string' ? value.split(/[;,，、\n]/) : []).forEach((item) => {
    const name = item.trim()
    if (name && !seen.has(name)) { seen.add(name); result.push(name) }
  })
  return result
}

export const attendeeCount = (meeting: Meeting) => uniqueNames([meeting.meeting_info?.advisor_names, meeting.meeting_info?.internal_attendees]).length

export const questionGroups = (snapshot: Pick<MeetingVerificationSnapshot, 'cut_point_questions' | 'open_ended_questions'>) => [
  { key: 'cut_point' as const, label: '切点问题', questions: snapshot.cut_point_questions },
  { key: 'open_ended' as const, label: '开放性问题', questions: snapshot.open_ended_questions },
]

export const sortQuestionsBySupport = (questions: VerificationQuestion[]) => [...questions].sort((a, b) => {
  const aScore = typeof a.support_score === 'number' ? a.support_score : null
  const bScore = typeof b.support_score === 'number' ? b.support_score : null
  if (aScore === null && bScore === null) return (a.display_order ?? 0) - (b.display_order ?? 0)
  if (aScore === null) return 1
  if (bScore === null) return -1
  return aScore - bScore || (a.display_order ?? 0) - (b.display_order ?? 0)
})

export const hasQuestions = (snapshot: Pick<MeetingVerificationSnapshot, 'cut_point_questions' | 'open_ended_questions'>) =>
  snapshot.cut_point_questions.length > 0 && snapshot.open_ended_questions.length > 0

export const missingConditionLabels = (conditions: string[]) => conditions.map((condition) => {
  const map: Record<string, string> = {
    cut_point_questions: '至少需要 1 条切点问题',
    open_ended_questions: '至少需要 1 条开放性问题',
    meeting_info: '会议基本信息未完成',
    confirmation: '请先确认会议核验结果',
  }
  return map[condition] ?? condition.replaceAll('切入点', '切点').replaceAll('开放式', '开放性')
})

export const isDirty = (draft: string, saved: string) => draft !== saved
export const isBusy = (loading: boolean, saving: boolean) => loading || saving

export const questionText = (question: Pick<VerificationQuestion, 'content'>) => question.content.trim()

export const questionGenerationStatusLabels: Record<QuestionGenerationStatus, string> = {
  QUEUED: '正在排队',
  RUNNING: '正在生成问题',
  RETRYING: '正在重试生成',
  PENDING_REVIEW: '问题生成完成，请核验',
  SUCCEEDED: '问题生成完成',
  FAILED: '问题生成失败',
  CANCELLED: '问题生成已取消',
}

export const questionGenerationStageLabels: Record<string, string> = {
  LOADING_MEETING: '正在加载会议资料',
  PLANNING_RETRIEVAL: '正在规划检索内容',
  RETRIEVING_KNOWLEDGE: '正在检索知识库',
  RERANKING_EVIDENCE: '正在整理证据',
  GENERATING_CUTPOINTS: '正在生成切点问题',
  GENERATING_OPEN_QUESTIONS: '正在生成开放性问题',
  VALIDATING_QUESTIONS: '正在校验问题',
  SAVING_RESULTS: '正在保存问题',
  PENDING_REVIEW: '等待人工核验',
}

export const questionGenerationStageLabel = (stage?: string | null) => stage ? questionGenerationStageLabels[stage] ?? stage : ''
export const isQuestionGenerationActive = (status?: string | null) => ['QUEUED', 'RUNNING', 'RETRYING'].includes(String(status).toUpperCase())
export const isQuestionGenerationTerminal = (status?: string | null) => ['PENDING_REVIEW', 'SUCCEEDED', 'FAILED', 'CANCELLED'].includes(String(status).toUpperCase())
