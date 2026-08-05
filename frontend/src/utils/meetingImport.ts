import type { MeetingImportConfig, MeetingImportStatus } from '@/types/meetingImport'

export const editableRoles = ['owner', 'admin', 'editor'] as const

export function canImport(role?: string) {
  return editableRoles.includes(role as (typeof editableRoles)[number])
}

export const meetingImportStatusLabels: Record<string, string> = {
  UPLOADING: '正在安全保存原始文件',
  UPLOADED: '正在安全保存原始文件',
  VALIDATING: '正在校验原始文件',
  PARSING: '正在解析文档结构',
  EXTRACTING_METADATA: '正在识别会议信息',
  READY_FOR_REVIEW: '已准备好校对',
  FAILED: '解析失败',
  CANCELLED: '已取消',
  CANCELED: '已取消',
}

export function meetingImportStatusLabel(status?: MeetingImportStatus) {
  return (status && meetingImportStatusLabels[status]) || status || '等待提交'
}

export function isActiveImportStatus(status?: MeetingImportStatus) {
  return Boolean(status) && !['READY_FOR_REVIEW', 'FAILED', 'CANCELLED', 'CANCELED'].includes(status as string)
}

export function canSubmitImport(role: string | undefined, knowledgeBaseId: string, file?: File) {
  return canImport(role) && Boolean(knowledgeBaseId) && Boolean(file)
}

export function meetingImportReviewPath(importId: string) {
  return `/meetings/import/${encodeURIComponent(importId)}/review`
}

export function normalizeExtension(name: string) {
  const index = name.lastIndexOf('.')
  return index >= 0 ? name.slice(index).toLowerCase() : ''
}

export async function validateImportFile(file: File, config: MeetingImportConfig, previous?: File): Promise<string | undefined> {
  if (!file || file.size === 0) return '文件为空，请选择有效原件。'
  if (config.max_upload_bytes > 0 && file.size > config.max_upload_bytes) return `文件超过允许大小（${formatBytes(config.max_upload_bytes)}）。`
  const extension = normalizeExtension(file.name)
  const extensions = config.allowed_extensions.map((item) => item.startsWith('.') ? item.toLowerCase() : `.${item.toLowerCase()}`)
  if (extensions.length && !extensions.includes(extension)) return `不支持 ${extension || '该'} 文件格式。`
  if (config.allowed_mime_types.length && !config.allowed_mime_types.includes(file.type)) return '文件 MIME 类型不被允许。'
  if (previous && previous.name === file.name && previous.size === file.size && previous.lastModified === file.lastModified) return '该文件已选择，请勿重复添加。'
  if (extension === '.json') {
    try {
      JSON.parse(await file.text())
    } catch {
      return '逐字稿 JSON 格式无效，请修正后重试。'
    }
  }
  return undefined
}

export function formatBytes(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
