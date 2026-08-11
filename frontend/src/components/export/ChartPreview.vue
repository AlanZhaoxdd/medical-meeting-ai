<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import type { EChartsOption } from 'echarts'
import type { ChartCategory, ChartSpec } from '@/types/meetingExport'

const props = defineProps<{
  spec: ChartSpec
  showLegend: boolean
  showLabels: boolean
  downloadMode?: boolean
}>()

const emit = defineEmits<{
  categoryClick: [category: ChartCategory]
}>()

const chartEl = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null

function wrapTitle(text: string, maxChars = 20): string {
  const cleaned = String(text ?? '').replace(/\n/g, '')
  if (cleaned.length <= maxChars) return cleaned
  const lines: string[] = []
  for (let index = 0; index < cleaned.length; index += maxChars) {
    lines.push(cleaned.slice(index, index + maxChars))
  }
  return lines.join('\n')
}

const option = computed<EChartsOption>(() => {
  const categories = props.spec.categories
  const isPie = props.spec.type === 'pie'
  const wrappedTitle = wrapTitle(props.spec.title, isPie ? 20 : 28)
  const titleLines = wrappedTitle.split('\n').length
  // Reserve space for the title and an optional subtitle so the chart body never
  // overlaps them, no matter how many lines the title wraps to.
  const titleBlockHeight = (isPie ? 8 : 14) + titleLines * 22 + (props.spec.subtitle ? 24 : 0)
  const pieCenterY = `${Math.min(0.68, Math.max(0.46, (titleBlockHeight + 28 + 104) / 380)) * 100}%`
  const metricLabel = props.spec.data_origin === 'demo'
    ? '人数'
    : props.spec.count_mode === 'evidence_count' ? '有效证据次数' : '人数'
  const base: EChartsOption = {
    textStyle: { fontFamily: 'Microsoft YaHei, PingFang SC, sans-serif', color: '#314e62' },
    title: {
      text: wrappedTitle,
      subtext: props.spec.subtitle,
      left: 'center',
      top: isPie ? 8 : 14,
      width: isPie ? '92%' : undefined,
      textStyle: {
        color: '#123c53',
        fontSize: isPie ? 14 : 16,
        fontWeight: 700,
        lineHeight: 22,
      },
      subtextStyle: { color: '#6f8390', fontSize: 11, lineHeight: 16 },
    },
    tooltip: { trigger: 'item', confine: true },
    grid: { left: 50, right: 30, top: Math.max(90, titleBlockHeight + 32), bottom: 70 },
  }
  if (props.spec.type === 'bar') {
    base.tooltip = {
      trigger: 'axis',
      confine: true,
      formatter: (params: unknown) => {
        const list = Array.isArray(params) ? params : []
        const index = Number(list[0]?.dataIndex ?? 0)
        const category = categories[index]
        if (!category) return ''
        const lines = [
          `<b>${category.label}</b>`,
          `${metricLabel}：${category.value}`,
        ]
        return lines.join('<br/>')
      },
    }
    base.xAxis = {
      type: 'category',
      data: categories.map((category) => category.label),
      axisLabel: { interval: 0, rotate: categories.length > 5 ? 24 : 0, width: 96, overflow: 'truncate' },
    }
    base.yAxis = {
      type: 'value',
      name: metricLabel,
    }
    base.series = [
      {
        type: 'bar',
        data: categories.map((category) => ({
          value: category.value,
          itemStyle: { color: '#168b82', borderRadius: [4, 4, 0, 0] },
        })),
        label: { show: props.showLabels, position: 'top', color: '#123c53', fontWeight: 700 },
        barMaxWidth: 54,
      },
    ]
  } else {
    base.tooltip = {
      trigger: 'item',
      confine: true,
      formatter: (params: unknown) => {
        const item = (params as { dataIndex?: number }) ?? {}
        const category = categories[Number(item.dataIndex ?? 0)]
        if (!category) return ''
        const lines = [
          `<b>${category.label}</b>`,
          `${category.value}人 · ${category.percentage ?? 0}%${props.spec.denominator?.value ? `（样本 ${props.spec.denominator.value} 人）` : ''}`,
        ]
        return lines.join('<br/>')
      },
    }
    base.legend = { show: props.showLegend, bottom: 8, type: 'scroll' }
    base.series = [
      {
        type: 'pie',
        radius: ['34%', '55%'],
        center: ['50%', pieCenterY],
        data: categories.map((category) => ({
          name: category.label,
          value: category.value,
        })),
        label: {
          show: props.showLabels,
          formatter: '{b} {d}%',
          color: '#314e62',
          fontSize: 11,
        },
        emphasis: { itemStyle: { shadowBlur: 8, shadowColor: 'rgba(0,0,0,0.2)' } },
      },
    ]
  }
  return base
})

function render() {
  if (!chartEl.value) return
  chart ??= echarts.init(chartEl.value)
  chart.setOption(option.value, true)
  chart.off('click')
  chart.on('click', (params: unknown) => {
    const index = Number((params as { dataIndex?: number })?.dataIndex ?? -1)
    const category = props.spec.categories[index]
    if (category && props.spec.data_origin !== 'demo') emit('categoryClick', category)
  })
}

function resize() {
  chart?.resize()
}

onMounted(() => {
  render()
  window.addEventListener('resize', resize)
})
watch(option, render, { deep: true })
onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  chart?.dispose()
  chart = null
})

defineExpose({
  downloadPng() {
    if (!chart) return
    const url = chart.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: '#fff' })
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${props.spec.id || 'chart'}.png`
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
  },
  downloadSvg() {
    if (!chart) return
    const url = chart.getDataURL({ type: 'svg', backgroundColor: '#fff' })
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${props.spec.id || 'chart'}.svg`
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
  },
})
</script>

<template>
  <div ref="chartEl" class="chart-preview" :class="{ 'chart-preview--download': downloadMode }"></div>
</template>

<style scoped>
.chart-preview { position: relative; z-index: 1; width: 100%; height: 380px; }
.chart-preview--download { position: absolute; left: -9999px; top: 0; width: 960px; height: 640px; }
</style>
