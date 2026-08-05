<script setup lang="ts">
import { ArrowDown } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import { computed } from 'vue'
import type { Meeting } from '@/types/meeting'
import { uniqueNames } from '@/utils/meetingVerification'
import { normalizeMeetingInfo } from '@/types/meetingVerification'

const props = defineProps<{ meeting: Meeting; expanded?: boolean }>()
const emit = defineEmits<{ toggle: [] }>()
const info = computed(() => normalizeMeetingInfo(props.meeting.meeting_info))
const attendees = computed(() => uniqueNames([info.value.advisor_names, info.value.internal_attendees]))
const dateLabel = computed(() => props.meeting.starts_at ? dayjs(props.meeting.starts_at).format('YYYY-MM-DD') : '未提供')
const startTimeLabel = computed(() => props.meeting.starts_at ? dayjs(props.meeting.starts_at).format('HH:mm') : '未提供')
const endTimeLabel = computed(() => props.meeting.ends_at ? dayjs(props.meeting.ends_at).format('HH:mm') : '未提供')
</script>

<template>
  <el-card class="verification-info-card" shadow="never">
    <button class="panel-toggle" type="button" :aria-expanded="expanded ?? false" @click="emit('toggle')">
      <span><strong>会议基本信息</strong><small>只读信息，来源于导入会议资料</small></span>
      <el-icon :class="{ rotated: expanded }"><ArrowDown /></el-icon>
    </button>
    <div v-if="expanded" class="info-grid">
      <div><label>会议名称</label><span>{{ meeting.title || '未提供' }}</span></div>
      <div><label>日期</label><span>{{ dateLabel }}</span></div>
      <div><label>开始时间</label><span>{{ startTimeLabel }}</span></div>
      <div><label>结束时间</label><span>{{ endTimeLabel }}</span></div>
      <div><label>地点/场次</label><span>{{ meeting.location || '未提供' }}</span></div>
      <div><label>科室/领域</label><span>{{ meeting.topic || '未提供' }}</span></div>
      <div><label>组织方</label><span>{{ meeting.organizer || '未提供' }}</span></div>
      <div><label>参会人数</label><span>{{ attendees.length ? `${attendees.length} 人` : '未提供' }}</span></div>
      <div class="full"><label>参会人员</label><span>{{ attendees.join('、') || '未提供' }}</span></div>
      <div><label>会议目的</label><span>{{ info.meeting_purpose || '未提供' }}</span></div>
      <div><label>讨论主题</label><span>{{ info.discussion_topics || '未提供' }}</span></div>
      <div><label>顾问选择标准</label><span>{{ info.advisor_selection_criteria || '未提供' }}</span></div>
      <div><label>记录人</label><span>{{ info.recorder || '未提供' }}</span></div>
    </div>
  </el-card>
</template>

<style scoped>
.verification-info-card { border: 1px solid var(--line); border-radius: 12px; }
.panel-toggle { width: 100%; display: flex; align-items: center; justify-content: space-between; padding: 0; border: 0; color: #173f58; background: transparent; text-align: left; cursor: pointer; }
.panel-toggle span { display: grid; gap: 5px; }
.panel-toggle small { color: #84949b; font-size: 12px; font-weight: 400; }
.panel-toggle .el-icon { transition: transform .2s; }
.panel-toggle .rotated { transform: rotate(180deg); }
.info-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px 28px; padding-top: 20px; margin-top: 18px; border-top: 1px solid #edf2f0; }
.info-grid div { display: grid; gap: 4px; }
.info-grid .full { grid-column: 1 / -1; }
.info-grid label { color: #8999a0; font-size: 12px; }
.info-grid span { color: #365568; line-height: 1.5; }
@media (max-width: 700px) { .info-grid { grid-template-columns: 1fr; } .info-grid .full { grid-column: auto; } }
</style>
