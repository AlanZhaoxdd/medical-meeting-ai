<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import MeetingStatusActions from '@/components/MeetingStatusActions.vue'
import StatusTag from '@/components/StatusTag.vue'
import { meetingsApi } from '@/api/meetings'
import type { Meeting, MeetingStatus } from '@/types/meeting'
import { toApiError } from '@/utils/errors'
import { formatDateTime, isTerminalStatus } from '@/utils/meeting'

const route = useRoute()
const router = useRouter()
const id = computed(() => route.params.id as string)
const meeting = ref<Meeting>()
const loading = ref(true)
const actionLoading = ref(false)
const load = async () => {
  loading.value = true
  try { meeting.value = await meetingsApi.get(id.value) }
  catch (error) { ElMessage.error(toApiError(error).message); await router.replace({ name: 'meeting-review' }) }
  finally { loading.value = false }
}
const changeStatus = async (status: MeetingStatus) => {
  actionLoading.value = true
  try { meeting.value = await meetingsApi.changeStatus(id.value, status); ElMessage.success('会议状态已更新') }
  catch (error) { ElMessage.error(toApiError(error).message); await load() }
  finally { actionLoading.value = false }
}
const remove = async () => {
  if (!meeting.value) return
  try {
    await ElMessageBox.confirm(
      `确定要删除「${meeting.value.title || '当前会议'}」吗？删除后，该会议将不再显示在会议列表中。`,
      '删除会议',
      { type: 'warning', confirmButtonText: '删除会议', cancelButtonText: '保留会议' },
    )
    await meetingsApi.remove(id.value); ElMessage.success('会议已删除'); await router.replace({ name: 'meeting-review' })
  } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error(toApiError(error).message) }
}
onMounted(load)
</script>

<template>
  <section>
    <div class="page-header"><div><el-button link @click="router.push({ name: 'meeting-review' })">← 返回会议核验</el-button><h1 class="page-title">会议详情</h1><p class="page-subtitle">查看会议资料与当前业务流程状态。</p></div><div v-if="meeting" class="header-actions"><el-button :disabled="isTerminalStatus(meeting.meeting_status)" @click="router.push({ name: 'meeting-review-detail', params: { meetingId: id } })">进入核验</el-button><el-button type="danger" plain @click="remove">删除会议</el-button></div></div>
    <el-skeleton v-if="loading" :rows="10" animated />
    <template v-else-if="meeting">
      <el-card class="content-card" shadow="never">
        <template #header><div class="detail-head"><div><h2>{{ meeting.title }}</h2><div class="status-row"><StatusTag :status="meeting.meeting_status" type="meeting" /><StatusTag :status="meeting.analysis_status" type="analysis" /><span>分析状态当前仅展示，不可手动修改</span></div></div><div class="detail-actions"><MeetingStatusActions :status="meeting.meeting_status" :loading="actionLoading" @change="changeStatus" /></div></div></template>
        <el-descriptions :column="2" border>
          <el-descriptions-item v-if="meeting.topic" label="会议主题">{{ meeting.topic }}</el-descriptions-item>
          <el-descriptions-item v-if="meeting.organizer" label="主办方">{{ meeting.organizer }}</el-descriptions-item>
          <el-descriptions-item label="开始时间">{{ formatDateTime(meeting.starts_at) }}</el-descriptions-item>
          <el-descriptions-item label="结束时间">{{ formatDateTime(meeting.ends_at) }}</el-descriptions-item>
          <el-descriptions-item v-if="meeting.location" label="举办地点">{{ meeting.location }}</el-descriptions-item>
          <el-descriptions-item v-if="meeting.online_url" label="线上地址"><el-link :href="meeting.online_url" target="_blank" type="primary">打开线上地址</el-link></el-descriptions-item>
          <el-descriptions-item v-if="meeting.cover_url" label="封面地址"><el-link :href="meeting.cover_url" target="_blank" type="primary">查看封面</el-link></el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ formatDateTime(meeting.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="更新时间">{{ formatDateTime(meeting.updated_at) }}</el-descriptions-item>
          <el-descriptions-item v-if="meeting.description" label="会议简介" :span="2"><div class="description">{{ meeting.description }}</div></el-descriptions-item>
        </el-descriptions>
      </el-card>
    </template>
  </section>
</template>

<style scoped>
.header-actions, .detail-actions, .status-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.detail-head { display: flex; justify-content: space-between; gap: 16px; align-items: center; }
h2 { margin: 0 0 10px; color: #173f58; font-size: 20px; }
.status-row span { color: #8493a0; font-size: 12px; }
.description { white-space: pre-wrap; line-height: 1.7; }
@media (max-width: 768px) { .detail-head { align-items: flex-start; flex-direction: column; } }
</style>
