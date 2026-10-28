<script setup lang="ts">
/**
 * 权益曲线。
 *
 * 数据来源：后端 equity_curve 字段（每交易日一点）。
 *
 * 买卖点标注说明：
 *   标注位置严格取自 trades 中的 entry_date / exit_date，
 *   即真实成交通道记录；
 *   不通过每日 shares>0 推断买卖事件，
 *   因为持仓状态不等于成交事件。
 */
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import * as echarts from 'echarts'

import type {
  BenchmarkPoint,
  EquityPoint,
  TradeRecord,
} from '@/types/backtest'
import { formatAmount } from '@/utils/format'

const props = defineProps<{
  equityCurve: EquityPoint[]
  trades: TradeRecord[]
  initialCapital: number | null
  /**
   * 买入持有基准曲线（可选）。
   *
   * 只有在日期与长度都与 equityCurve 完全对齐时才会绘制，
   * 详见 alignedBenchmark 的说明。
   *
   * 不传该prop 时，图表行为与改造前完全一致。
   */
  benchmark?: BenchmarkPoint[]
}>()

const chartRef = ref<HTMLDivElement | null>(null)
const chart = shallowRef<echarts.ECharts | null>(null)

/** 买入点：来自 trades[].entry_date。 */
const buyMarkers = computed(() =>
  props.trades.map((t) => ({
    date: t.entry_date,
    value: t.entry_price,
    shares: t.shares,
  })),
)

/** 卖出点：来自 trades[].exit_date。 */
const sellMarkers = computed(() =>
  props.trades.map((t) => ({
    date: t.exit_date,
    value: t.exit_price,
    pnl: t.pnl,
    forced: t.forced_exit,
  })),
)

/**
 * 对齐校验后的基准曲线。
 *
 * 为什么必须校验而不能直接按下标拼接：
 *     基准曲线与权益曲线共用同一套 category 横轴，
 *     按下标拼接的前提是两者逐日对应。
 *     若长度或日期不一致，拼接出的曲线会指向错误的交易日，
 *     这种错误在图上看起来只是「有点偏移」，
 *     极易被误读为真实的策略表现差异。
 *
 * 校验方式：
 *     逐项比较日期字符串，任一不等即视为未对齐。
 *     不做模糊匹配，也不做近似比较——
 *     宁可少画一条线，也不能画一条可能错误的线。
 *
 * 未对齐时返回 null，图表跳过基准线。
 */
const alignedBenchmark = computed<BenchmarkPoint[] | null>(() => {
  const source = props.benchmark

  if (!source?.length) {
    return null
  }

  if (source.length !== props.equityCurve.length) {
    return null
  }

  for (let index = 0; index < source.length; index += 1) {
    if (source[index].date !== props.equityCurve[index].date) {
      return null
    }
  }

  return source
})

/**
 * 把成交通道的日期映射到权益曲线上的坐标。
 *
 * 成交日若不在权益曲线中（例如最后一天强制平仓后），
 * 该点会被跳过，避免画到错误位置。
 */
const buyPoints = computed<[string, number][][]>(() => {
  const byDate = new Map(props.equityCurve.map((p) => [p.date, p.equity]))
  const points: [string, number][] = []
  for (const m of buyMarkers.value) {
    const equity = byDate.get(m.date)
    if (equity !== undefined) {
      points.push([m.date, equity])
    }
  }
  return [points]
})

const sellPoints = computed<[string, number][][]>(() => {
  const byDate = new Map(props.equityCurve.map((p) => [p.date, p.equity]))
  const points: [string, number][] = []
  for (const m of sellMarkers.value) {
    const equity = byDate.get(m.date)
    if (equity !== undefined) {
      points.push([m.date, equity])
    }
  }
  return [points]
})

function buildOption(): echarts.EChartsOption {
  const dates = props.equityCurve.map((p) => p.date)
  const equity = props.equityCurve.map((p) => p.equity)
  const positionValue = props.equityCurve.map((p) => p.position_value)

  const series: echarts.SeriesOption[] = [
    {
      name: '总权益',
      type: 'line',
      data: equity,
      showSymbol: false,
      smooth: false,
      lineStyle: { width: 1.6, color: '#409eff' },
      itemStyle: { color: '#409eff' },
      areaStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: 'rgba(64,158,255,0.22)' },
          { offset: 1, color: 'rgba(64,158,255,0.01)' },
        ]),
      },
      z: 3,
    },
    {
      name: '持仓市值',
      type: 'line',
      data: positionValue,
      showSymbol: false,
      lineStyle: { width: 1, color: '#e6a23c', type: 'dashed' },
      itemStyle: { color: '#e6a23c' },
      z: 2,
    },
    {
      name: '买入',
      type: 'scatter',
      data: buyPoints.value[0],
      symbol: 'triangle',
      symbolSize: 7,
      itemStyle: { color: '#f56c6c' },
      tooltip: {
        formatter: (params: unknown) => {
          const p = params as { value: [string, number] }
          return `买入 ${p.value[0]}<br/>权益 ${formatAmount(p.value[1])}`
        },
      },
      z: 5,
    },
    {
      name: '卖出',
      type: 'scatter',
      data: sellPoints.value[0],
      symbol: 'diamond',
      symbolSize: 7,
      itemStyle: { color: '#67c23a' },
      tooltip: {
        formatter: (params: unknown) => {
          const p = params as { value: [string, number] }
          return `卖出 ${p.value[0]}<br/>权益 ${formatAmount(p.value[1])}`
        },
      },
      z: 5,
    },
  ]

  if (
    props.initialCapital !== null &&
    props.initialCapital !== undefined
  ) {
    series.push({
      name: '初始资金',
      type: 'line',
      data: dates.map(() => props.initialCapital as number),
      showSymbol: false,
      lineStyle: { width: 1, color: '#c0c4cc', type: 'dotted' },
      tooltip: { show: false },
      z: 1,
    })
  }

  // 基准曲线：仅在对齐校验通过时追加。
  // 未对齐时静默跳过，页面侧会另行提示。
  if (alignedBenchmark.value) {
    series.push({
      name: '买入持有',
      type: 'line',
      data: alignedBenchmark.value.map((p) => p.equity),
      showSymbol: false,
      smooth: false,
      lineStyle: { width: 1.4, color: '#909399' },
      itemStyle: { color: '#909399' },
      z: 2,
    })
  }

  return {
    animation: false,
    grid: { left: 68, right: 24, top: 40, bottom: 56 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line' },
      confine: true,
    },
    legend: {
      data: [
        '总权益',
        '持仓市值',
        '初始资金',
        '买入',
        '卖出',
        ...(alignedBenchmark.value ? ['买入持有'] : []),
      ],
      top: 6,
      textStyle: { fontSize: 12 },
    },
    xAxis: {
      type: 'category',
      data: dates,
      boundaryGap: false,
      axisLine: { lineStyle: { color: '#dcdfe6' } },
      axisLabel: { fontSize: 11, color: '#909399' },
    },
    yAxis: {
      type: 'value',
      scale: true,
      name: '金额',
      nameTextStyle: { fontSize: 11, color: '#909399' },
      axisLabel: {
        fontSize: 11,
        color: '#909399',
        formatter: (value: number) =>
          Math.abs(value) >= 10000
            ? `${(value / 10000).toFixed(1)}万`
            : String(value),
      },
      splitLine: { lineStyle: { color: '#f2f6fc' } },
    },
    dataZoom: [
      {
        type: 'inside',
        start: 0,
        end: 100,
        zoomOnMouseWheel: true,
        moveOnMouseMove: true,
      },
      {
        type: 'slider',
        height: 20,
        bottom: 12,
        start: 0,
        end: 100,
        borderColor: '#dcdfe6',
        fillerColor: 'rgba(64,158,255,0.12)',
        handleStyle: { color: '#409eff' },
        textStyle: { fontSize: 10, color: '#909399' },
      },
    ],
    series,
  }
}

function render(): void {
  if (!chart.value) {
    return
  }
  chart.value.setOption(buildOption(), true)
}

function resize(): void {
  chart.value?.resize()
}

onMounted(() => {
  if (!chartRef.value) {
    return
  }
  chart.value = echarts.init(chartRef.value)
  render()
  window.addEventListener('resize', resize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  chart.value?.dispose()
  chart.value = null
})

watch(
  () => [
    props.equityCurve,
    props.trades,
    props.initialCapital,
    props.benchmark,
  ],
  () => render(),
)

/**
 * 基准曲线是否因未对齐而被跳过。
 *
 * 暴露给页面用于提示用户，
 * 避免出现「用户传了基准但图上没有」的无解释状态。
 */
const benchmarkSkipped = computed(
  () => Boolean(props.benchmark?.length) && alignedBenchmark.value === null,
)

defineExpose({ benchmarkSkipped })
</script>

<template>
  <el-card shadow="never" class="equity-card">
    <template #header>
      <div class="card-header">
        <span class="title">权益曲线</span>
        <span class="meta">
          {{ equityCurve.length }} 个交易日
          <template v-if="trades.length">
            · {{ trades.length }} 笔成交
          </template>
        </span>
      </div>
      <el-alert
        v-if="benchmarkSkipped"
        title="基准曲线与权益曲线日期未对齐，已跳过基准线绘制"
        type="warning"
        show-icon
        :closable="false"
        class="benchmark-warning"
      />
    </template>

    <el-empty
      v-if="!equityCurve.length"
      description="暂无权益数据"
      :image-size="80"
    />
    <div v-else ref="chartRef" class="chart-container"></div>
  </el-card>
</template>

<style scoped>
.equity-card {
  margin-bottom: 16px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.title {
  font-size: 15px;
  font-weight: 500;
}

.meta {
  font-size: 12px;
  color: #909399;
}

.chart-container {
  width: 100%;
  height: 420px;
}

.benchmark-warning {
  margin-top: 8px;
}
</style>
