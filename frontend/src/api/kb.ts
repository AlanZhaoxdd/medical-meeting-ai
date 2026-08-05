import { http } from '@/api/client'
import type {
  ExtractionTemplate,
  Job,
  KbDocument,
  KnowledgeBase,
  KnowledgeItem,
  ReviewStatus,
  SearchResult,
  UploadResponse,
} from '@/types/kb'

export const kbApi = {
  list() {
    return http.get<KnowledgeBase[]>('/api/v1/knowledge-bases').then((response) => response.data)
  },
  get(kbId: string) {
    return http.get<KnowledgeBase>(`/api/v1/knowledge-bases/${kbId}`).then((response) => response.data)
  },
  create(payload: { name: string; description: string }) {
    return http.post<KnowledgeBase>('/api/v1/knowledge-bases', payload).then((response) => response.data)
  },
  update(kbId: string, payload: Partial<Pick<KnowledgeBase, 'name' | 'description' | 'status' | 'default_template_id'>>) {
    return http.patch<KnowledgeBase>(`/api/v1/knowledge-bases/${kbId}`, payload).then((response) => response.data)
  },
  remove(kbId: string) {
    return http.delete(`/api/v1/knowledge-bases/${kbId}`)
  },
  templates(kbId: string) {
    return http.get<ExtractionTemplate[]>(`/api/v1/knowledge-bases/${kbId}/templates`).then((response) => response.data)
  },
  createTemplate(kbId: string, payload: { name: string; description: string; fields: string[] }) {
    return http.post<ExtractionTemplate>(`/api/v1/knowledge-bases/${kbId}/templates`, payload).then((response) => response.data)
  },
  documents(kbId: string) {
    return http.get<KbDocument[]>(`/api/v1/knowledge-bases/${kbId}/documents`).then((response) => response.data)
  },
  document(kbId: string, documentId: string) {
    return http.get<KbDocument>(`/api/v1/knowledge-bases/${kbId}/documents/${documentId}`).then((response) => response.data)
  },
  upload(kbId: string, form: FormData, onProgress?: (percent: number) => void) {
    return http
      .post<UploadResponse>(`/api/v1/knowledge-bases/${kbId}/documents`, form, {
        timeout: 120_000,
        onUploadProgress: (event) => {
          if (event.total) onProgress?.(Math.round((event.loaded / event.total) * 100))
        },
      })
      .then((response) => response.data)
  },
  blocks(kbId: string, documentId: string) {
    return http.get<Record<string, unknown>[]>(`/api/v1/knowledge-bases/${kbId}/documents/${documentId}/blocks`).then((response) => response.data)
  },
  chunks(kbId: string, documentId: string) {
    return http.get<Record<string, unknown>[]>(`/api/v1/knowledge-bases/${kbId}/documents/${documentId}/chunks`).then((response) => response.data)
  },
  retry(kbId: string, documentId: string) {
    return http.post<Job>(`/api/v1/knowledge-bases/${kbId}/documents/${documentId}/retry`).then((response) => response.data)
  },
  reindex(kbId: string, documentId: string) {
    return http.post<Job>(`/api/v1/knowledge-bases/${kbId}/documents/${documentId}/reindex`).then((response) => response.data)
  },
  removeDocument(kbId: string, documentId: string) {
    return http.delete(`/api/v1/knowledge-bases/${kbId}/documents/${documentId}`)
  },
  job(jobId: string) {
    return http.get<Job>(`/api/v1/jobs/${jobId}`).then((response) => response.data)
  },
  knowledgeItems(kbId: string, params: { document_id?: string; item_type?: string; review_status?: ReviewStatus } = {}) {
    return http.get<KnowledgeItem[]>(`/api/v1/knowledge-bases/${kbId}/knowledge-items`, { params }).then((response) => response.data)
  },
  updateKnowledgeItem(kbId: string, itemId: string, payload: Partial<KnowledgeItem>) {
    return http.patch<KnowledgeItem>(`/api/v1/knowledge-bases/${kbId}/knowledge-items/${itemId}`, payload).then((response) => response.data)
  },
  review(kbId: string, itemId: string, status: ReviewStatus, comment = '') {
    return http.post<KnowledgeItem>(`/api/v1/knowledge-bases/${kbId}/knowledge-items/${itemId}/review`, { status, comment }).then((response) => response.data)
  },
  publish(kbId: string, documentId: string) {
    return http.post(`/api/v1/knowledge-bases/${kbId}/documents/${documentId}/publish`).then((response) => response.data)
  },
  search(kbId: string, payload: {
    query: string
    top_k: number
    content_types: string[]
    meeting_ids: string[]
    document_ids: string[]
    include_drafts: boolean
  }) {
    return http
      .post<{ items: SearchResult[]; took_ms: number }>(
        `/api/v1/knowledge-bases/${kbId}/search`,
        payload,
        { timeout: 120_000 },
      )
      .then((response) => response.data)
  },
}
