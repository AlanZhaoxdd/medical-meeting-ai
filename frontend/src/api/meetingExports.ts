import { http } from '@/api/client'
import { ElMessage } from 'element-plus'
import {
  normalizeChartSpec,
  normalizeExportRecord,
  type ChartSpec,
  type ExportRecord,
  type ExportRecordList,
  type PptDeckSpec,
  type PptOutline,
  type TextExportConfig,
  type TextPreview,
} from '@/types/meetingExport'

const exportsRoot = (meetingId: string) => `/api/v1/meetings/${meetingId}/exports`
const chartsRoot = (meetingId: string) => `/api/v1/meetings/${meetingId}/charts`

interface SavePickerHandle {
  createWritable: () => Promise<{
    write: (data: Blob) => Promise<void>
    close: () => Promise<void>
  }>
}

interface SavePickerWindow {
  showSaveFilePicker?: (options?: { suggestedName?: string }) => Promise<SavePickerHandle>
}

function normalizeList(raw: unknown): ExportRecordList {
  const value = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
  return {
    items: (Array.isArray(value.items) ? value.items : [])
      .map(normalizeExportRecord)
      .filter((item): item is ExportRecord => Boolean(item)),
    total: Number(value.total ?? 0) || 0,
    page: Number(value.page ?? 1) || 1,
    page_size: Number(value.page_size ?? 20) || 20,
  }
}

export const meetingExportsApi = {
  async listExports(meetingId: string, page = 1, pageSize = 20): Promise<ExportRecordList> {
    const { data } = await http.get<unknown>(exportsRoot(meetingId), {
      params: { page, page_size: pageSize },
    })
    return normalizeList(data)
  },
  async getExport(exportId: string): Promise<ExportRecord> {
    const { data } = await http.get<unknown>(`/api/v1/exports/${exportId}`)
    const record = normalizeExportRecord(data)
    if (!record) throw new Error('导出任务格式不正确。')
    return record
  },
  async retryExport(exportId: string): Promise<ExportRecord> {
    const { data } = await http.post<unknown>(`/api/v1/exports/${exportId}/retry`)
    const record = normalizeExportRecord(data)
    if (!record) throw new Error('导出任务格式不正确。')
    return record
  },
  async cancelExport(exportId: string): Promise<ExportRecord> {
    const { data } = await http.post<unknown>(`/api/v1/exports/${exportId}/cancel`)
    const record = normalizeExportRecord(data)
    if (!record) throw new Error('导出任务格式不正确。')
    return record
  },
  async downloadExport(exportId: string): Promise<{ url: string; file_name: string }> {
    const { data } = await http.get<{ url: string; file_name: string }>(
      `/api/v1/exports/${exportId}/download`,
    )
    return data
  },
  async downloadExportFile(exportId: string, fileName?: string | null): Promise<void> {
    const name = fileName || 'download'
    const pickerWindow = window as unknown as SavePickerWindow
    let saveHandle: SavePickerHandle | undefined
    if (pickerWindow.showSaveFilePicker) {
      try {
        saveHandle = await pickerWindow.showSaveFilePicker({ suggestedName: name })
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') return
      }
    }
    const { data } = await http.get<Blob>(`/api/v1/exports/${exportId}/download/file`, {
      responseType: 'blob',
      timeout: 120_000,
    })
    if (saveHandle) {
      try {
        const writable = await saveHandle.createWritable()
        await writable.write(data)
        await writable.close()
        return
      } catch {
        throw new Error('保存文件失败，请重试或改用浏览器默认下载。')
      }
    }
    const objectUrl = URL.createObjectURL(data)
    const anchor = document.createElement('a')
    anchor.href = objectUrl
    anchor.download = name
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    URL.revokeObjectURL(objectUrl)
    ElMessage.info('已开始下载到浏览器默认目录；可在浏览器设置中开启“下载前询问保存位置”')
  },
  async previewText(
    meetingId: string,
    config: TextExportConfig,
  ): Promise<TextPreview> {
    const { data } = await http.get<unknown>(`${exportsRoot(meetingId)}/text/preview`, {
      params: {
        selected: config.sections?.length ? config.sections.join(',') : undefined,
        show_attendee_names: config.show_attendee_names,
        template: config.template,
        include_cover: config.include_cover,
      },
      timeout: 30_000,
    })
    return data as TextPreview
  },
  async createTextExport(meetingId: string, config: TextExportConfig): Promise<ExportRecord> {
    const { data } = await http.post<unknown>(`${exportsRoot(meetingId)}/text`, config)
    const record = normalizeExportRecord(data)
    if (!record) throw new Error('文字导出任务格式不正确。')
    return record
  },
  async createPptOutline(meetingId: string): Promise<ExportRecord> {
    const { data } = await http.post<unknown>(`${exportsRoot(meetingId)}/ppt/outline`)
    const record = normalizeExportRecord(data)
    if (!record) throw new Error('PPT 大纲任务格式不正确。')
    return record
  },
  async getPptOutline(meetingId: string): Promise<PptOutline | null> {
    try {
      const { data } = await http.get<unknown>(`${exportsRoot(meetingId)}/ppt/outline`)
      const value = (data && typeof data === 'object' ? data : {}) as Record<string, unknown>
      if (!value.spec || typeof value.spec !== 'object') return null
      return {
        id: String(value.id ?? ''),
        meeting_id: String(value.meeting_id ?? ''),
        analysis_version: Number(value.analysis_version ?? 0) || 0,
        spec: value.spec as PptDeckSpec,
        generated_at: String(value.generated_at ?? ''),
      }
    } catch (error) {
      const status = (error as { status?: number })?.status
      if (status === 404) return null
      throw error
    }
  },
  async savePptOutline(meetingId: string, spec: PptDeckSpec): Promise<PptOutline> {
    const { data } = await http.put<unknown>(`${exportsRoot(meetingId)}/ppt/outline`, spec)
    return data as PptOutline
  },
  async regeneratePptPage(
    meetingId: string,
    pageNumber: number,
    instruction?: string,
  ): Promise<PptOutline> {
    const { data } = await http.post<unknown>(
      `${exportsRoot(meetingId)}/ppt/outline/regenerate-page`,
      { page_number: pageNumber, instruction: instruction ?? '' },
      { timeout: 120_000 },
    )
    return data as PptOutline
  },
  async createPptExport(meetingId: string, config: Record<string, unknown>): Promise<ExportRecord> {
    const { data } = await http.post<unknown>(`${exportsRoot(meetingId)}/ppt`, config)
    const record = normalizeExportRecord(data)
    if (!record) throw new Error('PPT 导出任务格式不正确。')
    return record
  },
  async planChart(
    meetingId: string,
    payload: { chart_type: 'bar' | 'pie'; target_question_id?: string | null; metric: string },
  ): Promise<ExportRecord> {
    const { data } = await http.post<unknown>(`${chartsRoot(meetingId)}/plan`, null, {
      params: payload,
    })
    const record = normalizeExportRecord(data)
    if (!record) throw new Error('图表分析任务格式不正确。')
    return record
  },
  async listCharts(meetingId: string): Promise<ChartSpec[]> {
    const { data } = await http.get<unknown>(chartsRoot(meetingId))
    return (Array.isArray(data) ? data : [])
      .map(normalizeChartSpec)
      .filter((item): item is ChartSpec => Boolean(item))
  },
  chartImageUrl(meetingId: string, chartId: string, fmt: 'png' | 'svg'): string {
    return `${chartsRoot(meetingId)}/${chartId}/image?fmt=${fmt}`
  },
}
