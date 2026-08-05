import { describe, expect, it } from 'vitest'
import { serializeMeetingListParams } from '@/api/params'
import { toApiError } from '@/utils/errors'
import { getNextStatuses, isTerminalStatus, isValidTimeRange, toIsoString } from '@/utils/meeting'

describe('meeting workflow helpers', () => {
  it('returns only allowed next statuses', () => {
    expect(getNextStatuses('draft')).toEqual(['published', 'cancelled'])
    expect(getNextStatuses('completed')).toEqual(['archived'])
    expect(getNextStatuses('archived')).toEqual([])
    expect(isTerminalStatus('cancelled')).toBe(true)
    expect(isTerminalStatus('in_progress')).toBe(false)
  })

  it('serializes date values as timezone-aware ISO strings', () => {
    expect(toIsoString(new Date('2026-08-01T09:00:00+08:00'))).toMatch(/Z$/)
    expect(isValidTimeRange(new Date('2026-08-01T09:00:00'), new Date('2026-08-01T10:00:00'))).toBe(true)
    expect(isValidTimeRange(new Date('2026-08-01T09:00:00'), new Date('2026-08-01T08:00:00'))).toBe(false)
  })
})

describe('list query serialization', () => {
  it('omits empty filters but preserves pagination and values', () => {
    expect(serializeMeetingListParams({ page: 1, page_size: 20, keyword: '', meeting_status: 'draft' })).toEqual({ page: 1, page_size: 20, meeting_status: 'draft' })
  })
})

describe('API error parser', () => {
  it('returns a friendly message for unknown request errors', () => {
    expect(toApiError(new Error('boom')).message).toBe('请求失败，请稍后重试。')
  })
})
