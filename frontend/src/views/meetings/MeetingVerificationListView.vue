<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import dayjs from 'dayjs'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'
import { meetingsApi } from '@/api/meetings'
import type { Meeting } from '@/types/meeting'
import { toApiError } from '@/utils/errors'
import { attendeeCount, verificationStatusLabels, verificationStatusType } from '@/utils/meetingVerification'

const router = useRouter()
const loading = ref(false)
const loadError = ref('')
const result = reactive<{ items: Meeting[]; page: number; page_size: number; total: number; total_pages: number }>({ items: [], page: 1, page_size: 10, total: 0, total_pages: 0 })
const filters = reactive({ keyword: '' })
const deletingId = ref<string | null>(null)
let requestSequence = 0

async function load(page = result.page) {
  const requestId = ++requestSequence
  loading.value = true; loadError.value = ''; result.page = page
  try {
    const data = await meetingsApi.list({ page, page_size: result.page_size, keyword: filters.keyword || undefined })
    if (requestId !== requestSequence) return
    result.items = data.items; result.page = data.page; result.page_size = data.page_size; result.total = data.total; result.total_pages = data.total_pages
  } catch (error) { if (requestId === requestSequence) loadError.value = toApiError(error).message }
  finally { if (requestId === requestSequence) loading.value = false }
}
const search = () => load(1)
const open = (meeting: Meeting) => router.push({ name: 'meeting-review-detail', params: { meetingId: meeting.id } })
const openAnalysis = (meeting: Meeting) => router.push({ name: 'meeting-analysis', params: { meetingId: meeting.id } })
const formatDate = (value: string) => dayjs(value).format('YYYY-MM-DD')
const formatTime = (value: string) => dayjs(value).format('HH:mm')
const attendeeLabel = (meeting: Meeting) => { const count = attendeeCount(meeting); return count ? `${count} 人` : '未提供' }
const analysisSubmitted = (meeting: Meeting) => meeting.analysis_status === 'queued'
async function removeMeeting(meeting: Meeting) {
  try {
    await ElMessageBox.confirm(`删除会议“${meeting.title}”后将无法在列表中恢复，确定删除吗？`, '删除会议', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
    deletingId.value = meeting.id
    await meetingsApi.remove(meeting.id)
    ElMessage.success('会议已删除')
    const nextPage = result.items.length === 1 && result.page > 1 ? result.page - 1 : result.page
    await load(nextPage)
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(toApiError(error).message)
  } finally {
    deletingId.value = null
  }
}
onMounted(() => load(1))
</script>

<template>
  <section>
    <div class="page-header"><div><p class="eyebrow">MEETING VERIFICATION</p><h1 class="page-title">基本信息概览</h1><p class="page-subtitle">在提交 AI 分析前，核对会议基本信息与待确认问题。</p></div></div>
    <el-card class="filter-card" shadow="never"><el-form inline @submit.prevent="search"><el-form-item label="关键词"><el-input v-model="filters.keyword" clearable placeholder="会议标题" @keyup.enter="search" /></el-form-item><el-form-item><el-button type="primary" native-type="submit">查询</el-button></el-form-item></el-form></el-card>
    <el-card class="content-card" shadow="never">
      <el-alert v-if="loadError" type="error" :closable="false" :title="loadError" show-icon><template #default><el-button size="small" @click="load(result.page)">重试</el-button></template></el-alert>
      <el-table v-loading="loading" :data="result.items" row-key="id" @row-click="open">
        <el-table-column prop="title" label="会议" min-width="220"><template #default="{ row }"><strong class="meeting-title">{{ row.title }}</strong></template></el-table-column>
        <el-table-column label="日期" width="120"><template #default="{ row }">{{ formatDate(row.starts_at) }}</template></el-table-column>
        <el-table-column label="时间" width="90"><template #default="{ row }">{{ formatTime(row.starts_at) }}</template></el-table-column>
        <el-table-column prop="topic" label="领域" min-width="130"><template #default="{ row }">{{ row.topic || '未提供' }}</template></el-table-column>
        <el-table-column label="地点/场次" min-width="140"><template #default="{ row }">{{ row.location || '未提供' }}</template></el-table-column>
        <el-table-column label="参会人数" width="110"><template #default="{ row }">{{ attendeeLabel(row) }}</template></el-table-column>
        <el-table-column label="核验状态" width="120"><template #default="{ row }"><el-tag :type="analysisSubmitted(row) ? 'success' : verificationStatusType(row.verification_status ?? 'pending')" effect="light">{{ analysisSubmitted(row) ? '已提交分析' : verificationStatusLabels[row.verification_status ?? 'pending'] }}</el-tag></template></el-table-column>
        <el-table-column label="操作" width="270"><template #default="{ row }"><el-button link type="primary" @click.stop="open(row)">进入核验</el-button><el-button link type="primary" @click.stop="openAnalysis(row)">AI 纪要分析</el-button><el-button link type="danger" :loading="deletingId === row.id" @click.stop="removeMeeting(row)">删除</el-button></template></el-table-column>
        <template #empty><div class="empty-placeholder">{{ loadError ? '加载失败，请重试。' : '暂无待核验会议。' }}</div></template>
      </el-table>
      <div class="pagination"><el-pagination v-model:current-page="result.page" :page-size="result.page_size" layout="total, prev, pager, next" :total="result.total" @current-change="load" /></div>
    </el-card>
  </section>
</template>

<style scoped>
.meeting-title { color: #173f58; }.muted { margin-top: 4px; color: #8292a0; font-size: 12px; }.pagination { display: flex; justify-content: flex-end; margin-top: 20px; }:deep(.el-table__body tr) { cursor: pointer; }
</style>
