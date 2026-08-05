export type Role = 'owner' | 'admin' | 'editor' | 'reviewer' | 'viewer'
export type DocumentStatus =
  | 'UPLOADED'
  | 'PARSING'
  | 'PARSED'
  | 'CHUNKING'
  | 'EMBEDDING'
  | 'EXTRACTING'
  | 'AWAITING_REVIEW'
  | 'IN_REVIEW'
  | 'PUBLISHED'
  | 'FAILED'
  | 'DELETED'
export type ReviewStatus = 'PENDING' | 'APPROVED' | 'REJECTED' | 'NEEDS_CHANGES'

export interface CurrentUser {
  id: string
  email: string
  display_name: string
  organization_id: string
  role: Role
}

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: 'bearer'
  expires_in: number
}

export interface KnowledgeBase {
  id: string
  organization_id: string
  name: string
  description: string
  default_template_id?: string
  status: string
  document_count: number
  published_knowledge_count: number
  created_by: string
  created_at: string
  updated_at: string
}

export interface ExtractionTemplate {
  id: string
  knowledge_base_id: string
  name: string
  description: string
  fields: string[]
  version: number
  created_at: string
}

export interface KbDocument {
  id: string
  knowledge_base_id: string
  meeting_id?: string
  filename: string
  safe_filename: string
  mime_type: string
  source_type: string
  sha256: string
  version: number
  previous_version_id?: string
  template_id: string
  template_version: number
  status: DocumentStatus
  vector_sync_status: string
  error_code?: string
  error_message?: string
  created_at: string
  updated_at: string
  published_at?: string
}

export interface UploadResponse {
  document: KbDocument
  job_id?: string
  duplicate: boolean
}

export interface Job {
  job_id: string
  document_id: string
  status: string
  current_node: string
  progress: number
  error_code?: string
  error_message?: string
  result_summary: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface SourceRef {
  block_id?: string
  chunk_id?: string
  page_number?: number
  slide_number?: number
  speaker?: string
  start_ms?: number
  end_ms?: number
  quote: string
}

export interface KnowledgeItem {
  id: string
  document_id: string
  item_type: string
  title: string
  normalized_content: string
  structured_data: Record<string, unknown>
  source_refs: SourceRef[]
  confidence: number
  review_status: ReviewStatus
  review_comment?: string
  reviewer_id?: string
  publication_status: string
  revision: number
  updated_at: string
}

export interface SearchResult {
  chunk_id: string
  content: string
  dense_score: number
  sparse_score: number
  fused_score: number
  rerank_score: number
  document_id: string
  filename: string
  document_version: number
  content_type: string
  page_number?: number
  slide_number?: number
  speaker?: string
  time_range?: { start_ms: number; end_ms: number }
  publication_status: string
  source_locator: Record<string, unknown>
}
