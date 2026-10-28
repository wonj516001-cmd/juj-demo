<script setup lang="ts">
/**
 * 回测指标卡片。
 *
 * 全部字段直接取自后端响应，不做任何再计算，
 * null 一律显示为「—」。
 */
import { computed } from 'vue'

import type { BacktestResponse } from '@/types/backtest'
import {
  formatAmount,
  formatInteger,
  formatPercent,
  formatRatio,
  formatSignedPercent,
  valueColor,
} from '@/utils/format'

const props = defineProps<{
  result: BacktestResponse | null
}>()

interface MetricItem {
  label: string
  value: string
  color?: string
  hint?: string
}

const metrics = computed<MetricItem[]>(() => {
  const r = props.result
  if (!r) {
    return []
  }

  return [
    {
      label: '最终资金',
      value: formatAmount(r.final_capital),
      hint: `初始 ${formatAmount(r.initial_capital)}`,
    },
    {
      label: '总收益率',
      value: formatSignedPercent(r.total_return),
      color: valueColor(r.total_return),
    },
    {
      label: '年化收益率',
      value: formatSignedPercent(r.annual_return),
      color: valueColor(r.annual_return),
    },
    {
      label: '最大回撤',
      value: formatPercent(r.max_drawdown),
      color: valueColor(r.max_drawdown),
    },
    {
      label: 'Sharpe Ratio',
      value: formatRatio(r.sharpe_ratio),
      color: valueColor(r.sharpe_ratio),
    },
    {
      label: 'Profit Factor',
      value:
        r.profit_factor === null ? '—' : formatRatio(r.profit_factor, 3),
      color: valueColor(r.profit_factor),
      hint: r.profit_factor === null ? '无亏损交易时无定义' : undefined,
    },
    {
      label: '交易次数',
      value: formatInteger(r.total_trades),
      hint: `盈 ${r.winning_trades} / 亏 ${r.losing_trades}`,
    },
    {
      label: '胜率',
      value: formatPercent(r.win_rate),
    },
  ]
})
</script>

<template>
  <div v-if="metrics.length" class="metric-cards">
    <div
      v-for="item in metrics"
      :key="item.label"
      class="metric-card"
    >
      <div class="metric-label">{{ item.label }}</div>
      <div
        class="metric-value"
        :style="{ color: item.color || '#303133' }"
      >
        {{ item.value }}
      </div>
      <div v-if="item.hint" class="metric-hint">
        {{ item.hint }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.metric-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.metric-card {
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 14px 16px;
}

.metric-label {
  font-size: 13px;
  color: #909399;
  margin-bottom: 6px;
}

.metric-value {
  font-size: 22px;
  font-weight: 500;
  font-family: 'SF Mono', Consolas, Menlo, monospace;
  line-height: 1.3;
}

.metric-hint {
  font-size: 12px;
  color: #c0c4cc;
  margin-top: 4px;
}
</style>
