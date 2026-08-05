import type { MeetingListParams } from '@/types/meeting'

/** 移除空筛选条件，避免将空字符串作为后端枚举或时间参数传递。 */
export const serializeMeetingListParams = (params: MeetingListParams): MeetingListParams =>
  Object.fromEntries(
    Object.entries(params).filter(([, value]) => value !== '' && value !== undefined),
  ) as MeetingListParams
