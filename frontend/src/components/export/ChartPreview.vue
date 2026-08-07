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

const option = computed<EChartsOption>(() => {
  const categories = props.spec.categories
  const base: EChartsOption = {
    textStyle: { fontFamily: 'Microsoft YaHei, PingFang SC, sans-serif', color: '#314e62' },
    title: {
      text: props.spec.title,
      subtext: props.spec.subtitle,
      left: 'center',
      textStyle: { color: '#123c53', fontSize: 16, fontWeight: 700 },
      subtextStyle: { color: '#6f8390', fontSize: 11 },
    },
    tooltip: { trigger: 'item' },
    grid: { left: 50, right: 30, top: 90, bottom: 70 },
  }
  if (props.spec.type === 'bar') {
    base.tooltip = {
      trigger: 'axis',
      formatter: (params: unknown) => {
        const list = Array.isArray(params) ? params : []
        const index = Number(list[0]?.dataIndex ?? 0)
        const category = categories[index]
        if (!category) return ''
        const lines = [
          `<b>${category.label}</b>`,
          `${props.spec.metric === 'evidence_count' ? '有效证据片段数' : '独立参会者覆盖数'}：${category.value}`,
        ]
        for (const item of category.evidence.slice(0, 4)) {
          const speaker = item.speakerName ? `${item.speakerName}：` : ''
          lines.push(`<div style="margin-top:4px">${speaker}${item.snippet.slice(0, 60)}</div>`)
        }
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
      name: props.spec.metric === 'evidence_count' ? '证据片段数' : '独立参会者人数',
      minInterval: 1,
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
      formatter: (params: unknown) => {
        const item = (params as { dataIndex?: number }) ?? {}
        const category = categories[Number(item.dataIndex ?? 0)]
        if (!category) return ''
        const lines = [
          `<b>${category.label}</b>`,
          `${category.value} 人 · ${category.percentage ?? 0}%`,
        ]
        for (const evidence of category.evidence.slice(0, 3)) {
          lines.push(`<div style="margin-top:4px">${evidence.speakerName ?? ''}：${evidence.snippet.slice(0, 60)}</div>`)
        }
        return lines.join('<br/>')
      },
    }
    base.legend = { show: props.showLegend, bottom: 8, type: 'scroll' }
    base.series = [
      {
        type: 'pie',
        radius: ['38%', '62%'],
        center: ['50%', '46%'],
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
    if (category) emit('categoryClick', category)
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
.chart-preview { width: 100%; height: 380px; }
.chart-preview--download { position: absolute; left: -9999px; top: 0; width: 960px; height: 640px; }
</style>
