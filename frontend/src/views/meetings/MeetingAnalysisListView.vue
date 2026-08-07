<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import dayjs from 'dayjs'
import { DataAnalysis, Files, RefreshRight } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import { meetingsApi } from '@/api/meetings'
import type { Meeting } from '@/types/meeting'
import { toApiError } from '@/utils/errors'
import { analysisStatusLabels } from '@/utils/meeting'
import { attendeeCount } from '@/utils/meetingVerification'

const router = useRouter()
const loading = ref(false)
const loadError = ref('')
const result = reactive<{
  items: Meeting[]
  page: number
  page_size: number
  total: number
  total_pages: number
}>({ items: [], page: 1, page_size: 10, total: 0, total_pages: 0 })
const filters = reactive({ keyword: '' })
let requestSequence = 0

async function load(page = result.page) {
  const requestId = ++requestSequence
  loading.value = true
  loadError.value = ''
  result.page = page
  try {
    const data = await meetingsApi.list({
      page,
      page_size: result.page_size,
      keyword: filters.keyword || undefined,
    })
    if (requestId !== requestSequence) return
    result.items = data.items
    result.page = data.page
    result.page_size = data.page_size
    result.total = data.total
    result.total_pages = data.total_pages
  } catch (error) {
    if (requestId === requestSequence) loadError.value = toApiError(error).message
  } finally {
    if (requestId === requestSequence) loading.value = false
  }
}

const search = () => load(1)

function openAnalysis(meeting: Meeting) {
  void router.push({ name: 'meeting-analysis', params: { meetingId: meeting.id } })
}

function openExport(meeting: Meeting) {
  void router.push({ name: 'meeting-exports', params: { meetingId: meeting.id } })
}

const formatDate = (value: string) => dayjs(value).format('YYYY-MM-DD')
const formatTime = (value: string) => dayjs(value).format('HH:mm')
const attendeeLabel = (meeting: Meeting) => {
  const count = attendeeCount(meeting)
  return count ? `${count} 人` : '未提供'
}

onMounted(() => load(1))
</script>

<template>
  <section>
    <div class="page-header">
      <div>
        <p class="eyebrow">AI MEETING ANALYSIS</p>
        <h1 class="page-title">AI 纪要分析</h1>
        <p class="page-subtitle">选择会议查看 AI 生成的纪要分析与问答，或继续前往成果导出。</p>
      </div>
      <el-button :icon="RefreshRight" @click="load(1)">刷新</el-button>
    </div>

    <el-card class="filter-card" shadow="never">
      <el-form inline @submit.prevent="search">
        <el-form-item label="关键词">
          <el-input v-model="filters.keyword" clearable placeholder="会议标题" @keyup.enter="search" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" native-type="submit">查询</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card class="content-card" shadow="never">
      <el-alert
        v-if="loadError"
        type="error"
        :closable="false"
        :title="loadError"
        show-icon
      >
        <template #default>
          <el-button size="small" @click="load(result.page)">重试</el-button>
        </template>
      </el-alert>

      <el-table v-loading="loading" :data="result.items" row-key="id">
        <el-table-column prop="title" label="会议" min-width="220">
          <template #default="{ row }">
            <strong class="meeting-title">{{ row.title }}</strong>
          </template>
        </el-table-column>
        <el-table-column label="日期" width="120">
          <template #default="{ row }">{{ formatDate(row.starts_at) }}</template>
        </el-table-column>
        <el-table-column label="时间" width="90">
          <template #default="{ row }">{{ formatTime(row.starts_at) }}</template>
        </el-table-column>
        <el-table-column prop="topic" label="领域" min-width="130">
          <template #default="{ row }">{{ row.topic || '未提供' }}</template>
        </el-table-column>
        <el-table-column label="参会人数" width="110">
          <template #default="{ row }">{{ attendeeLabel(row) }}</template>
        </el-table-column>
        <el-table-column label="AI 分析状态" width="140">
          <template #default="{ row }">
            <el-tag
              :type="row.analysis_status === 'succeeded' ? 'success' : row.analysis_status === 'failed' ? 'danger' : row.analysis_status === 'processing' || row.analysis_status === 'queued' ? 'warning' : 'info'"
              effect="light"
            >
              {{ analysisStatusLabels[row.analysis_status as Meeting['analysis_status']] ?? row.analysis_status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="240">
          <template #default="{ row }">
            <el-button
              link
              type="primary"
              :icon="DataAnalysis"
              @click="openAnalysis(row)"
            >
              查看 AI 纪要
            </el-button>
            <el-button
              link
              type="primary"
              :icon="Files"
              :disabled="row.analysis_status !== 'succeeded'"
              @click="openExport(row)"
            >
              成果导出
            </el-button>
          </template>
        </el-table-column>
        <template #empty>
          <div class="empty-placeholder">{{ loadError ? '加载失败，请重试。' : '暂无会议。' }}</div>
        </template>
      </el-table>

      <div class="pagination">
        <el-pagination
          v-model:current-page="result.page"
          :page-size="result.page_size"
          layout="total, prev, pager, next"
          :total="result.total"
          @current-change="load"
        />
      </div>
    </el-card>
  </section>
</template>

<style scoped>
.meeting-title { color: #173f58; }
.pagination { display: flex; justify-content: flex-end; margin-top: 20px; }
</style>
