<script setup lang="ts">
import dayjs from 'dayjs'
import { ElMessage } from 'element-plus'
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import MeetingStatusActions from '@/components/MeetingStatusActions.vue'
import StatusTag from '@/components/StatusTag.vue'
import { meetingsApi } from '@/api/meetings'
import { useMeetings } from '@/composables/useMeetings'
import type { AnalysisStatus, Meeting, MeetingStatus } from '@/types/meeting'
import { formatDateTime, meetingStatusLabels, analysisStatusLabels } from '@/utils/meeting'
import { toApiError } from '@/utils/errors'

const router = useRouter()
const { loading, result, filters, fetchMeetings, resetFilters } = useMeetings()
const dateRange = ref<[Date, Date] | undefined>()
const actionLoadingId = ref<string>()

const search = async () => {
  filters.page = 1
  filters.starts_at_from = dateRange.value ? dayjs(dateRange.value[0]).startOf('day').toISOString() : undefined
  filters.starts_at_to = dateRange.value ? dayjs(dateRange.value[1]).endOf('day').toISOString() : undefined
  try { await fetchMeetings() } catch (error) { ElMessage.error(toApiError(error).message) }
}
const reset = async () => { resetFilters(); dateRange.value = undefined; await search() }
const changePage = async (page: number) => { filters.page = page; await search() }
const changeStatus = async (meeting: Meeting, status: MeetingStatus) => {
  actionLoadingId.value = meeting.id
  try { await meetingsApi.changeStatus(meeting.id, status); ElMessage.success('会议状态已更新'); await fetchMeetings() }
  catch (error) { ElMessage.error(toApiError(error).message) }
  finally { actionLoadingId.value = undefined }
}
const openMeeting = (meeting: Meeting) => router.push({ name: 'meeting-detail', params: { id: meeting.id } })
onMounted(search)
</script>

<template>
  <section>
    <div class="page-header"><div><h1 class="page-title">会议管理</h1><p class="page-subtitle">统一管理学术会议资料、业务状态与后续分析准备情况。</p></div><el-button type="primary" @click="router.push({ name: 'meeting-create' })">新建会议</el-button></div>
    <el-card class="filter-card" shadow="never">
      <el-form inline @submit.prevent="search">
        <el-form-item label="关键词"><el-input v-model="filters.keyword" clearable placeholder="标题或主办方" @keyup.enter="search" /></el-form-item>
        <el-form-item label="会议状态"><el-select v-model="filters.meeting_status" clearable placeholder="全部" style="width: 130px"><el-option v-for="(_, value) in meetingStatusLabels" :key="value" :label="meetingStatusLabels[value as MeetingStatus]" :value="value" /></el-select></el-form-item>
        <el-form-item label="分析状态"><el-select v-model="filters.analysis_status" clearable placeholder="全部" style="width: 130px"><el-option v-for="(_, value) in analysisStatusLabels" :key="value" :label="analysisStatusLabels[value as AnalysisStatus]" :value="value" /></el-select></el-form-item>
        <el-form-item label="开始日期"><el-date-picker v-model="dateRange" type="daterange" range-separator="至" start-placeholder="开始日期" end-placeholder="结束日期" /></el-form-item>
        <el-form-item><el-button type="primary" native-type="submit">查询</el-button><el-button @click="reset">重置</el-button></el-form-item>
      </el-form>
    </el-card>
    <el-card class="content-card" shadow="never">
      <el-table v-loading="loading" :data="result.items" row-key="id" class="meeting-table" @row-click="openMeeting">
        <el-table-column prop="title" label="会议" min-width="220"><template #default="{ row }"><strong class="meeting-title">{{ row.title }}</strong><div class="muted">{{ row.topic || '未设置主题' }}</div></template></el-table-column>
        <el-table-column label="时间" min-width="165"><template #default="{ row }">{{ formatDateTime(row.starts_at) }}<br /><span class="muted">至 {{ formatDateTime(row.ends_at) }}</span></template></el-table-column>
        <el-table-column prop="location" label="地点" min-width="130"><template #default="{ row }">{{ row.location || '—' }}</template></el-table-column>
        <el-table-column prop="organizer" label="主办方" min-width="130"><template #default="{ row }">{{ row.organizer || '—' }}</template></el-table-column>
        <el-table-column label="会议状态" width="110"><template #default="{ row }"><StatusTag :status="row.meeting_status" type="meeting" /></template></el-table-column>
        <el-table-column label="分析状态" width="110"><template #default="{ row }"><StatusTag :status="row.analysis_status" type="analysis" /></template></el-table-column>
        <el-table-column label="快捷操作" width="180" align="center" fixed="right"><template #default="{ row }"><div v-if="row.meeting_status === 'published'" class="table-actions" @click.stop><MeetingStatusActions :status="row.meeting_status" :loading="actionLoadingId === row.id" @change="changeStatus(row, $event)" /></div><span v-else class="muted">—</span></template></el-table-column>
        <template #empty><div class="empty-placeholder">暂无会议数据，您可以新建第一场会议。</div></template>
      </el-table>
      <div class="pagination"><el-pagination v-model:current-page="filters.page" :page-size="filters.page_size" layout="total, prev, pager, next" :total="result.total" @current-change="changePage" /></div>
    </el-card>
  </section>
</template>

<style scoped>
.muted { margin-top: 4px; color: #8292a0; font-size: 12px; }
.pagination { display: flex; justify-content: flex-end; margin-top: 20px; }
.meeting-title { color: #173f58; font-weight: 600; }
:deep(.meeting-table .el-table__body tr) { cursor: pointer; }
:deep(.meeting-table .el-table__body tr:hover > td) { background: #f1faf9 !important; }
</style>
