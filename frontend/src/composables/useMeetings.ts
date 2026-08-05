import { reactive, ref } from 'vue'
import { meetingsApi } from '@/api/meetings'
import { serializeMeetingListParams } from '@/api/params'
import type { MeetingListParams, PaginatedMeetings } from '@/types/meeting'

export const useMeetings = () => {
  const loading = ref(false)
  const result = ref<PaginatedMeetings>({ items: [], page: 1, page_size: 20, total: 0, total_pages: 0 })
  const filters = reactive<MeetingListParams>({ page: 1, page_size: 20, keyword: '' })

  const fetchMeetings = async () => {
    loading.value = true
    try {
      result.value = await meetingsApi.list(serializeMeetingListParams(filters))
    } finally {
      loading.value = false
    }
  }

  const resetFilters = () => {
    Object.assign(filters, { page: 1, page_size: 20, keyword: '', meeting_status: undefined, analysis_status: undefined, starts_at_from: undefined, starts_at_to: undefined })
  }

  return { loading, result, filters, fetchMeetings, resetFilters }
}
