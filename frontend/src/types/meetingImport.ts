export type MeetingImportStatus =
  | 'UPLOADING'
  | 'UPLOADED'
  | 'VALIDATING'
  | 'PARSING'
  | 'EXTRACTING_METADATA'
  | 'READY_FOR_REVIEW'
  | 'FAILED'
  | 'CANCELLED'
  | 'CANCELED'
  | (string & {})

export interface MeetingImportConfig {
  max_upload_bytes: number
  allowed_extensions: string[]
  allowed_mime_types: string[]
  mime_types?: Record<string, string[]>
}

export interface MeetingImportRead {
  id: string
  import_id?: string
  knowledge_base_id: string
  document_id?: string
  filename?: string
  status: MeetingImportStatus
  progress?: number
  progress_percent?: number
  current_step?: string
  error_code?: string
  error_message?: string
  can_retry?: boolean
  created_at?: string
  updated_at?: string
  [key: string]: unknown
}

export type ReviewBlockType = 'paragraph' | 'speaker' | 'heading' | 'table' | 'unknown' | (string & {})

export interface ReviewBlock {
  id: string
  type?: ReviewBlockType
  block_type?: ReviewBlockType
  text: string
  table_markdown?: string | null
  speaker?: string | null
  start_ms?: number | null
  end_ms?: number | null
  page_number?: number | null
  paragraph_number?: number | null
  source?: Record<string, unknown> | null
  [key: string]: unknown
}

export interface ReviewMetadataField {
  value: string | null
  suggested_value?: string | null
  confidence?: number | null
  confidence_label?: string | null
  source?: { block_id?: string; quote?: string; page_number?: number; [key: string]: unknown } | Array<{ block_id?: string; quote?: string; page_number?: number; [key: string]: unknown }> | null
  needs_confirmation?: boolean
  user_modified?: boolean
}

export interface ReviewMetadata {
  title: ReviewMetadataField
  starts_at: ReviewMetadataField
  ends_at: ReviewMetadataField
  location: ReviewMetadataField
  online_url: ReviewMetadataField
  organizer: ReviewMetadataField
  topic: ReviewMetadataField
  description: ReviewMetadataField
  meeting_purpose?: ReviewMetadataField
  discussion_topics?: ReviewMetadataField
  meeting_date?: ReviewMetadataField
  advisor_selection_criteria?: ReviewMetadataField
  advisor_names?: ReviewMetadataField
  internal_attendees?: ReviewMetadataField
  recorder?: ReviewMetadataField
  [key: string]: ReviewMetadataField
}

export interface ReviewRevision {
  id: string
  revision_number: number
  status?: string
  version: number
  blocks: ReviewBlock[]
  created_at?: string
  updated_at?: string
  created_by?: string
  creator?: string
  [key: string]: unknown
}

export type MeetingImportVectorizationStatus = 'PENDING' | 'RUNNING' | 'SYNCED' | 'STALE' | 'FAILED' | (string & {})

export interface MeetingImportVectorization {
  status: MeetingImportVectorizationStatus
  job_id?: string | null
  current_node?: string | null
  progress?: number | null
  error_code?: string | null
  error_message?: string | null
  retryable?: boolean
  current_revision_version?: number | null
  vectorized_revision_version?: number | null
  [key: string]: unknown
}

export interface MeetingImportReview {
  import: MeetingImportRead
  file?: { filename?: string; [key: string]: unknown }
  status: MeetingImportStatus | string
  document?: Record<string, unknown>
  knowledge_base?: Record<string, unknown>
  meeting_id?: string | null
  original_blocks: ReviewBlock[]
  current_revision: ReviewRevision | null
  revisions: ReviewRevision[]
  meeting_metadata: ReviewMetadata
  metadata_version: number
  needs_confirmation_count: number
  vectorization: MeetingImportVectorization
  [key: string]: unknown
}

export interface DuplicateImportDetails {
  existing_document_id?: string
  existing_import_id?: string
  [key: string]: unknown
}
