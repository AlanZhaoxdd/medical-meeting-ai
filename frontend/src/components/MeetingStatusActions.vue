<script setup lang="ts">
import { ElMessageBox } from 'element-plus'
import type { MeetingStatus } from '@/types/meeting'
import { getNextStatuses, meetingStatusLabels } from '@/utils/meeting'

const props = defineProps<{ status: MeetingStatus; loading?: boolean }>()
const emit = defineEmits<{ change: [status: MeetingStatus] }>()

const actionLabels: Partial<Record<MeetingStatus, string>> = {
  published: '发布',
  in_progress: '开始',
  completed: '完成',
  archived: '归档',
  cancelled: '取消',
}

const confirmChange = async (target: MeetingStatus) => {
  try {
    await ElMessageBox.confirm(
      `确认将会议状态更新为「${meetingStatusLabels[target]}」吗？`,
      '确认状态流转',
      { confirmButtonText: '确认更新', cancelButtonText: '取消', type: target === 'cancelled' ? 'warning' : 'info' },
    )
    emit('change', target)
  } catch {
    // 用户取消确认时无需提示。
  }
}
</script>

<template>
  <div v-if="getNextStatuses(props.status).length" class="status-actions">
    <el-button v-for="target in getNextStatuses(props.status)" :key="target" size="small" :type="target === 'cancelled' ? 'danger' : 'primary'" :loading="props.loading" @click="confirmChange(target)">
      {{ actionLabels[target] || meetingStatusLabels[target] }}
    </el-button>
  </div>
</template>

<style scoped>
.status-actions { display: flex; align-items: center; gap: 8px; }
.status-actions .el-button + .el-button { margin-left: 0; }
</style>
