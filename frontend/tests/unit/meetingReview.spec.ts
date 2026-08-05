import { describe, expect, it } from 'vitest'
import type { ReviewMetadata } from '@/types/meetingImport'
import { normalizeReview, normalizeVectorization } from '@/api/meetingImports'
import { canEditReview, cleanTableMarkdown, cleanTranscriptText, countReplacementPreview, findLiteralMatches, isTableBlock, isVectorizationSynced, nextMatchIndex, parseMarkdownTable, tableBlockText, validateReviewMetadata, vectorizationProgress, vectorizationStatusLabel } from '@/utils/meetingReview'

const blocks = [{ id: 'a', text: '药物治疗与风险' }, { id: 'b', text: 'The Risk is low. risk.' }]

describe('meeting review helpers', () => {
  it('matches Chinese literals and English case-insensitively by default', () => {
    expect(findLiteralMatches(blocks, '风险')).toHaveLength(1)
    expect(findLiteralMatches(blocks, 'risk')).toHaveLength(2)
    expect(findLiteralMatches(blocks, 'risk', { caseSensitive: true })).toHaveLength(1)
  })

  it('supports full/current block scope, counts previews, and wraps navigation', () => {
    expect(findLiteralMatches(blocks, 'risk', { scope: 'BLOCK', blockId: 'a' })).toHaveLength(0)
    expect(findLiteralMatches(blocks, 'risk', { scope: 'BLOCK', blockId: 'b' })).toHaveLength(2)
    expect(countReplacementPreview(blocks, 'risk')).toBe(2)
    expect(nextMatchIndex(2, 1, 1)).toBe(0)
    expect(nextMatchIndex(2, 0, -1)).toBe(1)
    expect(nextMatchIndex(0, 0)).toBe(-1)
  })

  it('validates required metadata, time ordering and URLs', () => {
    const base = {
      title: { value: '' }, starts_at: { value: '2026-08-02T10:00:00Z' }, ends_at: { value: '2026-08-02T09:00:00Z' },
      location: { value: null }, online_url: { value: 'ftp://bad' }, organizer: { value: null }, topic: { value: null }, description: { value: null },
    } as ReviewMetadata
    expect(validateReviewMetadata(base)).toMatchObject({ title: expect.any(String), ends_at: expect.any(String), online_url: expect.any(String) })
  })

  it('does not require start/end time when a meeting date was extracted', () => {
    const dateOnly = {
      title: { value: '专家顾问会' }, meeting_date: { value: '2024年03月16日' },
      starts_at: { value: null }, ends_at: { value: null }, online_url: { value: null },
    } as ReviewMetadata

    expect(validateReviewMetadata(dateOnly)).toEqual({})
  })

  it('gates mutations by role', () => {
    expect(canEditReview('owner')).toBe(true)
    expect(canEditReview('admin')).toBe(true)
    expect(canEditReview('editor')).toBe(true)
    expect(canEditReview('reviewer')).toBe(false)
  })

  it('normalizes deployed review aliases into the canonical client shape', () => {
    const result = normalizeReview({
      import_id: 'i1', status: 'READY_FOR_REVIEW', file: { filename: 'notes.txt' }, knowledge_base_id: 'kb1',
      original_blocks: [{ block_id: 'b1', block_type: 'paragraph', text: '原文', source_ref: { page_number: 2 } }],
      current_revision: { revision_id: 'r1', version: 3, status: 'DRAFT', blocks: [{ block_id: 'b1', block_type: 'paragraph', text: '校对' }] },
      revision_history: [{ revision_id: 'r0', version: 2, status: 'CONFIRMED', blocks: [] }],
      metadata: { title: { value: '会议' } }, metadata_version: 4, needs_confirmation_count: 0,
      vectorization: { status: 'synced', current_version: 3, vectorized_version: 3, progress_percent: 100 },
    })
    expect(result.import.id).toBe('i1')
    expect(result.current_revision.id).toBe('r1')
    expect(result.current_revision.blocks[0].id).toBe('b1')
    expect(result.original_blocks[0].page_number).toBe(2)
    expect(result.meeting_metadata.title.value).toBe('会议')
    expect(result.revisions).toHaveLength(1)
    expect(result.vectorization.status).toBe('SYNCED')
    expect(isVectorizationSynced(result.vectorization, result.current_revision.version)).toBe(true)
  })

  it('normalizes vectorization progress and gates on the exact revision version', () => {
    const active = normalizeVectorization({ status: 'RUNNING', progress: 0.42, current_revision_version: 4 })
    expect(active.progress).toBe(0.42)
    expect(vectorizationProgress(active.progress)).toBe(42)
    expect(vectorizationStatusLabel('STALE')).toBe('需要重新同步')
    const synced = normalizeVectorization({ status: 'SYNCED', current_revision_version: 4, vectorized_revision_version: 4 })
    expect(isVectorizationSynced(synced, 4)).toBe(true)
    expect(isVectorizationSynced(synced, 5)).toBe(false)
  })

  it('preserves a missing current revision instead of fabricating an editable id', () => {
    const result = normalizeReview({ import_id: 'i1', status: 'READY_FOR_REVIEW', original_blocks: [], current_revision: null, metadata: {} })
    expect(result.current_revision).toBeNull()
  })

  it('cleans rich-cell comments and renders markdown tables as readable rows', () => {
    const raw = '| <!-- rich cell -->   |   政策指引   |\n|---|---|\n| 依柯胰岛素东南区区域专家顾问会 | <!-- rich cell --> |'
    expect(cleanTableMarkdown(raw)).toBe('|  | 政策指引 |\n|---|---|\n| 依柯胰岛素东南区区域专家顾问会 |  |')
    expect(parseMarkdownTable(raw)).toEqual([['', '政策指引'], ['依柯胰岛素东南区区域专家顾问会', '']])
    expect(cleanTranscriptText('说明 <!-- rich cell --> 内容')).toBe('说明  内容')
    expect(isTableBlock({ type: 'paragraph', text: raw })).toBe(true)
    expect(tableBlockText({ type: 'paragraph', text: raw })).toContain('政策指引')
  })
})
