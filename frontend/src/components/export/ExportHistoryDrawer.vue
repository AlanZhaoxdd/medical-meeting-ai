<script setup lang="ts">
import { Download, RefreshRight, VideoPause } from '@element-plus/icons-vue'
import { computed } from 'vue'
import { ElMessage } from 'element-plus'
import { meetingExportsApi } from '@/api/meetingExports'
import type { ExportRecord, ExportStatus } from '@/types/meetingExport'
import { toApiError } from '@/utils/errors'

const model = defineModel<boolean>({ required: true })
const props = defineProps<{ records: ExportRecord[] }>()
const emit = defineEmits<{ refresh: [] }>()

const typeLabels: Record<string, string> = {
  text: '文字纪要',
  ppt: '汇报 PPT',
  chart: '数据图表',
}

const formatLabels: Record<string, string> = {
  docx: 'DOCX',
  pdf: 'PDF',
  pptx: 'PPTX',
  png: 'PNG',
  svg: 'SVG',
}

const statusMeta: Record<ExportStatus, { label: string; type: 'success' | 'warning' | 'danger' | 'info' | 'primary' }> = {
  PENDING: { label: '等待中', type: 'info' },
  ANALYZING: { label: '分析中', type: 'warning' },
  GENERATING: { label: '生成中', type: 'warning' },
  RENDERING: { label: '排版中', type: 'warning' },
  COMPLETED: { label: '已完成', type: 'success' },
  FAILED: { label: '失败', type: 'danger' },
  CANCELLED: { label: '已取消', type: 'info' },
}

const sorted = computed(() =>
  [...props.records].sort((a, b) => b.created_at.localeCompare(a.created_at)),
)

function isBusy(status: ExportStatus): boolean {
  return ['PENDING', 'ANALYZING', 'GENERATING', 'RENDERING'].includes(status)
}

async function download(record: ExportRecord) {
  try {
    await meetingExportsApi.downloadExportFile(record.export_id, record.file_name)
  } catch (error) {
    ElMessage.error(toApiError(error).message)
  }
}

async function retry(record: ExportRecord) {
  try {
    await meetingExportsApi.retryExport(record.export_id)
    ElMessage.success('导出任务已重新提交')
    emit('refresh')
  } catch (error) {
    ElMessage.error(toApiError(error).message)
  }
}

async function cancel(record: ExportRecord) {
  try {
    await meetingExportsApi.cancelExport(record.export_id)
    ElMessage.success('导出任务已取消')
    emit('refresh')
  } catch (error) {
    ElMessage.error(toApiError(error).message)
  }
}
</script>

<template>
  <el-drawer v-model="model" title="导出历史" size="min(820px, 94vw)">
    <div class="history-summary">
      共 {{ sorted.length }} 条记录 · 不同会议与分析版本的导出相互隔离
    </div>
    <el-table :data="sorted" size="small" class="history-table">
      <el-table-column label="类型" width="110">
        <template #default="{ row }">
          <span class="type-name">{{ typeLabels[row.export_type] ?? row.export_type }}</span>
          <el-tag v-if="row.file_format" size="small" effect="plain">{{ formatLabels[row.file_format] ?? row.file_format }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="140">
        <template #default="{ row }">
          <el-tooltip
            :disabled="!row.error_message"
            :content="row.error_message || ''"
            placement="top"
          >
            <el-tag :type="statusMeta[row.status as ExportStatus]?.type ?? 'info'" size="small" effect="light">
              {{ statusMeta[row.status as ExportStatus]?.label ?? row.status }}
            </el-tag>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column label="进度" width="150">
        <template #default="{ row }">
          <el-progress
            :percentage="row.status === 'COMPLETED' ? 100 : row.progress"
            :stroke-width="8"
            :status="row.status === 'FAILED' ? 'exception' : undefined"
          />
        </template>
      </el-table-column>
      <el-table-column label="文件名 / 说明" min-width="180">
        <template #default="{ row }">
          <p class="file-name">{{ row.file_name || (row.message || '') }}</p>
          <p class="file-meta">版本 v{{ row.analysis_version }} · {{ row.created_at.slice(0, 16).replace('T', ' ') }}</p>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="150" fixed="right">
        <template #default="{ row }">
          <div class="row-actions">
            <el-button
              v-if="row.status === 'COMPLETED' && row.download_url"
              text
              size="small"
              :icon="Download"
              @click="download(row)"
            >
              下载
            </el-button>
            <el-button
              v-else-if="row.status === 'FAILED' || row.status === 'CANCELLED'"
              text
              size="small"
              :icon="RefreshRight"
              @click="retry(row)"
            >
              重试
            </el-button>
            <el-button
              v-else-if="isBusy(row.status as ExportStatus)"
              text
              size="small"
              :icon="VideoPause"
              @click="cancel(row)"
            >
              取消
            </el-button>
          </div>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!sorted.length" description="暂无导出记录" />
  </el-drawer>
</template>

<style scoped>
.history-summary { margin-bottom: 12px; color: #8a99a0; font-size: 12px; }
.type-name { margin-right: 6px; color: #314e62; font-weight: 600; }
.file-name { margin: 0; color: #314e62; font-size: 13px; }
.file-meta { margin: 4px 0 0; color: #9aa7ac; font-size: 11px; }
.row-actions { display: flex; }
</style>
