import type {
  AnalysisModule,
  MeetingAnalysisContext,
  MeetingChatRequest,
  MeetingChatResponse,
  MeetingChatTransport,
  RagSource,
  RagSourceType,
  TranscriptSegment,
} from '@/types/meetingAnalysis'
import { formatMilliseconds, insufficientContextNotice } from '@/utils/meetingAnalysis'
import { normalizeMeetingInfo, type VerificationQuestion } from '@/types/meetingVerification'

const delay = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException('The operation was aborted.', 'AbortError'))
      return
    }
    const timer = window.setTimeout(resolve, ms)
    signal?.addEventListener(
      'abort',
      () => {
        window.clearTimeout(timer)
        reject(new DOMException('The operation was aborted.', 'AbortError'))
      },
      { once: true },
    )
  })

const ACTION_KEYWORDS = /行动|下一步|跟进|落实|执行|待办|todo|之后.*(安排|推进)/i
const ACTION_OWNER_PATTERN = /由([^，。；；,]+?)(?:负责|跟进|牵头|落实)/
const ACTION_DEADLINE_PATTERN = /(\d{1,2}月\d{1,2}日|本周[内五]|下周[一二三四五六日天前]*|本月底)/
const ACTION_DELIVERABLE_PATTERN = /(?:提交|输出|交付)([^，。；；]{1,20})/

function questionSources(
  meetingId: string,
  questions: VerificationQuestion[],
  type: RagSourceType,
): RagSource[] {
  return questions.map((question, index) => ({
    id: `${type}-${question.id}`,
    index: index + 1,
    type,
    title: type === 'cutoff_question' ? '切点问题' : '开放性问题',
    snippet: question.content,
    content: question.rationale || question.content,
    questionId: question.id,
    meetingId,
  }))
}

function transcriptSources(meetingId: string, segments: TranscriptSegment[]): RagSource[] {
  return segments.slice(0, 20).map((segment, index) => ({
    id: `transcript-${segment.id}`,
    index: index + 1,
    type: 'transcript',
    title: segment.speaker ? `转写片段 · ${segment.speaker}` : `转写片段 ${index + 1}`,
    snippet: segment.text.slice(0, 160),
    content: segment.text,
    speakerName: segment.speaker ?? undefined,
    timestamp: formatMilliseconds(segment.startMs) ?? undefined,
    blockId: segment.id,
    meetingId,
    documentId: segment.documentId,
  }))
}

function kbDocuments(context: MeetingAnalysisContext) {
  return context.documents.filter((document) => document.sourceType !== 'transcript')
}

function knowledgeBaseSources(context: MeetingAnalysisContext): RagSource[] {
  return kbDocuments(context).slice(0, 10).map((document, index) => ({
    id: `kb-${document.id}`,
    index: index + 1,
    type: 'knowledge_base',
    title: document.filename,
    snippet: `文档《${document.filename}》已关联至当前会议${document.status ? `（${document.status}）` : ''}，可用于核对结论。`,
    documentName: document.filename,
    knowledgeBaseName: context.knowledgeBaseName ?? undefined,
    knowledgeBaseId: document.knowledgeBaseId,
    documentId: document.id,
    meetingId: context.meeting.id,
  }))
}

function splitTopics(value: string | null | undefined): string[] {
  if (!value) return []
  return value
    .split(/[;,，；、。\n]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

/** Compose the mock "AI 通读纪要" module from real meeting data. No names or medical claims are invented. */
export function buildMockMinutesDocument(context: MeetingAnalysisContext): AnalysisModule {
  const { meeting, verification, transcript } = context
  const info = normalizeMeetingInfo(meeting.meeting_info)
  const attendees = [...info.advisor_names, ...info.internal_attendees]
  const cutPoints = verification.cut_point_questions
  const openQuestions = verification.open_ended_questions

  const topics = splitTopics(info.discussion_topics).concat(meeting.topic ? [meeting.topic] : [])
  const kbSourceList = knowledgeBaseSources(context)
  const sources: RagSource[] = []
  const push = (source: RagSource): number => {
    const existing = sources.find((item) => item.id === source.id)
    if (existing) return existing.index
    sources.push({ ...source, index: sources.length + 1 })
    return sources.length
  }

  const sections: string[] = []

  const summarySentences: string[] = []
  const theme = meeting.topic || info.meeting_purpose || meeting.title
  if (theme) {
    const topicPart = topics.length ? `，主要讨论${[...new Set(topics)].slice(0, 2).join('、')}` : ''
    summarySentences.push(`本场会议围绕「${theme}」展开${topicPart}。`)
  }
  if (cutPoints.length) {
    summarySentences.push(`会议就「${cutPoints[0].content}」等关键决策点形成初步结论。`)
  }
  if (openQuestions.length) {
    summarySentences.push(`同时，「${openQuestions[0].content}」等事项仍需进一步确认。`)
  }
  if (transcript.length || cutPoints.length || openQuestions.length) {
    summarySentences.push('总体来看，会议在核心议题上形成结论方向，后续需围绕行动项与待确认事项推进落实。')
  }
  if (summarySentences.length) sections.push(`## 会议总述\n\n${summarySentences.join('')}`)

  const overviewLines: string[] = []
  if (meeting.title) overviewLines.push(`- **会议名称**：${meeting.title}`)
  if (meeting.topic) overviewLines.push(`- **领域/科室**：${meeting.topic}`)
  if (info.meeting_purpose) overviewLines.push(`- **会议目的**：${info.meeting_purpose}`)
  if (topics.length) overviewLines.push(`- **主要议题**：${[...new Set(topics)].join('、')}`)
  if (attendees.length) overviewLines.push(`- **参会人员（${attendees.length} 人）**：${attendees.join('、')}`)
  if (meeting.location) overviewLines.push(`- **地点**：${meeting.location}`)
  if (meeting.organizer) overviewLines.push(`- **组织方**：${meeting.organizer}`)
  if (info.recorder) overviewLines.push(`- **记录人**：${info.recorder}`)
  overviewLines.push(`- **材料情况**：转写片段 ${transcript.length} 条、切点问题 ${cutPoints.length} 条、开放性问题 ${openQuestions.length} 条。`)
  if (overviewLines.length) sections.push(`## 会议概况\n\n${overviewLines.join('\n')}`)

  const consensusItems = cutPoints.map((question) => {
    const index = push(questionSources(meeting.id, [question], 'cutoff_question')[0])
    const score = question.support_score != null
      ? `支持度 ${Math.round(question.support_score * 100)}%`
      : question.confidence != null ? `置信度 ${Math.round(question.confidence * 100)}%` : ''
    return `- **${question.content}**：已根据转写与证据确认。${score ? `${score}。` : ''}[${index}]`
  })
  if (consensusItems.length) sections.push(`## 核心结论与共识\n\n${consensusItems.join('\n')}`)

  const cutpointItems = cutPoints.map((question) => {
    const index = push(questionSources(meeting.id, [question], 'cutoff_question')[0])
    const topic = question.topic ? `（主题：${question.topic}）` : ''
    const score = question.support_score != null
      ? `支持度 ${Math.round(question.support_score * 100)}%`
      : question.confidence != null ? `置信度 ${Math.round(question.confidence * 100)}%` : ''
    return `**${question.content}**${topic}：已给出结论，${score ? `${score}。` : ''}支持证据见引用来源。[${index}]`
  })
  if (cutpointItems.length) sections.push(`## 关键决策点（切点问题）\n\n${cutpointItems.join('\n')}`)

  const openItems = openQuestions.map((question) => {
    const index = push(questionSources(meeting.id, [question], 'open_question')[0])
    const topic = question.topic ? `（主题：${question.topic}）` : ''
    return `**${question.content}**${topic}：当前材料中尚未明确，需进一步补充背景、观点或后续行动。[${index}]`
  })
  if (openItems.length) sections.push(`## 待确认事项（开放性问题）\n\n${openItems.join('\n')}`)

  const unresolved = openQuestions.filter((question) => /分歧|未确认|待确认|尚未|遗留/.test(question.content))
  if (unresolved.length) {
    const unresolvedItems = unresolved.map((question) => {
      const index = push(questionSources(meeting.id, [question], 'open_question')[0])
      return `- **${question.content}**：存在分歧或尚未收口，建议后续跟进。[${index}]`
    })
    sections.push(`## 分歧与遗留问题\n\n${unresolvedItems.join('\n')}`)
  } else if (transcript.length || cutPoints.length || openQuestions.length) {
    sections.push('## 分歧与遗留问题\n\n暂无明确的分歧记录。')
  }

  const actionQuestions = openQuestions.filter((question) => ACTION_KEYWORDS.test(question.content))
  if (actionQuestions.length) {
    const actionItems = actionQuestions.map((question) => {
      const index = push(questionSources(meeting.id, [question], 'open_question')[0])
      const owner = question.content.match(ACTION_OWNER_PATTERN)?.[1]?.trim() || '未明确'
      const deadline = question.content.match(ACTION_DEADLINE_PATTERN)?.[1]?.trim() || '未明确'
      const deliverable = question.content.match(ACTION_DELIVERABLE_PATTERN)?.[1]?.trim() || '未明确'
      return `- **${question.content}**（责任人：${owner}；截止时间：${deadline}；交付物：${deliverable}）[${index}]`
    })
    sections.push(`## 行动项\n\n${actionItems.join('\n')}`)
  } else if (transcript.length || cutPoints.length || openQuestions.length) {
    sections.push('## 行动项\n\n暂无明确行动项。')
  }

  const followUpSegments = transcript.filter((segment) => /下次会议|下次|跟进时间|后续安排|再议|另行通知/.test(segment.text))
  if (followUpSegments.length) {
    const index = push(transcriptSources(meeting.id, followUpSegments)[0])
    const first = followUpSegments[0].text.trim()
    const excerpt = first.length > 80 ? `${first.slice(0, 80)}…` : first
    sections.push(`## 下次会议与跟进安排\n\n转写中提到后续跟进安排：${excerpt}。[${index}]`)
  } else if (transcript.length || cutPoints.length || openQuestions.length) {
    sections.push('## 下次会议与跟进安排\n\n暂未提及。')
  }

  const kbDocs = kbDocuments(context)
  if (context.knowledgeBaseName || kbDocs.length) {
    const kbLines: string[] = []
    if (context.knowledgeBaseName) kbLines.push(`- **知识库**：${context.knowledgeBaseName}`)
    if (kbDocs.length) {
      kbLines.push(`- **关联文档（${kbDocs.length} 篇）**：`)
      for (const doc of kbDocs.slice(0, 8)) {
        const matched = kbSourceList.find((source) => source.documentId === doc.id)
        if (!matched) continue
        kbLines.push(`  - 《${doc.filename}》 [${push(matched)}]`)
      }
    }
    if (cutPoints.length || openQuestions.length) {
      kbLines.push(`- **结论依据**：本次共确认切点问题 ${cutPoints.length} 条、开放性问题 ${openQuestions.length} 条，结论可在下方引用来源中逐条核对。`)
    }
    sections.push(`## 知识库依据\n\n${kbLines.join('\n')}`)
  }

  const hasSubstance = Boolean(
    meeting.topic ||
      info.meeting_purpose ||
      topics.length ||
      attendees.length ||
      meeting.location ||
      transcript.length ||
      cutPoints.length ||
      openQuestions.length ||
      kbDocs.length,
  )
  return {
    id: 'minutes',
    title: 'AI 通读纪要',
    description: 'AI 通读整篇确认稿后生成的会议纪要，含会议总述、核心结论、关键决策点与待确认事项。',
    state: hasSubstance ? 'ready' : 'empty',
    content: hasSubstance ? sections.join('\n\n---\n\n') : undefined,
    references: sources,
    category: 'ai',
  }
}

const NEWLINE = '\n'

function streamChunks(
  text: string,
  onDelta: (chunk: string) => void,
  signal?: AbortSignal,
): Promise<void> {
  return new Promise((resolve, reject) => {
    let index = 0
    const step = () => {
      if (signal?.aborted) {
        reject(new DOMException('The operation was aborted.', 'AbortError'))
        return
      }
      const size = 8 + Math.floor(Math.random() * 7)
      const chunk = text.slice(index, index + size)
      if (!chunk) {
        resolve()
        return
      }
      index += size
      onDelta(chunk)
      window.setTimeout(step, 24 + Math.floor(Math.random() * 30))
    }
    step()
  })
}

interface MockAnswerPlan {
  answer: string
  sources: RagSource[]
}

function planAnswer(request: MeetingChatRequest, context: MeetingAnalysisContext): MockAnswerPlan | null {
  const { verification, transcript } = context
  const cutPoints = verification.cut_point_questions
  const openQuestions = verification.open_ended_questions
  const question = request.question
  const kbDocs = kbDocuments(context)
  const meeting = context.meeting
  const info = normalizeMeetingInfo(meeting.meeting_info)

  const isConsensus = /共识|一致|结论/.test(question)
  const isViewpoints = /观点|意见|区别|不同|差异/.test(question)
  const isUnresolved = /未明确|未解决|待确认|尚未|分歧|遗留/.test(question)
  const isKbRelated = /知识库|历史|一致|依据/.test(question)

  if (isConsensus && cutPoints.length) {
    const lines = cutPoints.map((q, index) => `${index + 1}. ${q.content} [${index + 1}]`)
    return {
      answer: `根据已确认的切点问题，本次会议可以确认以下核心结论：\n${NEWLINE}${lines.join('\n')}\n${NEWLINE}如需逐条核对，可点击正文角标查看对应的切点问题来源。`,
      sources: questionSources(meeting.id, cutPoints, 'cutoff_question'),
    }
  }

  if (isViewpoints && transcript.length) {
    const speakers = new Map<string, TranscriptSegment[]>()
    for (const segment of transcript) {
      const key = segment.speaker?.trim() || '未标注发言者'
      speakers.set(key, [...(speakers.get(key) ?? []), segment])
    }
    const lines = [...speakers.entries()].slice(0, 8).map(([speaker, segments], index) => {
      const excerpt = (segments[0]?.text ?? '').trim().slice(0, 70)
      return `${index + 1}. **${speaker}**：${excerpt} [${index + 1}]`
    })
    return {
      answer: `转写稿中各参会者的主要观点摘录如下（仅引用原始转写内容）：\n${NEWLINE}${lines.join('\n')}\n${NEWLINE}完整表述请通过引用来源定位到对应转写片段。`,
      sources: transcriptSources(meeting.id, transcript).slice(0, 8),
    }
  }

  if (isUnresolved && openQuestions.length) {
    const lines = openQuestions.map((q, index) => `${index + 1}. ${q.content} [${index + 1}]`)
    return {
      answer: `以下问题在本次会议材料中尚未得到明确结论，建议作为后续跟进重点：\n${NEWLINE}${lines.join('\n')}`,
      sources: questionSources(meeting.id, openQuestions, 'open_question'),
    }
  }

  if (isKbRelated) {
    if (!kbDocs.length || !context.knowledgeBaseId) {
      return {
        answer: insufficientContextNotice,
        sources: [],
      }
    }
    const docLines = kbDocs.slice(0, 8).map((doc, index) => `${index + 1}. 《${doc.filename}》 [${index + 1}]`)
    const history = transcript.length ? `本场会议转写片段共 ${transcript.length} 条` : '本场会议暂无可用的转写片段'
    return {
      answer: `当前知识库共关联 ${kbDocs.length} 篇文档，可用于核对历史结论的一致性：\n${NEWLINE}${docLines.join('\n')}\n${NEWLINE}${history}。如需判断一致性，请在文档与会议记录之间逐条对照引用内容。`,
      sources: knowledgeBaseSources(context),
    }
  }

  if (cutPoints.length || transcript.length || openQuestions.length) {
    const lines: string[] = []
    if (info.meeting_purpose) lines.push(`- 会议目的：${info.meeting_purpose}`)
    if (info.discussion_topics) lines.push(`- 主要议题：${info.discussion_topics}`)
    if (cutPoints.length) lines.push(`- 已确认切点问题 ${cutPoints.length} 条：${cutPoints[0].content}${cutPoints.length > 1 ? ` 等` : ''}`)
    if (openQuestions.length) lines.push(`- 待确认开放性问题 ${openQuestions.length} 条：${openQuestions[0].content}${openQuestions.length > 1 ? ` 等` : ''}`)
    if (transcript.length) lines.push(`- 转写记录 ${transcript.length} 条，可定位到具体说话人与时间点。`)
    return {
      answer: `结合当前会议材料，可以回答如下：\n${NEWLINE}${lines.join('\n')}\n${NEWLINE}上述内容均来自会议记录，可通过下方参考来源逐条核验。`,
      sources: [
        ...(cutPoints.length ? questionSources(meeting.id, cutPoints, 'cutoff_question') : []),
        ...(openQuestions.length ? questionSources(meeting.id, openQuestions, 'open_question') : []),
        ...transcriptSources(meeting.id, transcript).slice(0, 6),
      ].slice(0, 8),
    }
  }

  return null
}

export const mockChatTransport: MeetingChatTransport = {
  mode: 'mock',
  async chat(payload, context, handlers, signal) {
    if (!context) {
      handlers.onError?.(new Error('会议上下文不可用，无法进行问答。'))
      return
    }
    handlers.onStage?.('RETRIEVING_MEETING')
    await delay(420, signal)
    if (payload.scope === 'MEETING_AND_KB' && context.knowledgeBaseId) {
      handlers.onStage?.('RETRIEVING_KB')
      await delay(420, signal)
    }
    handlers.onStage?.('ORGANIZING')
    await delay(380, signal)

    const plan = planAnswer(payload, context)
    if (!plan) {
      const response: MeetingChatResponse = {
        conversationId: payload.conversationId ?? `mock-${context.meeting.id}`,
        messageId: `msg-${Date.now()}`,
        answer: insufficientContextNotice,
        status: 'INSUFFICIENT_CONTEXT',
        sources: [],
        route: 'MEETING_GROUNDED',
      }
      handlers.onDone?.(response)
      return
    }
    if (plan.answer === insufficientContextNotice) {
      const response: MeetingChatResponse = {
        conversationId: payload.conversationId ?? `mock-${context.meeting.id}`,
        messageId: `msg-${Date.now()}`,
        answer: plan.answer,
        status: 'INSUFFICIENT_CONTEXT',
        sources: [],
        route: 'MEETING_GROUNDED',
      }
      handlers.onDone?.(response)
      return
    }

    handlers.onStage?.('STREAMING')
    await streamChunks(plan.answer, (chunk) => handlers.onDelta?.(chunk), signal)
    handlers.onDone?.({
      conversationId: payload.conversationId ?? `mock-${context.meeting.id}`,
      messageId: `msg-${Date.now()}`,
      answer: plan.answer,
      status: 'COMPLETED',
      sources: plan.sources,
      suggestedQuestions: [],
      route: 'MEETING_GROUNDED',
    })
  },
}
