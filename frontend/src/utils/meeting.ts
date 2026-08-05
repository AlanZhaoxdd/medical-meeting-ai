import dayjs from 'dayjs'
import type { AnalysisStatus, MeetingStatus } from '@/types/meeting'

export const meetingStatusLabels: Record<MeetingStatus, string> = {
  draft: '草稿',
  published: '已发布',
  in_progress: '进行中',
  completed: '已完成',
  cancelled: '已取消',
  archived: '已归档',
}

export const analysisStatusLabels: Record<AnalysisStatus, string> = {
  not_ready: '未就绪',
  ready: '待分析',
  queued: '已排队',
  processing: '分析中',
  succeeded: '分析完成',
  failed: '分析失败',
  cancelled: '已取消',
}

const transitionMap: Record<MeetingStatus, MeetingStatus[]> = {
  draft: ['published', 'cancelled'],
  published: ['in_progress', 'cancelled'],
  in_progress: ['completed', 'cancelled'],
  completed: ['archived'],
  cancelled: [],
  archived: [],
}

export const getNextStatuses = (status: MeetingStatus): MeetingStatus[] => transitionMap[status]

export const isTerminalStatus = (status: MeetingStatus) =>
  status === 'cancelled' || status === 'archived'

export const formatDateTime = (value?: string | null) =>
  value ? dayjs(value).format('YYYY-MM-DD HH:mm') : '—'

export const toIsoString = (value: Date | string) => dayjs(value).toISOString()

export const toDate = (value?: string | null) => (value ? dayjs(value).toDate() : undefined)

export const isValidTimeRange = (startsAt?: Date, endsAt?: Date) =>
  Boolean(startsAt && endsAt && endsAt > startsAt)
