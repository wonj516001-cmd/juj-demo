<script setup lang="ts">
/**
 * 模拟回测页面。
 *
 * ==============================================================
 * 数据性质（重要）
 * ==============================================================
 * 本页面的全部结果均基于【合成行情】，
 * 由后端在内存中用数学模型生成，
 * 不是任何真实证券的历史价格。
 *
 * 结果不代表实际交易表现，不构成任何投资建议。
 *
 * ==============================================================
 * 与 BacktestView.vue 的关系
 * ==============================================================
 * 两者共用 MetricCards / TradeTable / EquityChart 三个展示组件，
 * 但各自持有独立的请求逻辑与表单：
 *   - 请求模型不同（Demo 无 symbol / end_date）
 *   - 摘要语义不同（Demo 有两套区间与基准对比）
 *   - 错误语义不同（Demo 会返回 VALIDATION_ERROR 等）
 *
 * 因此不复用真实回测页面本身，
 * 只复用其中的并发守卫与错误处理思路。
 */
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'

import DemoForm from '@/components/DemoForm.vue'
import EquityChart from '@/components/EquityChart.vue'
import MetricCards from '@/components/MetricCards.vue'
import TradeTable from '@/components/TradeTable.vue'
import { ApiCallError, runDemoBacktest } from '@/api/demo'
import type {
  DemoBacktestRequest,
  DemoBacktestResponse,
} from '@/types/backtest'
import {
  formatAmount,
  formatRatio,
  formatSignedPercent,
  valueColor,
} from '@/utils/format'

const loading = ref(false)
const result = ref<DemoBacktestResponse | null>(null)
const errorMessage = ref('')

/**
 * 并发守卫。
 *
 * 沿用 BacktestView 的思路：
 * 用自增序号保证「只有最后一次请求的结果被采纳」，
 * 避免较早返回的请求覆盖最新结果。
 */
let requestSeq = 0

const equityCurve = computed(() => result.value?.equity_curve ?? [])
const trades = computed(() => result.value?.trades ?? [])
const benchmark = computed(() => result.value?.benchmark ?? [])
const initialCapital = computed(
  () => result.value?.initial_capital ?? null,
)

/**
 * 摘要条。
 *
 * 同时展示两套区间：
 *   模拟区间   —— 用户请求生成的数据范围
 *   实际回测   —— 权益曲线的真实范围（已扣除指标预热期）
 *
 * 两者不同是正常现象：策略指标在预热期内无法产生交易，
 * 引擎会丢弃预热期数据。MA 策略预热 59 个交易日，
 * 两者可相差两个多月。
 */
const summary = computed(() => {
  const r = result.value
  if (!r) {
    return ''
  }

  const parts = [
    r.strategy,
    `${r.mode_label}行情`,
    `模拟区间 ${r.start_date} ~ ${r.end_date}`,
    `实际回测 ${r.equity_start_date} ~ ${r.equity_end_date}`,
    `权益点 ${r.equity_curve.length}`,
    `成交 ${r.total_trades} 笔`,
  ]

  // 起点与请求不一致时，说明策略指标预热占用了数据。
  // 显式说明可避免用户误以为「数据丢了」。
  if (r.equity_start_date !== r.start_date) {
    parts.push('含指标预热期')
  }

  return parts.join(' · ')
})

/** 基准对比指标。 */
const benchmarkCards = computed(() => {
  const r = result.value
  if (!r) {
    return []
  }

  return [
    {
      label: '超额收益',
      value:
        r.excess_return === null
          ? '—'
          : formatSignedPercent(r.excess_return),
      color: valueColor(r.excess_return),
      hint: `同区间：${r.equity_start_date} ~ ${r.equity_end_date}`,
    },
    {
      label: '基准收益（同区间）',
      value: formatSignedPercent(r.benchmark_curve_return),
      color: valueColor(r.benchmark_curve_return),
      hint: `与策略同区间，供超额计算`,
    },
    {
      label: '基准收益（完整区间）',
      value: formatSignedPercent(r.benchmark_return),
      color: valueColor(r.benchmark_return),
      hint: `${r.start_date} ~ ${r.end_date} 全程买入持有`,
    },
    {
      label: 'Profit Factor',
      value:
        r.profit_factor === null
          ? '—'
          : formatRatio(r.profit_factor, 3),
      color: valueColor(r.profit_factor),
      hint:
        r.profit_factor === null ? '无亏损交易时无定义' : undefined,
    },
    {
      label: '期末资金',
      value: formatAmount(r.final_capital),
      hint: `初始 ${formatAmount(r.initial_capital)}`,
    },
  ]
})

async function handleSubmit(
  payload: DemoBacktestRequest,
): Promise<void> {
  const current = ++requestSeq

  loading.value = true
  errorMessage.value = ''

  try {
    const data = await runDemoBacktest(payload)

    // 已有更新的请求发出，丢弃本次结果
    if (current !== requestSeq) {
      return
    }

    result.value = data

    if (!data.equity_curve.length) {
      ElMessage.warning('回测完成，但未返回权益曲线数据')
    } else if (!data.trades.length) {
      ElMessage.info('回测完成，该区间内没有产生交易')
    } else {
      ElMessage.success('模拟回测完成')
    }
  } catch (error) {
    // 同上，旧请求的失败不覆盖新状态
    if (current !== requestSeq) {
      return
    }

    const message =
      error instanceof ApiCallError
        ? error.message
        : '模拟回测请求失败，请稍后重试'

    errorMessage.value = message
    // 清除旧结果，避免用户把上一次的模拟结果误认为本次结果
    result.value = null
    ElMessage.error(message)
  } finally {
    if (current === requestSeq) {
      loading.value = false
    }
  }
}
</script>

<template>
  <div class="demo-view">
    <!-- 常驻免责声明：不可关闭，始终位于页面顶部 -->
    <el-alert
      title="模拟数据，非真实行情，不代表实际交易表现"
      description="本页所有行情由数学模型在内存中生成，不来自任何真实证券历史价格。结果仅用于演示回测链路，不构成投资建议。买入持有基准曲线为零成本理想曲线，未计入佣金、印花税与滑点，因此与已计入这些成本的策略曲线并非完全同口径。"
      type="warning"
      show-icon
      :closable="false"
      class="disclaimer"
    />

    <DemoForm :loading="loading" @submit="handleSubmit" />

    <el-alert
      v-if="errorMessage"
      class="error-alert"
      :title="errorMessage"
      type="error"
      show-icon
      closable
      @close="errorMessage = ''"
    />

    <template v-if="result">
      <div class="summary-bar">{{ summary }}</div>

      <MetricCards :result="result" />

      <div v-if="benchmarkCards.length" class="benchmark-cards">
        <div
          v-for="item in benchmarkCards"
          :key="item.label"
          class="benchmark-card"
        >
          <div class="benchmark-label">{{ item.label }}</div>
          <div
            class="benchmark-value"
            :style="{ color: item.color || '#303133' }"
          >
            {{ item.value }}
          </div>
          <div v-if="item.hint" class="benchmark-hint">
            {{ item.hint }}
          </div>
        </div>
      </div>

      <el-alert
        title="超额收益口径说明"
        description="超额收益 = 策略总收益率 − 同区间基准收益率。两侧区间完全一致，均为权益曲线区间（已扣除策略指标预热期），因此该数值可直接解读。页面同时列出「基准收益（完整区间）」，它按用户请求的完整模拟区间计算，覆盖区间更长，仅供了解该期间单纯持有的整体表现，不能与策略收益直接相减。所有基准均为零成本买入持有曲线，未计入佣金、印花税与滑点。"
        type="info"
        show-icon
        :closable="false"
        class="method-note"
      />

      <EquityChart
        :equity-curve="equityCurve"
        :trades="trades"
        :initial-capital="initialCapital"
        :benchmark="benchmark"
      />

      <TradeTable :trades="trades" />
    </template>

    <el-empty
      v-else-if="!loading && !errorMessage"
      description="选择行情模式后点击「开始模拟回测」"
      :image-size="110"
    />
  </div>
</template>

<style scoped>
.demo-view {
  padding: 4px;
}

.disclaimer {
  margin-bottom: 16px;
}

.error-alert {
  margin-bottom: 16px;
}

.summary-bar {
  font-size: 12px;
  color: #909399;
  margin-bottom: 10px;
  font-family: 'SF Mono', Consolas, Menlo, monospace;
  line-height: 1.6;
}

.benchmark-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.benchmark-card {
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 14px 16px;
}

.benchmark-label {
  font-size: 13px;
  color: #909399;
  margin-bottom: 6px;
}

.benchmark-value {
  font-size: 22px;
  font-weight: 500;
  font-family: 'SF Mono', Consolas, Menlo, monospace;
  line-height: 1.3;
}

.benchmark-hint {
  font-size: 12px;
  color: #c0c4cc;
  margin-top: 4px;
}

.method-note {
  margin-bottom: 16px;
}
</style>
