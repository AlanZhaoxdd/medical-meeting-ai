import { http } from '@/api/client'
import type { MeetingImportConfig, MeetingImportRead, MeetingImportReview, MeetingImportVectorization, ReviewBlock, ReviewMetadata, ReviewRevision } from '@/types/meetingImport'
import { cleanTranscriptText } from '@/utils/meetingReview'

function normalizeConfig(config: MeetingImportConfig & { mime_types?: Record<string, string[]> }) {
  const configured = config.allowed_mime_types as string[] | Record<string, string[]> | undefined
  const mimeTypes = Array.isArray(configured)
    ? configured
    : Object.values(configured || config.mime_types || {}).flat()
  return { ...config, allowed_mime_types: [...new Set(mimeTypes)] }
}

function normalizeImport(record: MeetingImportRead): MeetingImportRead {
  const failure = record.failure as { message?: string; displayable?: string } | undefined
  const file = record.file as { filename?: string } | undefined
  return {
    ...record,
    id: record.id || record.import_id,
    filename: record.filename || file?.filename,
    progress: record.progress ?? record.progress_percent,
    error_message: record.error_message || failure?.displayable || failure?.message,
  }
}

// Backend versions expose a few untyped aliases; this boundary is intentionally permissive.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const asRecord = (value: unknown): Record<string, any> => (value && typeof value === 'object' ? value as Record<string, any> : {})
const asBlocks = (value: unknown): ReviewBlock[] => (Array.isArray(value) ? value : []).map((item, index) => {
  const block = asRecord(item)
  const source = asRecord(block.source ?? block.source_ref)
  const type = block.type || block.block_type
  const rawText = type === 'table' && block.table_markdown ? block.table_markdown : block.text ?? block.content ?? block.transcript ?? ''
  const text = cleanTranscriptText(rawText, type)
  return { ...block, id: String(block.id || block.block_id || `block-${index + 1}`), type, text, table_markdown: type === 'table' ? text : block.table_markdown, speaker: block.speaker ?? source.speaker, start_ms: block.start_ms ?? source.start_ms, end_ms: block.end_ms ?? source.end_ms, page_number: block.page_number ?? source.page_number, source: Object.keys(source).length ? source : null }
})

function normalizeMetadata(value: unknown): ReviewMetadata {
  const source = asRecord(value)
  const fields = ['title', 'starts_at', 'ends_at', 'location', 'online_url', 'organizer', 'topic', 'description']
  const metadata = {} as ReviewMetadata
  for (const key of fields) {
    const raw = source[key]
    const item = raw && typeof raw === 'object' ? asRecord(raw) : { value: raw }
    metadata[key] = { value: item.value == null ? null : String(item.value), ...item }
  }
  for (const [key, raw] of Object.entries(source)) if (!(key in metadata)) {
    const item = raw && typeof raw === 'object' ? asRecord(raw) : { value: raw }
    metadata[key] = { value: item.value == null ? null : String(item.value), ...item }
  }
  return metadata
}

const normalizeRevision = (value: unknown, fallbackId: string, fallbackVersion = 1): ReviewRevision => {
  const revision = asRecord(value)
  return {
    ...revision,
    id: String(revision.id || revision.revision_id || fallbackId),
    revision_number: Number(revision.revision_number ?? revision.number ?? 1),
    version: Number(revision.version ?? revision.current_version ?? fallbackVersion),
    blocks: asBlocks(revision.blocks ?? revision.content ?? revision.block_edits),
  }
}

export function normalizeVectorization(value: unknown): MeetingImportVectorization {
  const vectorization = asRecord(value)
  const progress = vectorization.progress ?? vectorization.progress_percent
  const currentVersion = vectorization.current_revision_version ?? vectorization.current_version
  const vectorizedVersion = vectorization.vectorized_revision_version ?? vectorization.vectorized_version
  return {
    ...vectorization,
    status: String(vectorization.status ?? 'PENDING').toUpperCase(),
    job_id: vectorization.job_id == null ? null : String(vectorization.job_id),
    current_node: vectorization.current_node == null ? null : String(vectorization.current_node),
    progress: progress == null ? null : Number(progress),
    error_code: vectorization.error_code == null ? null : String(vectorization.error_code),
    error_message: vectorization.error_message == null ? null : String(vectorization.error_message),
    retryable: vectorization.retryable == null ? undefined : Boolean(vectorization.retryable),
    current_revision_version: currentVersion == null ? null : Number(currentVersion),
    vectorized_revision_version: vectorizedVersion == null ? null : Number(vectorizedVersion),
  }
}

export function normalizeReview(value: unknown): MeetingImportReview {
  const raw = asRecord(value)
  const importRaw = asRecord(raw.import ?? raw.meeting_import ?? raw)
  const currentRaw = raw.current_revision ?? raw.currentRevision ?? raw.revision
  const current = currentRaw ? normalizeRevision(currentRaw, 'current-revision', 1) : null
  const revisions = (Array.isArray(raw.revisions) ? raw.revisions : Array.isArray(raw.revision_history) ? raw.revision_history : Array.isArray(raw.history) ? raw.history : []).map((item, index) => normalizeRevision(item, `revision-${index + 1}`, current?.version ?? 1))
  const originalBlocks = asBlocks(raw.original_blocks ?? raw.originalBlocks ?? asRecord(raw.document).blocks ?? raw.blocks)
  const metadataRaw = raw.meeting_metadata ?? raw.meetingMetadata ?? raw.metadata ?? {}
  return {
    ...raw,
    import: normalizeImport({ ...importRaw, id: String(importRaw.id || importRaw.import_id || raw.import_id), knowledge_base_id: String(importRaw.knowledge_base_id || importRaw.kb_id || raw.knowledge_base_id || '') } as MeetingImportRead),
    file: raw.file,
    status: String(raw.status ?? importRaw.status ?? 'UNKNOWN'),
    original_blocks: originalBlocks,
    current_revision: current,
    revisions,
    meeting_metadata: normalizeMetadata(metadataRaw),
    metadata_version: Number(raw.metadata_version ?? raw.metadataVersion ?? 1),
    needs_confirmation_count: Number(raw.needs_confirmation_count ?? raw.needsConfirmationCount ?? 0),
    vectorization: normalizeVectorization(raw.vectorization ?? raw.vector_sync ?? raw.vectorization_status),
  }
}

export const meetingImportsApi = {
  config() {
    return http.get<MeetingImportConfig & { mime_types?: Record<string, string[]> }>('/api/v1/meeting-imports/config').then((response) => normalizeConfig(response.data))
  },
  create(payload: { knowledgeBaseId: string; file?: File; documentId?: string; confirmDuplicate?: boolean }, onProgress?: (percent: number) => void) {
    const form = new FormData()
    form.append('knowledge_base_id', payload.knowledgeBaseId)
    // `kb_id` is retained for compatibility with deployed API revisions.
    form.append('kb_id', payload.knowledgeBaseId)
    if (payload.file) form.append('file', payload.file)
    if (payload.documentId) form.append('document_id', payload.documentId)
    if (payload.confirmDuplicate !== undefined) form.append('confirm_duplicate', String(payload.confirmDuplicate))
    return http.post<MeetingImportRead>('/api/v1/meeting-imports', form, {
      timeout: 120_000,
      onUploadProgress: (event) => {
        if (event.total) onProgress?.(Math.round((event.loaded / event.total) * 100))
      },
    }).then((response) => normalizeImport(response.data))
  },
  get(importId: string) {
    return http.get<MeetingImportRead>(`/api/v1/meeting-imports/${importId}`).then((response) => normalizeImport(response.data))
  },
  review(importId: string) {
    return http.get<unknown>(`/api/v1/meeting-imports/${importId}/review`).then((response) => normalizeReview(response.data))
  },
  reopen(importId: string) {
    return http.post<unknown>(`/api/v1/meeting-imports/${importId}/reopen`).then((response) => normalizeReview(response.data))
  },
  vectorization(importId: string) {
    return http.get<unknown>(`/api/v1/meeting-imports/${importId}/vectorization`).then((response) => normalizeVectorization(response.data))
  },
  vectorize(importId: string, payload: { expected_version: number }) {
    return http.post<unknown>(`/api/v1/meeting-imports/${importId}/vectorize`, payload).then((response) => {
      const value = asRecord(response.data)
      return normalizeVectorization(value.vectorization ?? value)
    })
  },
  revision(importId: string, revisionId: string) {
    return http.get<unknown>(`/api/v1/meeting-imports/${importId}/revisions/${revisionId}`).then((response) => normalizeRevision(response.data, revisionId))
  },
  patchRevision(importId: string, revisionId: string, payload: { expected_version: number; block_edits: Array<{ block_id: string; text: string; table_markdown?: string }> }) {
    return http.patch<unknown>(`/api/v1/meeting-imports/${importId}/revisions/${revisionId}`, payload).then((response) => normalizeRevision(response.data, revisionId))
  },
  find(importId: string, payload: { query: string; scope: 'FULL' | 'BLOCK'; block_id?: string; case_sensitive?: boolean; revision_id?: string }) {
    return http.post<unknown>(`/api/v1/meeting-imports/${importId}/find`, payload).then((response) => response.data)
  },
  replace(importId: string, payload: { query: string; replacement: string; scope: 'FULL' | 'BLOCK'; block_id?: string; match_index?: number; case_sensitive?: boolean; expected_version: number; mode: 'CURRENT' | 'ALL' }) {
    return http.post<unknown>(`/api/v1/meeting-imports/${importId}/replace`, payload).then((response) => {
      const value = asRecord(response.data)
      return { ...value, replacement_count: Number(value.replacement_count ?? value.count ?? 0), new_version: Number(value.new_version ?? value.version ?? 1), affected_blocks: (value.affected_blocks ?? value.affected_block_ids) as string[] | undefined, operation_id: String(value.operation_id || '') }
    })
  },
  undoReplace(importId: string, operationId: string, payload: { expected_version: number }) {
    return http.post<unknown>(`/api/v1/meeting-imports/${importId}/replace/${operationId}/undo`, payload).then((response) => {
      const value = asRecord(response.data)
      return { ...value, new_version: Number(value.new_version ?? value.version ?? 1) }
    })
  },
  patchMetadata(importId: string, payload: { expected_version: number; title?: string | null; starts_at?: string | null; ends_at?: string | null; location?: string | null; online_url?: string | null; organizer?: string | null; topic?: string | null; description?: string | null; meeting_purpose?: string | null; discussion_topics?: string | null; meeting_date?: string | null; advisor_selection_criteria?: string | null; advisor_names?: string | null; internal_attendees?: string | null; recorder?: string | null }) {
    return http.patch<unknown>(`/api/v1/meeting-imports/${importId}/metadata`, payload).then((response) => response.data as { metadata?: Record<string, unknown>; metadata_version?: number })
  },
  confirm(importId: string, payload: { expected_version: number; expected_metadata_version: number; title?: string | null; starts_at?: string | null; ends_at?: string | null; location?: string | null; online_url?: string | null; organizer?: string | null; topic?: string | null; description?: string | null; meeting_purpose?: string | null; discussion_topics?: string | null; meeting_date?: string | null; advisor_selection_criteria?: string | null; advisor_names?: string | null; internal_attendees?: string | null; recorder?: string | null }, idempotencyKey: string) {
    return http.post<unknown>(`/api/v1/meeting-imports/${importId}/confirm`, payload, { headers: { 'Idempotency-Key': idempotencyKey } }).then((response) => response.data as { meeting_id: string; rag_job_id?: string; rag_status?: string; rag_error?: string | null; rag_retryable?: boolean; ai_task_id?: string | null; question_generation_status?: string | null; meeting_status?: string })
  },
  retry(importId: string) {
    return http.post<MeetingImportRead>(`/api/v1/meeting-imports/${importId}/retry`).then((response) => normalizeImport(response.data))
  },
  cancel(importId: string) {
    return http.post<MeetingImportRead>(`/api/v1/meeting-imports/${importId}/cancel`).then((response) => normalizeImport(response.data))
  },
}
