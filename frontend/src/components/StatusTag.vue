<script setup lang="ts">
import type { AnalysisStatus, MeetingStatus } from '@/types/meeting'
import { analysisStatusLabels, meetingStatusLabels } from '@/utils/meeting'

const props = defineProps<{ status: MeetingStatus | AnalysisStatus; type: 'meeting' | 'analysis' }>()

const tagType = (status: string) => {
  if (['published', 'ready', 'succeeded'].includes(status)) return 'success'
  if (['in_progress', 'processing', 'queued'].includes(status)) return 'warning'
  if (['cancelled', 'failed'].includes(status)) return 'danger'
  if (status === 'archived') return 'info'
  return 'primary'
}
</script>

<template>
  <el-tag :type="tagType(props.status)" effect="light" round>
    {{ props.type === 'meeting' ? meetingStatusLabels[props.status as MeetingStatus] : analysisStatusLabels[props.status as AnalysisStatus] }}
  </el-tag>
</template>
