import { describe, expect, it } from 'vitest'
import { ApiRequestError, toApiError } from '@/utils/errors'
import { canImport, canSubmitImport, isActiveImportStatus, meetingImportReviewPath, meetingImportStatusLabel, validateImportFile } from '@/utils/meetingImport'

const config = { max_upload_bytes: 10, allowed_extensions: ['.json', '.txt'], allowed_mime_types: ['application/json', 'text/plain'] }

describe('meeting import helpers', () => {
  it('limits import to editable roles and maps status copy', () => {
    expect(canImport('owner')).toBe(true)
    expect(canImport('reviewer')).toBe(false)
    expect(meetingImportStatusLabel('UPLOADED')).toBe('正在安全保存原始文件')
    expect(meetingImportStatusLabel('PARSING')).toBe('正在解析文档结构')
    expect(meetingImportStatusLabel('EXTRACTING_METADATA')).toBe('正在识别会议信息')
    expect(meetingImportStatusLabel('READY_FOR_REVIEW')).toBe('已准备好校对')
    expect(meetingImportStatusLabel('FAILED')).toBe('解析失败')
    expect(isActiveImportStatus('PARSING')).toBe(true)
    expect(isActiveImportStatus('FAILED')).toBe(false)
  })

  it('requires role, knowledge base and file before submit and builds the review route', () => {
    const file = new File(['meeting'], 'meeting.txt', { type: 'text/plain' })
    expect(canSubmitImport('editor', '', file)).toBe(false)
    expect(canSubmitImport('editor', 'kb-1')).toBe(false)
    expect(canSubmitImport('viewer', 'kb-1', file)).toBe(false)
    expect(canSubmitImport('editor', 'kb-1', file)).toBe(true)
    expect(meetingImportReviewPath('import/id')).toBe('/meetings/import/import%2Fid/review')
  })

  it('preserves normalized HTTP details for duplicate and terminal polling errors', () => {
    const error = new ApiRequestError('重复原件', 409, 'duplicate_document', { existing_document_id: 'doc-1' })
    expect(toApiError(error)).toBe(error)
    expect(toApiError(error).status).toBe(409)
    expect(toApiError(error).details).toEqual({ existing_document_id: 'doc-1' })
  })

  it('validates empty, extension, mime, size, duplicate and JSON files', async () => {
    expect(await validateImportFile(new File([], 'empty.json', { type: 'application/json' }), config)).toContain('为空')
    expect(await validateImportFile(new File(['{}'], 'notes.pdf', { type: 'application/pdf' }), config)).toContain('不支持')
    const wrongMime = new File(['{}'], 'notes.json', { type: 'image/jpeg' })
    expect(await validateImportFile(wrongMime, config)).toContain('MIME')
    expect(await validateImportFile(new File(['01234567890'], 'notes.txt', { type: 'text/plain' }), config)).toContain('超过')
    const duplicate = new File(['{}'], 'notes.json', { type: 'application/json', lastModified: 10 })
    expect(await validateImportFile(duplicate, config, duplicate)).toContain('重复')
    expect(await validateImportFile(new File(['{bad'], 'notes.json', { type: 'application/json' }), config)).toContain('JSON')
    expect(await validateImportFile(new File(['{}'], 'notes.json', { type: 'application/json' }), config)).toBeUndefined()
  })
})
