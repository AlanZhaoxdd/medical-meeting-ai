import { describe, expect, it } from 'vitest'
import { normalizeChartSpec, normalizeExportRecord } from '@/types/meetingExport'

describe('meeting export normalizers', () => {
  it('normalizes a backend export record with download metadata', () => {
    const record = normalizeExportRecord({
      export_id: 'export-1',
      meeting_id: 'meeting-1',
      analysis_version: 3,
      export_type: 'ppt',
      file_format: 'pptx',
      status: 'COMPLETED',
      progress: 100,
      current_stage: 'completed',
      message: 'PPT 导出完成',
      error_message: null,
      file_name: '汇报.pptx',
      download_url: 'https://minio.local/export',
      config: { include_charts: true },
      created_at: '2026-08-06T10:00:00Z',
    })
    expect(record?.export_id).toBe('export-1')
    expect(record?.export_type).toBe('ppt')
    expect(record?.status).toBe('COMPLETED')
    expect(record?.file_format).toBe('pptx')
    expect(record?.config.include_charts).toBe(true)
  })

  it('normalizes a chart spec with evidence and validation', () => {
    const spec = normalizeChartSpec({
      id: 'chart-1',
      meeting_id: 'meeting-1',
      analysis_version: 3,
      type: 'bar',
      title: '各切点问题参会者覆盖度',
      subtitle: '统计口径：独立参会者',
      metric: 'independent_speakers',
      denominator: { name: '有效参会者', value: 5 },
      categories: [
        {
          key: 'q1',
          label: '剂量调整',
          value: 2,
          percentage: null,
          evidence: [{ speakerName: '张三', sourceId: 's1', snippet: '我建议调整' }],
        },
      ],
      validation: { valid: true, generatedAt: '2026-08-06T10:00:00Z' },
      generated_at: '2026-08-06T10:00:00Z',
    })
    expect(spec?.type).toBe('bar')
    expect(spec?.categories[0].value).toBe(2)
    expect(spec?.categories[0].evidence[0].speakerName).toBe('张三')
    expect(spec?.validation.valid).toBe(true)
  })

  it('rejects malformed records', () => {
    expect(normalizeExportRecord(null)).toBeNull()
    expect(normalizeExportRecord({})).toBeNull()
    expect(normalizeChartSpec({})).toBeNull()
  })
})
