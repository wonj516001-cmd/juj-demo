/**
 * 展示格式化工具。
 *
 * 约定：
 *   - 金额保留两位小数
 *   - 收益率类小数转百分比
 *   - null / undefined 显示为「—」，而不是 0 或 NaN
 */

/** 空值占位符。 */
export const EMPTY_PLACEHOLDER = '—'

/** 判断是否为空值。 */
function isEmpty(value: number | null | undefined): boolean {
  return value === null || value === undefined || Number.isNaN(value)
}

/** 金额：千分位 + 两位小数。 */
export function formatAmount(value: number | null | undefined): string {
  if (isEmpty(value)) {
    return EMPTY_PLACEHOLDER
  }
  return (value as number).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

/** 带符号金额，用于盈亏展示。 */
export function formatSignedAmount(
  value: number | null | undefined,
): string {
  if (isEmpty(value)) {
    return EMPTY_PLACEHOLDER
  }
  const numeric = value as number
  const formatted = Math.abs(numeric).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
  return numeric >= 0 ? `+${formatted}` : `-${formatted}`
}

/**
 * 百分比：把小数转成百分比字符串。
 *
 * 例：0.3484 -> "34.84%"
 *     -0.2943 -> "-29.43%"
 */
export function formatPercent(
  value: number | null | undefined,
  digits = 2,
): string {
  if (isEmpty(value)) {
    return EMPTY_PLACEHOLDER
  }
  return `${((value as number) * 100).toFixed(digits)}%`
}

/** 带符号百分比，用于收益率展示。 */
export function formatSignedPercent(
  value: number | null | undefined,
  digits = 2,
): string {
  if (isEmpty(value)) {
    return EMPTY_PLACEHOLDER
  }
  const numeric = value as number
  const formatted = `${(Math.abs(numeric) * 100).toFixed(digits)}%`
  return numeric >= 0 ? `+${formatted}` : `-${formatted}`
}

/** 比率：保留两位小数，不加百分号（用于 Sharpe）。 */
export function formatRatio(
  value: number | null | undefined,
  digits = 4,
): string {
  if (isEmpty(value)) {
    return EMPTY_PLACEHOLDER
  }
  return (value as number).toFixed(digits)
}

/** 整数（股数、次数）。 */
export function formatInteger(
  value: number | null | undefined,
): string {
  if (isEmpty(value)) {
    return EMPTY_PLACEHOLDER
  }
  return Math.round(value as number).toLocaleString('zh-CN')
}

/** 价格：保留四位小数。 */
export function formatPrice(
  value: number | null | undefined,
): string {
  if (isEmpty(value)) {
    return EMPTY_PLACEHOLDER
  }
  return (value as number).toFixed(4)
}

/** 根据数值正负返回语义色（Element Plus 主题色）。 */
export function valueColor(
  value: number | null | undefined,
): string {
  if (isEmpty(value)) {
    return '#909399'
  }
  const numeric = value as number
  if (numeric > 0) {
    // A 股习惯：涨为红，跌为绿
    return '#f56c6c'
  }
  if (numeric < 0) {
    return '#67c23a'
  }
  return '#909399'
}
