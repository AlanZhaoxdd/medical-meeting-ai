import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { readChatResponse } from '@/api/meetingAnalysis'
import { buildMockMinutesDocument, mockChatTransport } from '@/api/meetingAnalysis.mock'
import { normalizeCandidate } from '@/api/meetingVerification'
import type { AnalysisModule, ChatHandlers, MeetingAnalysisContext, MeetingChatRequest, RagSource } from '@/types/meetingAnalysis'
import {
  composeMinutesDocument,
  formatMilliseconds,
  insufficientContextNotice,
  markdownToPlainText,
  normalizeAnalysisModules,
  normalizeAnalysisSources,
  recommendedQuestions,
  selectionTypeCounts,
  sourceSubtitle,
} from '@/utils/meetingAnalysis'

function contextFixture(overrides: Partial<MeetingAnalysisContext> = {}): MeetingAnalysisContext {
  return {
    meeting: {
      id: 'meeting-1',
      title: '某领域月度病例讨论会',
      starts_at: '2026-08-01T09:00:00+08:00',
      ends_at: '2026-08-01T10:30:00+08:00',
      location: '第一会议室',
      topic: '病例讨论',
      meeting_info: {
        meeting_purpose: '讨论近期重点病例并形成处理共识',
        discussion_topics: '病例复盘；用药方案；随访安排',
        advisor_names: ['张医生', '李医生'],
        internal_attendees: ['王医生'],
      },
      meeting_status: 'completed',
      analysis_status: 'succeeded',
      created_at: '2026-08-01T08:00:00+08:00',
      updated_at: '2026-08-01T11:00:00+08:00',
    },
    verification: {
      meeting: {} as MeetingAnalysisContext['meeting'],
      cut_point_questions: [
        { id: 'q1', question_type: 'cut_point', content: '是否采纳该用药方案', version: 1, support_score: 0.9 },
      ],
      open_ended_questions: [
        { id: 'q2', question_type: 'open_ended', content: '随访周期需要进一步确认', version: 1 },
      ],
      verification_version: 1,
      eligibility: { can_confirm: true, can_submit_analysis: true, missing_conditions: [] },
    },
    knowledgeBaseId: 'kb-1',
    knowledgeBaseName: '临床指南库',
    documents: [
      { id: 'doc-1', knowledgeBaseId: 'kb-1', meetingId: 'meeting-1', filename: '转写稿.json', sourceType: 'transcript' },
      { id: 'doc-2', knowledgeBaseId: 'kb-1', meetingId: null, filename: '诊疗指南-2026.pdf', sourceType: 'document', status: 'PUBLISHED' },
    ],
    transcript: [
      { id: 'block-1', order: 1, speaker: '张医生', startMs: 0, endMs: 15000, text: '我建议先观察一周再决定是否调整剂量。', documentId: 'doc-1' },
      { id: 'block-2', order: 2, speaker: '李医生', startMs: 16000, endMs: 30000, text: '同意，但需要把随访节点明确下来。', documentId: 'doc-1' },
    ],
    transcriptDocumentId: 'doc-1',
    ...overrides,
  }
}

describe('meeting analysis formatting helpers', () => {
  it('reads JSON-string and data-wrapped chat responses', () => {
    const response = readChatResponse(
      JSON.stringify({
        data: {
          conversation_id: 'conversation-1',
          message_id: 'message-1',
          answer: '答案 [1]',
          status: 'COMPLETED',
          sources: [{ index: 1, type: 'transcript', title: '会议片段', snippet: '证据' }],
        },
      }),
    )
    expect(response.conversationId).toBe('conversation-1')
    expect(response.messageId).toBe('message-1')
    expect(response.answer).toBe('答案 [1]')
    expect(response.sources).toHaveLength(1)
  })

  it('formats milliseconds into time labels', () => {
    expect(formatMilliseconds(65_000)).toBe('01:05')
    expect(formatMilliseconds(3_661_000)).toBe('01:01:01')
    expect(formatMilliseconds(null)).toBeNull()
    expect(formatMilliseconds(-1)).toBeNull()
  })

  it('converts markdown to readable plain text', () => {
    expect(markdownToPlainText('## 标题\n- 项目一\n- 项目二\n**加粗** `code` [链接](https://x)')).toContain('标题')
    expect(markdownToPlainText('**加粗**')).toBe('加粗')
    expect(markdownToPlainText('# 一级标题\n\n正文内容')).toContain('正文内容')
  })

  it('builds source subtitles from available fields only', () => {
    expect(sourceSubtitle({ id: 's1', index: 1, type: 'transcript', title: 't', snippet: 's', speakerName: '张医生', timestamp: '00:10', pageNumber: 2 })).toContain('张医生')
    expect(sourceSubtitle({ id: 's2', index: 2, type: 'knowledge_base', title: 't', snippet: 's', documentName: '指南.pdf', knowledgeBaseName: '指南库', chunkId: 'c-1' })).toContain('c-1')
  })

  it('provides recommended questions for the empty state', () => {
    expect(recommendedQuestions.length).toBeGreaterThanOrEqual(3)
    expect(recommendedQuestions[0]).toContain('共识')
  })
})

describe('mock minutes document', () => {
  it('composes a single holistic minutes module from real meeting data', () => {
    const module = buildMockMinutesDocument(contextFixture())
    expect(module.id).toBe('minutes')
    expect(module.category).toBe('ai')
    expect(module.state).toBe('ready')
    expect(module.content).toContain('## 会议总述')
    expect(module.content).toContain('## 会议概况')
    expect(module.content).toContain('## 核心结论与共识')
    expect(module.content).toContain('## 关键决策点（切点问题）')
    expect(module.content).toContain('## 待确认事项（开放性问题）')
    expect(module.content).toContain('## 下次会议与跟进安排')
    expect(module.content).toContain('暂未提及')
    expect(module.content).toContain('是否采纳该用药方案')
    expect(module.content).toContain('随访周期需要进一步确认')
    expect(module.references.some((source) => source.type === 'cutoff_question')).toBe(true)
    expect(module.references.some((source) => source.type === 'open_question')).toBe(true)
  })

  it('formats action items with owner, deadline and deliverable from explicit text', () => {
    const module = buildMockMinutesDocument(
      contextFixture({
        verification: {
          ...contextFixture().verification,
          open_ended_questions: [
            ...contextFixture().verification.open_ended_questions,
            { id: 'q3', question_type: 'open_ended', content: '由王医生负责跟进随访方案，下周三前提交最终版', version: 1 },
            { id: 'q4', question_type: 'open_ended', content: '后续需跟进随访方案确认', version: 1 },
          ],
        },
      }),
    )
    expect(module.content).toContain('（责任人：王医生；截止时间：下周三前；交付物：最终版）')
    expect(module.content).toContain('（责任人：未明确；截止时间：未明确；交付物：未明确）')
  })

  it('includes organizer and recorder in the overview when present', () => {
    const module = buildMockMinutesDocument(
      contextFixture({
        meeting: {
          ...contextFixture().meeting,
          organizer: '市场部',
          meeting_info: { ...contextFixture().meeting.meeting_info, recorder: '小李' },
        },
      }),
    )
    expect(module.content).toContain('**组织方**：市场部')
    expect(module.content).toContain('**记录人**：小李')
  })

  it('cites follow-up arrangements when mentioned in the transcript', () => {
    const module = buildMockMinutesDocument(
      contextFixture({
        transcript: [
          ...contextFixture().transcript,
          { id: 'block-3', order: 3, speaker: '张医生', startMs: 31000, endMs: 45000, text: '下次会议定在下周三，届时讨论随访结果。', documentId: 'doc-1' },
        ],
      }),
    )
    expect(module.content).toContain('## 下次会议与跟进安排')
    expect(module.content).toContain('转写中提到后续跟进安排')
    expect(module.references.some((source) => source.type === 'transcript')).toBe(true)
  })

  it('marks the minutes empty when the meeting has no source material', () => {
    const module = buildMockMinutesDocument(
      contextFixture({
        meeting: { ...contextFixture().meeting, meeting_info: {}, topic: null, location: null },
        verification: {
          ...contextFixture().verification,
          cut_point_questions: [],
          open_ended_questions: [],
        },
        transcript: [],
        documents: [],
        knowledgeBaseId: null,
        knowledgeBaseName: null,
      }),
    )
    expect(module.state).toBe('empty')
    expect(module.content).toBeUndefined()
  })
})

describe('composeMinutesDocument', () => {
  const source = (id: string, index: number, type: RagSource['type'], title: string): RagSource => ({
    id,
    index,
    type,
    title,
    snippet: '摘要',
  })

  it('passes through a single new-format minutes module', () => {
    const module: AnalysisModule = {
      id: 'minutes',
      title: 'AI 通读纪要',
      category: 'ai',
      state: 'ready',
      content: '## 会议概况\n正文 [1]',
      references: [source('s1', 1, 'knowledge_base', '指南')],
    }
    expect(composeMinutesDocument([module])).toBe(module)
  })

  it('merges legacy modules in fixed order and renumbers citations', () => {
    const legacy: AnalysisModule[] = [
      {
        id: 'summary',
        title: '会议核心摘要',
        category: 'meeting',
        state: 'ready',
        content: '摘要正文 [1] [2]',
        references: [
          source('a', 1, 'knowledge_base', '文档A'),
          source('b', 2, 'cutoff_question', '切点问题'),
        ],
      },
      {
        id: 'transcript',
        title: '会议转写稿',
        category: 'transcript',
        state: 'ready',
        content: '转写内容',
        references: [],
      },
      {
        id: 'cutoff-questions',
        title: '切点问题分析',
        category: 'questions',
        state: 'ready',
        items: ['是否采纳该用药方案'],
        references: [
          source('a', 1, 'knowledge_base', '文档A'),
          source('c', 2, 'open_question', '开放性问题'),
        ],
      },
    ]
    const composed = composeMinutesDocument(legacy)
    expect(composed?.id).toBe('minutes')
    expect(composed?.category).toBe('ai')
    expect(composed?.state).toBe('ready')
    expect(composed?.content).toContain('## 会议核心摘要')
    expect(composed?.content).toContain('## 切点问题分析')
    expect(composed?.content).toContain('摘要正文 [1] [2]')
    expect(composed?.content).toContain('- 是否采纳该用药方案')
    expect(composed?.content).not.toContain('会议转写稿')
    expect(composed?.references.map((item) => item.index)).toEqual([1, 2, 3])
    expect(composed?.references.map((item) => item.id)).toEqual(['a', 'b', 'c'])
  })

  it('returns an empty module when legacy modules have no content', () => {
    const composed = composeMinutesDocument([
      { id: 'summary', title: '会议核心摘要', category: 'meeting', state: 'empty', references: [] },
    ])
    expect(composed?.id).toBe('minutes')
    expect(composed?.state).toBe('empty')
  })

  it('returns null when only the transcript module exists', () => {
    const composed = composeMinutesDocument([
      { id: 'transcript', title: '会议转写稿', category: 'transcript', state: 'ready', content: '转写', references: [] },
    ])
    expect(composed).toBeNull()
  })
})

describe('mock chat transport', () => {
  beforeAll(() => {
    vi.stubGlobal('window', {
      setTimeout: vi.fn((fn: () => void) => setTimeout(fn, 0)),
      clearTimeout: (id: ReturnType<typeof setTimeout>) => clearTimeout(id),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })
  })

  afterAll(() => {
    vi.unstubAllGlobals()
  })

  it('returns INSUFFICIENT_CONTEXT instead of fabricating an answer', async () => {
    const context = contextFixture({
      verification: {
        ...contextFixture().verification,
        cut_point_questions: [],
        open_ended_questions: [],
      },
      transcript: [],
      documents: [],
      knowledgeBaseId: null,
      knowledgeBaseName: null,
    })
    const payload: MeetingChatRequest = { meetingId: 'meeting-1', question: '本次会议形成了哪些核心共识？', scope: 'MEETING_AND_KB' }
    const deltas: string[] = []
    let finalStatus: string | null = null
    let finalAnswer = ''
    const handlers: ChatHandlers = {
      onDelta: (delta) => deltas.push(delta),
      onDone: (response) => {
        finalStatus = response.status
        finalAnswer = response.answer
      },
    }
    await mockChatTransport.chat(payload, context, handlers)
    expect(finalStatus).toBe('INSUFFICIENT_CONTEXT')
    expect(finalAnswer).toBe(insufficientContextNotice)
    expect(deltas.length).toBe(0)
  })

  it('streams an answer grounded in real questions and sources', async () => {
    const context = contextFixture()
    const payload: MeetingChatRequest = { meetingId: 'meeting-1', question: '本次会议形成了哪些核心共识？', scope: 'CURRENT_MEETING' }
    const deltas: string[] = []
    let finalSources = 0
    let finalStatus: string | null = null
    const handlers: ChatHandlers = {
      onDelta: (delta) => deltas.push(delta),
      onDone: (response) => {
        finalStatus = response.status
        finalSources = response.sources.length
      },
    }
    await mockChatTransport.chat(payload, context, handlers)
    expect(finalStatus).toBe('COMPLETED')
    expect(deltas.length).toBeGreaterThan(1)
    expect(finalSources).toBeGreaterThan(0)
  })
})

describe('real analysis result normalization', () => {
  it('maps backend sources into RagSource fields', () => {
    const sources = normalizeAnalysisSources([
      {
        index: 2,
        type: 'transcript',
        title: '会议转写片段',
        snippet: '摘要',
        content: '转写全文',
        speaker_name: '张医生',
        timestamp: '00:12',
        page_number: null,
        chunk_id: 'c1',
        document_title: '转写.json',
      },
    ])
    expect(sources).toHaveLength(1)
    expect(sources[0].speakerName).toBe('张医生')
    expect(sources[0].content).toBe('转写全文')
    expect(sources[0].timestamp).toBe('00:12')
    expect(sources[0].chunkId).toBe('c1')
    expect(sources[0].index).toBe(2)
  })

  it('binds module citations to sources and marks empty modules', () => {
    const sources = normalizeAnalysisSources([
      { index: 1, type: 'knowledge_base', title: '指南', snippet: '摘要', chunk_id: 'k1' },
      { index: 2, type: 'cutoff_question', title: '切点问题', snippet: '问题', question_id: 'q1' },
    ])
    const modules = normalizeAnalysisModules(
      [
        { id: 'summary', title: '会议核心摘要', content: '正文 [1]', citations: [1, 99] },
        { id: 'actions', title: '行动项', content: null, items: [], citations: [] },
      ],
      sources,
    )
    expect(modules).toHaveLength(2)
    expect(modules[0].references.map((source) => source.index)).toEqual([1])
    expect(modules[1].state).toBe('empty')
  })

  it('counts selected questions per type', () => {
    const questions = [
      { id: 'a', question_type: 'cut_point' },
      { id: 'b', question_type: 'open_ended' },
      { id: 'c', question_type: 'cut_point' },
    ]
    expect(selectionTypeCounts(questions, new Set(['a', 'b']))).toEqual({ cutPoint: 1, openEnded: 1 })
    expect(selectionTypeCounts(questions, new Set(['c']))).toEqual({ cutPoint: 1, openEnded: 0 })
  })

  it('normalizes manual questions into selectable candidates', () => {
    const candidate = normalizeCandidate({
      id: 'q1',
      question_type: 'cut_point',
      content: '  手工问题  ',
      source: 'manual',
      version: 2,
      analysis_selected: true,
      candidate_rank: null,
    })
    expect(candidate).not.toBeNull()
    expect(candidate?.content).toBe('手工问题')
    expect(candidate?.rank).toBeNull()
    expect(candidate?.selected).toBe(true)
    expect(candidate?.version).toBe(2)
  })
})
