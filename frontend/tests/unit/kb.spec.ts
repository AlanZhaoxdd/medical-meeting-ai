import { describe, expect, it } from 'vitest'
import { canAccessMeetingWorkspace, canAccessSettings, canEditKb, canIncludeDrafts, canManageKb, roleLabel } from '@/utils/kb'

describe('KB permissions', () => {
  it('separates IT settings access from minutes editing', () => {
    expect(canAccessSettings('owner')).toBe(false)
    expect(canAccessSettings('admin')).toBe(true)
    expect(canAccessMeetingWorkspace('owner')).toBe(true)
    expect(canAccessMeetingWorkspace('admin')).toBe(false)
    expect(canManageKb('owner')).toBe(false)
    expect(canManageKb('admin')).toBe(true)
    expect(canManageKb('editor')).toBe(false)
    expect(canEditKb('editor')).toBe(true)
    expect(canIncludeDrafts('viewer')).toBe(false)
  })

  it('shows business-facing role labels', () => {
    expect(roleLabel('owner')).toBe('纪要编辑员')
    expect(roleLabel('admin')).toBe('IT管理员')
  })
})
