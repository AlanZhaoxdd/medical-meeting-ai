import { describe, expect, it } from 'vitest'
import { normalizeQuestionEvidence, normalizeQuestionGenerationTask, normalizeVerification } from '@/types/meetingVerification'
import { attendeeCount, canEditVerification, isDirty, isQuestionGenerationActive, missingConditionLabels, questionGenerationStageLabel, questionGroups, sortQuestionsBySupport, verificationStatusLabels } from '@/utils/meetingVerification'

describe('meeting verification helpers', () => {
  it('maps verification statuses and roles', () => {
    expect(verificationStatusLabels).toMatchObject({ pending: '待核验', in_progress: '核验中', confirmed: '已确认' })
    expect(canEditVerification('owner')).toBe(true)
    expect(canEditVerification('admin')).toBe(true)
    expect(canEditVerification('editor')).toBe(true)
    expect(canEditVerification('reviewer')).toBe(false)
    expect(canEditVerification('viewer')).toBe(false)
  })

  it('renders eligibility missing conditions and groups questions', () => {
    expect(missingConditionLabels(['cut_point_questions', 'open_ended_questions'])).toEqual(['至少需要 1 条切点问题', '至少需要 1 条开放性问题'])
    const snapshot = normalizeVerification({ meeting: {}, cut_point_questions: [{ id: 'c1', content: '切点？', version: 2 }], open_ended_questions: [{ id: 'o1', content: '开放？', version: 1 }], verification_version: 4, eligibility: { can_confirm: true, can_submit_analysis: false, missing_conditions: [] } })
    expect(questionGroups(snapshot).map((group) => group.questions.length)).toEqual([1, 1])
  })

  it('sorts questions from low to high support and keeps unscored questions last', () => {
    const questions = [
      { id: 'high', content: '高', support_score: 0.95, version: 1 },
      { id: 'none', content: '无', support_score: null, version: 1 },
      { id: 'low', content: '低', support_score: 0.2, version: 1 },
    ]
    expect(sortQuestionsBySupport(questions as never).map((question) => question.id)).toEqual(['low', 'high', 'none'])
  })

  it('deduplicates advisors and internal attendees when counting participants', () => {
    expect(attendeeCount({ meeting_info: { advisor_names: '张三, 李四', internal_attendees: ['李四', '王五'] } } as never)).toBe(3)
  })

  it('tracks dirty and normalizes response defaults', () => {
    expect(isDirty('draft', 'saved')).toBe(true)
    expect(isDirty('same', 'same')).toBe(false)
    const snapshot = normalizeVerification({ meeting: { id: 'm1' }, cut_point_questions: [], open_ended_questions: [] })
    expect(snapshot.verification_version).toBe(1)
    expect(snapshot.eligibility.can_confirm).toBe(false)
    expect(snapshot.eligibility.can_submit_analysis).toBe(false)
  })

  it('normalizes generation task states and progress without exceeding bounds', () => {
    const task = normalizeQuestionGenerationTask({ taskId: 'task-1', status: 'running', currentStage: 'RETRIEVING_KNOWLEDGE', progress: 130, cutPointCount: 2, openQuestionCount: 1 })
    expect(task).toMatchObject({ task_id: 'task-1', status: 'RUNNING', progress: 100, cutpoint_count: 2, open_question_count: 1 })
    expect(isQuestionGenerationActive(task.status)).toBe(true)
    expect(questionGenerationStageLabel(task.current_stage)).toBe('正在检索知识库')
  })

  it('normalizes generated question metadata and evidence without exposing identifiers', () => {
    const snapshot = normalizeVerification({ meeting: { id: 'm1' }, cut_point_questions: [{ id: 'q1', content: '问题', version: 1, origin: 'AI_GENERATED', topic: '主题', support_score: 0.8, evidence_count: 2 }], open_ended_questions: [] })
    expect(snapshot.cut_point_questions[0]).toMatchObject({ origin: 'AI_GENERATED', topic: '主题', support_score: 0.8, evidence_count: 2 })
    expect(normalizeQuestionEvidence({ documentTitle: '指南', sectionTitle: '第 1 节', quote: '原文', summary: '摘要', chunk_text: '片段', rerankScore: 0.91 })).toMatchObject({ document_title: '指南', section_title: '第 1 节', quote: '原文', evidence_summary: '摘要', rerank_score: 0.91 })
  })
})
