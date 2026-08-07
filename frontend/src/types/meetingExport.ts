import type { RagSource } from '@/types/meetingAnalysis'

export type ExportType = 'text' | 'ppt' | 'chart'
export type ExportFileFormat = 'docx' | 'pdf' | 'pptx' | 'png' | 'svg'

export type ExportStatus =
  | 'PENDING'
  | 'ANALYZING'
  | 'GENERATING'
  | 'RENDERING'
  | 'COMPLETED'
  | 'FAILED'
  | 'CANCELLED'

export interface ExportRecord {
  export_id: string
  meeting_id: string
  analysis_version: number
  export_type: ExportType
  file_format: ExportFileFormat | null
  status: ExportStatus
  progress: number
  current_stage: string
  message: string | null
  error_code: string | null
  error_message: string | null
  file_name: string | null
  download_url: string | null
  config: Record<string, unknown>
  retry_count: number
  created_at: string
  started_at: string | null
  completed_at: string | null
}

export interface TextExportSection {
  key: string
  title: string
  content: string | null
  items: string[]
  citations: number[]
}

export interface TextPreview {
  meeting_id: string
  meeting_title: string
  starts_at: string | null
  ends_at: string | null
  location: string | null
  organizer: string | null
  topic: string | null
  analysis_version: number
  template: string
  include_cover: boolean
  sections: TextExportSection[]
  sources: Array<{ index: number; title: string; snippet: string; type: string }>
}

export interface TextExportConfig {
  format: 'docx' | 'pdf'
  file_name?: string
  include_cover: boolean
  template: 'formal' | 'minimal'
  sections?: string[]
  show_attendee_names: boolean
  include_references: boolean
  include_timestamps: boolean
}

export interface PptBullet {
  text: string
  sourceIds: string[]
}

export interface PptSlide {
  pageNumber: number
  type: string
  title: string
  bullets: PptBullet[]
  chartIds?: string[]
  speakerNotes?: string
}

export interface PptDeckSpec {
  title: string
  subtitle?: string
  theme: 'formal' | 'minimal'
  slides: PptSlide[]
}

export interface PptOutline {
  id: string
  meeting_id: string
  analysis_version: number
  spec: PptDeckSpec
  generated_at: string
}

export interface PptExportConfig {
  file_name?: string
  theme: 'formal' | 'minimal'
  include_charts: boolean
  include_references: boolean
  anonymous_attendees: boolean
  page_count: 'auto' | '6' | '7' | '8'
  title?: string
  report_unit?: string
  presenter?: string
  slides?: PptSlide[]
}

export interface ChartEvidence {
  speakerId?: string | null
  speakerName?: string | null
  sourceId: string
  timestamp?: string | null
  snippet: string
}

export interface ChartCategory {
  key: string
  label: string
  value: number
  percentage?: number | null
  evidence: ChartEvidence[]
}

export interface ChartSpec {
  id: string
  meeting_id: string
  analysis_version: number
  type: 'bar' | 'pie'
  title: string
  subtitle: string
  metric: string
  target_id: string | null
  target_label: string | null
  denominator: { name: string; value: number } | null
  categories: ChartCategory[]
  validation: {
    valid: boolean
    reason?: string | null
    generatedAt: string
  }
  generated_at: string
}

export type ExportRecordList = {
  items: ExportRecord[]
  total: number
  page: number
  page_size: number
}

export function normalizeExportRecord(raw: unknown): ExportRecord | null {
  if (!raw || typeof raw !== 'object') return null
  const value = raw as Record<string, unknown>
  const exportId = String(value.export_id ?? value.exportId ?? '')
  if (!exportId) return null
  return {
    export_id: exportId,
    meeting_id: String(value.meeting_id ?? ''),
    analysis_version: Number(value.analysis_version ?? 0) || 0,
    export_type: String(value.export_type ?? 'text') as ExportType,
    file_format: value.file_format ? (String(value.file_format) as ExportFileFormat) : null,
    status: String(value.status ?? 'PENDING') as ExportStatus,
    progress: Number(value.progress ?? 0) || 0,
    current_stage: String(value.current_stage ?? ''),
    message: typeof value.message === 'string' ? value.message : null,
    error_code: typeof value.error_code === 'string' ? value.error_code : null,
    error_message: typeof value.error_message === 'string' ? value.error_message : null,
    file_name: typeof value.file_name === 'string' ? value.file_name : null,
    download_url: typeof value.download_url === 'string' ? value.download_url : null,
    config: value.config && typeof value.config === 'object' ? (value.config as Record<string, unknown>) : {},
    retry_count: Number(value.retry_count ?? 0) || 0,
    created_at: String(value.created_at ?? ''),
    started_at: typeof value.started_at === 'string' ? value.started_at : null,
    completed_at: typeof value.completed_at === 'string' ? value.completed_at : null,
  }
}

export function normalizeChartSpec(raw: unknown): ChartSpec | null {
  if (!raw || typeof raw !== 'object') return null
  const value = raw as Record<string, unknown>
  const id = String(value.id ?? '')
  if (!id) return null
  return {
    id,
    meeting_id: String(value.meeting_id ?? ''),
    analysis_version: Number(value.analysis_version ?? 0) || 0,
    type: value.type === 'pie' ? 'pie' : 'bar',
    title: String(value.title ?? ''),
    subtitle: String(value.subtitle ?? ''),
    metric: String(value.metric ?? ''),
    target_id: typeof value.target_id === 'string' ? value.target_id : null,
    target_label: typeof value.target_label === 'string' ? value.target_label : null,
    denominator:
      value.denominator && typeof value.denominator === 'object'
        ? (value.denominator as { name: string; value: number })
        : null,
    categories: Array.isArray(value.categories)
      ? value.categories.map((item) => {
          const category = (item ?? {}) as Record<string, unknown>
          return {
            key: String(category.key ?? ''),
            label: String(category.label ?? ''),
            value: Number(category.value ?? 0) || 0,
            percentage: typeof category.percentage === 'number' ? category.percentage : null,
            evidence: Array.isArray(category.evidence)
              ? (category.evidence as ChartEvidence[]).map((evidence) => ({
                  speakerId: typeof evidence.speakerId === 'string' ? evidence.speakerId : null,
                  speakerName: typeof evidence.speakerName === 'string' ? evidence.speakerName : null,
                  sourceId: String(evidence.sourceId ?? ''),
                  timestamp: typeof evidence.timestamp === 'string' ? evidence.timestamp : null,
                  snippet: String(evidence.snippet ?? ''),
                }))
              : [],
          }
        })
      : [],
    validation: {
      valid: (value.validation as Record<string, unknown>)?.valid === true,
      reason: typeof (value.validation as Record<string, unknown>)?.reason === 'string' ? String((value.validation as Record<string, unknown>)?.reason) : null,
      generatedAt: String((value.validation as Record<string, unknown>)?.generatedAt ?? ''),
    },
    generated_at: String(value.generated_at ?? ''),
  }
}

export type { RagSource }
