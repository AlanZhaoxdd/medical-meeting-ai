<script setup lang="ts">
import { DataAnalysis, Right } from '@element-plus/icons-vue'
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { meetingsApi } from '@/api/meetings'
import type { Meeting } from '@/types/meeting'
import { toApiError } from '@/utils/errors'
import { attendeeCount } from '@/utils/meetingVerification'
import dayjs from 'dayjs'

const router = useRouter()
const meetings = ref<Meeting[]>([])
const loading = ref(false)
const loadError = ref('')
const selectedId = ref('')

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const data = await meetingsApi.list({ page: 1, page_size: 50 })
    meetings.value = data.items
    if (data.items.length && !selectedId.value) selectedId.value = data.items[0].id
  } catch (error) {
    loadError.value = toApiError(error).message
  } finally {
    loading.value = false
  }
}

function enter() {
  if (!selectedId.value) return
  void router.push({ name: 'meeting-analysis', params: { meetingId: selectedId.value } })
}

onMounted(load)
</script>

<template>
  <section>
    <div class="page-header">
      <div>
        <p class="eyebrow">AI MEETING ANALYSIS</p>
        <h1 class="page-title">分析中心</h1>
        <p class="page-subtitle">选择一场会议，查看 AI 纪要分析并进行基于会议内容与知识库的智能问答。</p>
      </div>
    </div>
    <el-card class="content-card analysis-landing" shadow="never">
      <div class="landing-hero">
        <div class="hero-icon"><el-icon><DataAnalysis /></el-icon></div>
        <div class="hero-copy">
          <h2>AI 纪要分析</h2>
          <p>每个会议的 AI 纪要分析是独立页面：AI 通读整篇确认稿后生成一份完整纪要，切点问题与开放性问题以小节形式融入正文并带引用角标，另提供 RAG 智能问答窗口。也可以从“基本信息概览”列表中每个会议的操作列进入。</p>
        </div>
      </div>
      <el-alert v-if="loadError" type="error" :closable="false" :title="loadError" show-icon>
        <template #default><el-button size="small" @click="load">重试</el-button></template>
      </el-alert>
      <div v-else class="picker">
        <el-select v-model="selectedId" filterable :loading="loading" placeholder="选择会议" class="picker-select">
          <el-option v-for="meeting in meetings" :key="meeting.id" :label="meeting.title" :value="meeting.id">
            <span>{{ meeting.title }}</span>
            <span class="option-meta">{{ dayjs(meeting.starts_at).format('YYYY-MM-DD') }} · {{ attendeeCount(meeting) || 0 }} 人</span>
          </el-option>
        </el-select>
        <el-button type="primary" :icon="Right" :disabled="!selectedId" @click="enter">进入 AI 纪要分析</el-button>
      </div>
      <el-empty v-if="!loading && !loadError && !meetings.length" :image-size="54" description="暂无会议，请先导入或创建会议" />
    </el-card>
  </section>
</template>

<style scoped>
.analysis-landing { max-width: 860px; }
.landing-hero { display: flex; align-items: flex-start; gap: 18px; padding: 18px 4px 22px; }
.hero-icon { display: grid; place-items: center; flex: 0 0 54px; height: 54px; border-radius: 14px 14px 14px 5px; color: white; background: linear-gradient(135deg, #6c4fd0, #8a6ae8); font-size: 26px; }
.hero-copy h2 { margin: 2px 0 8px; color: #173f58; font-size: 20px; }
.hero-copy p { margin: 0; color: #6f7a90; font-size: 13px; line-height: 1.7; }
.picker { display: flex; align-items: center; gap: 12px; padding: 18px 0 6px; border-top: 1px solid #edf2f0; }
.picker-select { max-width: 460px; }
.picker-select :deep(.el-select-dropdown__item) { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.option-meta { color: #9aa5b0; font-size: 12px; }
@media (max-width: 640px) {
  .landing-hero { flex-direction: column; }
  .picker { align-items: stretch; flex-direction: column; }
  .picker-select { max-width: none; }
}
</style>
