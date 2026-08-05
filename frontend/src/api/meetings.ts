import { http } from '@/api/client'
import type {
  Meeting,
  MeetingListParams,
  MeetingPayload,
  MeetingStatus,
  PaginatedMeetings,
} from '@/types/meeting'

const endpoint = '/api/v1/meetings'

export const meetingsApi = {
  async list(params: MeetingListParams): Promise<PaginatedMeetings> {
    const { data } = await http.get<PaginatedMeetings>(endpoint, { params })
    return data
  },
  async get(id: string): Promise<Meeting> {
    const { data } = await http.get<Meeting>(`${endpoint}/${id}`)
    return data
  },
  async create(payload: MeetingPayload): Promise<Meeting> {
    const { data } = await http.post<Meeting>(endpoint, payload)
    return data
  },
  async update(id: string, payload: Partial<MeetingPayload>): Promise<Meeting> {
    const { data } = await http.patch<Meeting>(`${endpoint}/${id}`, payload)
    return data
  },
  async changeStatus(id: string, meeting_status: MeetingStatus): Promise<Meeting> {
    const { data } = await http.patch<Meeting>(`${endpoint}/${id}/status`, { meeting_status })
    return data
  },
  async remove(id: string): Promise<void> {
    await http.delete(`${endpoint}/${id}`)
  },
}
