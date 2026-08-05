import type { MeetingImportVectorization, ReviewBlock, ReviewMetadata } from '@/types/meetingImport'

export type SearchScope = 'FULL' | 'BLOCK'

export interface ReviewMatch {
  blockId: string
  start: number
  end: number
}

const RICH_CELL_COMMENT = /<!--.*?-->/gs

/** Remove parser artefacts while keeping table columns and readable spacing. */
export function cleanTableMarkdown(value: string) {
  return value
    .replace(RICH_CELL_COMMENT, '')
    .split(/\r?\n/)
    .map((line) => {
      const trimmed = line.trim()
      if (!trimmed) return ''
      if (!trimmed.includes('|')) return trimmed.replace(/\s+/g, ' ')
      const cells = trimmed.split('|')
      const leading = !cells[0].trim()
      const trailing = !cells[cells.length - 1].trim()
      const start = leading ? 1 : 0
      const end = trailing ? -1 : cells.length
      const normalized = cells.slice(start, end).map((cell) => cell.replace(/\s+/g, ' ').trim())
      const separator = normalized.length > 0 && normalized.every((cell) => /^:?-{3,}:?$/.test(cell))
      if (separator) return `${leading ? '|' : ''}${normalized.join('|')}${trailing ? '|' : ''}`
      return `${leading ? '| ' : ''}${normalized.join(' | ')}${trailing ? ' |' : ''}`
    })
    .filter(Boolean)
    .join('\n')
    .trim()
}

export function cleanTranscriptText(value: unknown, type?: string | null) {
  const text = String(value ?? '').replace(RICH_CELL_COMMENT, '')
  return type === 'table' || (text.includes('|') && text.includes('\n'))
    ? cleanTableMarkdown(text)
    : text.trim()
}

/**
 * Some older imports were stored without the `table` block type.  Detect the
 * markdown shape as a display fallback so those tables are still rendered as
 * tables instead of exposing pipe delimiters to the user.
 */
export function isTableBlock(block: Pick<ReviewBlock, 'text' | 'type' | 'block_type' | 'table_markdown'>) {
  const type = block.type || block.block_type
  if (type === 'table') return true
  const value = block.table_markdown || block.text || ''
  const lines = cleanTableMarkdown(value).split(/\r?\n/).filter(Boolean)
  const pipeLines = lines.filter((line) => line.includes('|'))
  return pipeLines.length >= 2 && pipeLines.every((line) => line.split('|').length >= 2)
}

export function tableBlockText(block: Pick<ReviewBlock, 'text' | 'type' | 'block_type' | 'table_markdown'>) {
  return cleanTranscriptText(block.table_markdown || block.text, 'table')
}

export function parseMarkdownTable(value: string) {
  return cleanTableMarkdown(value)
    .split(/\r?\n/)
    .filter((line) => line.includes('|'))
    .map((line) => {
      const cells = line.split('|')
      const leading = !cells[0].trim()
      const trailing = !cells[cells.length - 1].trim()
      return cells.slice(leading ? 1 : 0, trailing ? -1 : cells.length).map((cell) => cell.trim())
    })
    .filter((row) => row.some(Boolean))
    .filter((row, index) => !(index === 1 && row.every((cell) => /^:?-{3,}:?$/.test(cell))))
}

export function findLiteralMatches(blocks: Pick<ReviewBlock, 'id' | 'text'>[], query: string, options: { caseSensitive?: boolean; scope?: SearchScope; blockId?: string } = {}): ReviewMatch[] {
  if (!query) return []
  const selected = options.scope === 'BLOCK' && options.blockId ? blocks.filter((block) => block.id === options.blockId) : blocks
  const needle = options.caseSensitive ? query : query.toLocaleLowerCase()
  const matches: ReviewMatch[] = []
  for (const block of selected) {
    const haystack = options.caseSensitive ? block.text : block.text.toLocaleLowerCase()
    let from = 0
    while (from <= haystack.length - needle.length) {
      const index = haystack.indexOf(needle, from)
      if (index < 0) break
      matches.push({ blockId: block.id, start: index, end: index + query.length })
      from = index + Math.max(needle.length, 1)
    }
  }
  return matches
}

export function countReplacementPreview(blocks: Pick<ReviewBlock, 'id' | 'text'>[], query: string, options: { caseSensitive?: boolean; scope?: SearchScope; blockId?: string } = {}) {
  return findLiteralMatches(blocks, query, options).length
}

export function nextMatchIndex(total: number, current: number, direction: 1 | -1 = 1) {
  if (!total) return -1
  return (current + direction + total) % total
}

export function escapeHighlightHtml(value: string) {
  return value.replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char] || char)
}

export function highlightLiteral(text: string, query: string, currentOffset = -1, caseSensitive = false) {
  if (!query) return escapeHighlightHtml(text)
  const matches = findLiteralMatches([{ id: 'text', text }], query, { caseSensitive })
  let cursor = 0
  return matches.map((match) => {
    const before = escapeHighlightHtml(text.slice(cursor, match.start))
    const value = escapeHighlightHtml(text.slice(match.start, match.end))
    const current = match.start === currentOffset
    cursor = match.end
    return `${before}<mark class="review-match${current ? ' is-current' : ''}">${value}</mark>`
  }).join('') + escapeHighlightHtml(text.slice(cursor))
}

export function isValidUrl(value: string | null | undefined) {
  if (!value?.trim()) return true
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:'
  } catch {
    return false
  }
}

export function validateReviewMetadata(metadata: ReviewMetadata) {
  const errors: Partial<Record<'title' | 'starts_at' | 'ends_at' | 'online_url', string>> = {}
  if (!metadata.title?.value?.trim()) errors.title = '请输入会议标题'
  // A reliable extracted meeting date represents a whole-day meeting.  Do
  // not require reviewers to invent start/end times in that case; the API
  // stores a UTC full-day range only for legacy Meeting compatibility.
  const hasMeetingDate = Boolean(metadata.meeting_date?.value?.trim())
  if (!hasMeetingDate && !metadata.starts_at?.value) errors.starts_at = '请选择开始时间'
  if (!hasMeetingDate && !metadata.ends_at?.value) errors.ends_at = '请选择结束时间'
  if (!hasMeetingDate && metadata.starts_at?.value && metadata.ends_at?.value) {
    const start = new Date(metadata.starts_at.value).getTime()
    const end = new Date(metadata.ends_at.value).getTime()
    if (!Number.isNaN(start) && !Number.isNaN(end) && end <= start) errors.ends_at = '结束时间必须晚于开始时间'
  }
  if (metadata.online_url?.value && !isValidUrl(metadata.online_url.value)) errors.online_url = '请输入有效的 http(s) 地址'
  return errors
}

export function canEditReview(role?: string) {
  return role === 'owner' || role === 'admin' || role === 'editor'
}

export function isVectorizationSynced(vectorization: MeetingImportVectorization | null | undefined, revisionVersion: number | null | undefined) {
  if (!vectorization || vectorization.status.toUpperCase() !== 'SYNCED' || revisionVersion == null) return false
  return vectorization.current_revision_version === revisionVersion && vectorization.vectorized_revision_version === revisionVersion
}

export function vectorizationStatusLabel(status?: string) {
  switch (String(status || '').toUpperCase()) {
    case 'PENDING': return '等待向量化'
    case 'RUNNING': return '正在生成向量'
    case 'SYNCED': return '向量已同步'
    case 'STALE': return '需要重新同步'
    case 'FAILED': return '向量化失败'
    default: return '向量状态未知'
  }
}

export function vectorizationProgress(value?: number | null) {
  if (value == null || Number.isNaN(value)) return 0
  return Math.max(0, Math.min(100, Math.round(value <= 1 ? value * 100 : value)))
}

export function formatBlockSource(block: ReviewBlock) {
  const parts = [block.type || block.block_type, block.speaker, block.page_number ? `第 ${block.page_number} 页` : undefined, block.paragraph_number ? `段落 ${block.paragraph_number}` : undefined]
  return parts.filter(Boolean).join(' · ')
}
