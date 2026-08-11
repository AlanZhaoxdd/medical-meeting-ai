export type MeetingStatus =
  | 'draft'
  | 'published'
  | 'in_progress'
  | 'completed'
  | 'cancelled'
  | 'archived'

export type AnalysisStatus =
  | 'not_ready'
  | 'ready'
  | 'queued'
  | 'processing'
  | 'succeeded'
  | 'failed'
  | 'cancelled'

export interface Meeting {
  id: string
  import_id?: string | null
  organization_id?: string | null
  knowledge_base_id?: string | null
  title: string
  starts_at: string
  ends_at: string
  location: string | null
  online_url: string | null
  organizer: string | null
  topic: string | null
  description: string | null
  cover_url: string | null
  meeting_status: MeetingStatus
  analysis_status: AnalysisStatus
  created_at: string
  updated_at: string
  verification_status?: VerificationStatus
  verification_version?: number
  verification_confirmed_at?: string | null
  verification_confirmed_by?: string | null
  analysis_requested_at?: string | null
  meeting_info?: MeetingInfo | null
}

export type VerificationStatus = 'pending' | 'in_progress' | 'confirmed'

export interface MeetingInfo {
  meeting_purpose?: string | null
  discussion_topics?: string | null
  advisor_selection_criteria?: string | null
  advisor_names?: string | string[] | null
  internal_attendees?: string | string[] | null
  recorder?: string | null
  [key: string]: unknown
}

export interface MeetingPayload {
  title: string
  starts_at: string
  ends_at: string
  location?: string | null
  online_url?: string | null
  organizer?: string | null
  topic?: string | null
  description?: string | null
  cover_url?: string | null
}

export interface MeetingListParams {
  page?: number
  page_size?: number
  meeting_status?: MeetingStatus
  analysis_status?: AnalysisStatus
  keyword?: string
  starts_at_from?: string
  starts_at_to?: string
}

export interface PaginatedMeetings {
  items: Meeting[]
  page: number
  page_size: number
  total: number
  total_pages: number
}

export interface ApiErrorBody {
  code: string
  message: string
  details?: unknown
}
