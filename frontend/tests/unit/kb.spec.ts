import { describe, expect, it } from 'vitest'
import { canEditKb, canIncludeDrafts, canManageKb, canPublishDocument } from '@/utils/kb'

describe('KB permissions', () => {
  it('keeps draft retrieval and management role-aware', () => {
    expect(canManageKb('owner')).toBe(true)
    expect(canManageKb('editor')).toBe(false)
    expect(canEditKb('editor')).toBe(true)
    expect(canIncludeDrafts('viewer')).toBe(false)
  })
})

describe('publication gate', () => {
  it('requires synced vectors and resolved review items', () => {
    const document = {
      id: 'd1',
      status: 'IN_REVIEW',
      vector_sync_status: 'SYNCED',
    } as Parameters<typeof canPublishDocument>[0]
    const approved = [{ document_id: 'd1', review_status: 'APPROVED' }] as Parameters<
      typeof canPublishDocument
    >[1]
    expect(canPublishDocument(document, approved)).toBe(true)
    expect(
      canPublishDocument(document, [
        { document_id: 'd1', review_status: 'PENDING' },
      ] as Parameters<typeof canPublishDocument>[1]),
    ).toBe(false)
  })
})
