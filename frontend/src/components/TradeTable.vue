<script setup lang="ts">
/**
 * 交易明细表。
 *
 * 字段全部取自后端 trades，未做任何再计算。
 * return_rate 为 null 时显示「—」。
 */
import { computed, ref, watch } from 'vue'

import type { TradeRecord } from '@/types/backtest'
import {
  EMPTY_PLACEHOLDER,
  formatInteger,
  formatPrice,
  formatSignedAmount,
  formatSignedPercent,
  valueColor,
} from '@/utils/format'

const props = defineProps<{
  trades: TradeRecord[]
}>()

const pageSize = ref(20)
const currentPage = ref(1)

const total = computed(() => props.trades.length)

const pagedTrades = computed<TradeRecord[]>(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return props.trades.slice(start, start + pageSize.value)
})

watch(
  () => props.trades,
  () => {
    // 换一批数据时回到第一页
    currentPage.value = 1
  },
)

/** 组合单笔总费用，便于核对。 */
function totalFee(row: TradeRecord): number {
  return row.commission + row.exit_commission + row.stamp_duty
}
</script>

<template>
  <el-card shadow="never" class="trade-card">
    <template #header>
      <div class="card-header">
        <span class="title">交易明细</span>
        <span class="meta">
          {{ total }} 笔
          <template v-if="total">
            · 第 {{ currentPage }} / {{ Math.ceil(total / pageSize) || 1 }} 页
          </template>
        </span>
      </div>
    </template>

    <el-empty
      v-if="!total"
      description="该区间内没有产生交易"
      :image-size="90"
    />

    <template v-else>
      <el-table
        :data="pagedTrades"
        stripe
        size="small"
        border
        style="width: 100%"
      >
        <el-table-column type="index" label="#" width="56" align="center">
          <template #default="scope">
            {{ (currentPage - 1) * pageSize + scope.$index + 1 }}
          </template>
        </el-table-column>

        <el-table-column
          prop="entry_date"
          label="买入日期"
          width="112"
          sortable
        />
        <el-table-column
          prop="exit_date"
          label="卖出日期"
          width="112"
          sortable
        />
        <el-table-column label="买入价" width="104" align="right">
          <template #default="scope">
            {{ formatPrice(scope.row.entry_price) }}
          </template>
        </el-table-column>
        <el-table-column label="卖出价" width="104" align="right">
          <template #default="scope">
            {{ formatPrice(scope.row.exit_price) }}
          </template>
        </el-table-column>
        <el-table-column label="数量" width="92" align="right">
          <template #default="scope">
            {{ formatInteger(scope.row.shares) }}
          </template>
        </el-table-column>

        <el-table-column label="盈亏" width="126" align="right" sortable>
          <template #default="scope">
            <span :style="{ color: valueColor(scope.row.pnl) }">
              {{ formatSignedAmount(scope.row.pnl) }}
            </span>
          </template>
        </el-table-column>

        <el-table-column label="收益率" width="104" align="right" sortable>
          <template #default="scope">
            <span :style="{ color: valueColor(scope.row.return_rate) }">
              {{
                scope.row.return_rate === null
                  ? EMPTY_PLACEHOLDER
                  : formatSignedPercent(scope.row.return_rate)
              }}
            </span>
          </template>
        </el-table-column>

        <el-table-column label="总费用" width="110" align="right">
          <template #default="scope">
            {{ formatPrice(totalFee(scope.row)) }}
          </template>
        </el-table-column>

        <el-table-column label="强制平仓" width="92" align="center">
          <template #default="scope">
            <el-tag
              v-if="scope.row.forced_exit"
              type="warning"
              size="small"
              effect="light"
            >
              是
            </el-tag>
            <span v-else class="text-muted">—</span>
          </template>
        </el-table-column>

        <el-table-column type="expand">
          <template #default="scope">
            <div class="detail-grid">
              <div><span>方向</span>{{ scope.row.side }}</div>
              <div>
                <span>买入佣金</span>{{ formatPrice(scope.row.commission) }}
              </div>
              <div>
                <span>卖出佣金</span>{{ formatPrice(scope.row.exit_commission) }}
              </div>
              <div><span>印花税</span>{{ formatPrice(scope.row.stamp_duty) }}</div>
              <div>
                <span>净盈亏</span>
                <b :style="{ color: valueColor(scope.row.pnl) }">
                  {{ formatSignedAmount(scope.row.pnl) }}
                </b>
              </div>
              <div>
                <span>收益率</span>
                {{
                  scope.row.return_rate === null
                    ? EMPTY_PLACEHOLDER
                    : formatSignedPercent(scope.row.return_rate)
                }}
              </div>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        v-model:current-page="currentPage"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[20, 50, 100, 200]"
        layout="total, sizes, prev, pager, next, jumper"
        background
        class="pagination"
      />
    </template>
  </el-card>
</template>

<style scoped>
.trade-card {
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

.text-muted {
  color: #c0c4cc;
}

.pagination {
  margin-top: 14px;
  justify-content: flex-end;
}

.detail-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 8px 20px;
  padding: 8px 48px;
  font-size: 13px;
}

.detail-grid span {
  color: #909399;
  margin-right: 8px;
}
</style>
