<script setup lang="ts">
/**
 * Demo 回测参数表单。
 *
 * 与 BacktestForm.vue 的关系：
 *   两者【不共享代码】，因为请求模型不同：
 *     真实链路需要 symbol / start_date / end_date
 *     Demo 不需要 symbol 与 end_date（区间长度由 periods 决定）
 *
 *   但复用了相同的设计思路：
 *     - 策略参数范围从后端 /api/strategy/types 获取，失败回退常量
 *     - 参数默认值由后端规范驱动，切换策略时重置
 *     - submitting 本地标记拦截重复点击
 *     - 参数范围在提交前本地预校验
 *
 * 默认值策略：
 *   不在此处硬编码后端默认值。
 *   未由用户改动的字段直接不发送，交给后端默认，
 *   避免前后端默认值漂移。
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'

import { fetchStrategyTypes } from '@/api/backtest'
import { fetchDemoModes } from '@/api/demo'
import type {
  DemoBacktestRequest,
  DemoMarketMode,
  DemoModeOption,
  StrategyParamSpec,
  StrategySpec,
  StrategyType,
} from '@/types/backtest'

const props = defineProps<{
  loading: boolean
}>()

const emit = defineEmits<{
  (event: 'submit', payload: DemoBacktestRequest): void
}>()

/** 与后端 strategy_validator 的 STRATEGY_SPECS 保持一致的兜底值。 */
const FALLBACK_SPECS: StrategySpec[] = [
  {
    strategy_type: 'ma_cross',
    label: '均线交叉',
    parameters: {
      fast_period: { type: 'int', default: 20, min: 1, max: 250 },
      slow_period: { type: 'int', default: 60, min: 2, max: 500 },
    },
  },
  {
    strategy_type: 'kdj',
    label: 'KDJ 金叉死叉',
    parameters: {
      rsv_period: { type: 'int', default: 9, min: 2, max: 250 },
      k_period: { type: 'int', default: 3, min: 1, max: 100 },
      d_period: { type: 'int', default: 3, min: 1, max: 100 },
    },
  },
  {
    strategy_type: 'bollinger',
    label: '布林带',
    parameters: {
      period: { type: 'int', default: 20, min: 2, max: 250 },
      std_dev: { type: 'float', default: 2, min: 0.1, max: 10 },
    },
  },
  {
    strategy_type: 'rsi',
    label: 'RSI 超卖超买',
    parameters: {
      period: { type: 'int', default: 14, min: 2, max: 250 },
      oversold: { type: 'float', default: 30, min: 0, max: 99 },
      overbought: { type: 'float', default: 70, min: 1, max: 100 },
    },
  },
  {
    strategy_type: 'macd',
    label: 'MACD 金叉死叉',
    parameters: {
      fast_period: { type: 'int', default: 12, min: 1, max: 250 },
      slow_period: { type: 'int', default: 26, min: 2, max: 500 },
      signal_period: { type: 'int', default: 9, min: 1, max: 250 },
    },
  },
]

/** 模式列表兜底（后端不可达时仍可演示）。 */
const FALLBACK_MODES: DemoModeOption[] = [
  {
    mode: 'trend',
    label: '趋势',
    mu: 0.0008,
    sigma: 0.018,
    kappa: 0,
    data_source: 'synthetic',
    is_demo: true,
    disclaimer: '',
  },
  {
    mode: 'range',
    label: '震荡',
    mu: 0,
    sigma: 0.012,
    kappa: 0.05,
    data_source: 'synthetic',
    is_demo: true,
    disclaimer: '',
  },
  {
    mode: 'high_vol',
    label: '高波动',
    mu: 0,
    sigma: 0.045,
    kappa: 0,
    data_source: 'synthetic',
    is_demo: true,
    disclaimer: '',
  },
]

const formRef = ref<FormInstance>()

/**
 * 提交中的本地标记。
 *
 * loading 由父组件传入，它要等 emit 之后才置位；
 * 而表单校验 await 期间已经让出控制权，
 * 按钮仍可点击，会连发两次请求。
 * 这里在点击瞬间就置位，先于校验，杜绝重复提交。
 */
const submitting = ref(false)

const specs = ref<StrategySpec[]>(FALLBACK_SPECS)
const modes = ref<DemoModeOption[]>(FALLBACK_MODES)
/** 策略参数表单区域是否显示校验错误 */
const paramError = ref('')

/**
 * 表单状态。
 *
 * 全部字段都在后端有默认值，
 * 这里只作为 UI 展示与「用户是否改过」的载体。
 */
const form = reactive({
  mode: 'trend' as DemoMarketMode,
  strategy_type: 'kdj' as StrategyType,
  periods: 600,
  seed: 20240101,
  base_price: 10,
  initial_capital: 100000,
  start_date: '2020-01-01',
  parameters: {} as Record<string, number>,
})

/** 记录用户实际改动的字段，未改动的不发往后端。 */
const touched = reactive({
  periods: false,
  seed: false,
  base_price: false,
  initial_capital: false,
  start_date: false,
})

const currentSpec = computed<StrategySpec>(
  () =>
    specs.value.find((s) => s.strategy_type === form.strategy_type) ??
    specs.value[0],
)

interface ParamField extends StrategyParamSpec {
  key: string
}

const currentParams = computed<ParamField[]>(() =>
  Object.entries(currentSpec.value.parameters).map(([key, value]) => ({
    key,
    ...value,
  })),
)

const currentMode = computed<DemoModeOption>(
  () =>
    modes.value.find((m) => m.mode === form.mode) ?? modes.value[0],
)

/** 加载后端策略规范；失败时静默使用兜底常量。 */
async function loadSpecs(): Promise<void> {
  try {
    const data = await fetchStrategyTypes()
    if (data?.strategies?.length) {
      specs.value = data.strategies
    }
  } catch {
    specs.value = FALLBACK_SPECS
  }
}

/** 加载可用的合成行情模式。 */
async function loadModes(): Promise<void> {
  try {
    const data = await fetchDemoModes()
    if (data?.modes?.length) {
      modes.value = data.modes
    }
  } catch {
    modes.value = FALLBACK_MODES
  }
}

/** 按当前策略重置参数为默认值。 */
function applyDefaults(): void {
  const next: Record<string, number> = {}
  for (const [key, spec] of Object.entries(
    currentSpec.value.parameters,
  )) {
    next[key] = spec.default
  }
  form.parameters = next
  paramError.value = ''
}

onMounted(async () => {
  await Promise.all([loadSpecs(), loadModes()])
  applyDefaults()
})

watch(() => form.strategy_type, () => {
  applyDefaults()
})

/**
 * 数值约束，与后端 DemoBacktestRequest 的 model_json_schema 一致。
 */
const MIN_PERIODS = 2
const MAX_PERIODS = 4000
const MIN_BASE_PRICE = 10
const MIN_INITIAL_CAPITAL = 1
const MAX_INITIAL_CAPITAL = 1e12

const rules: FormRules = {
  periods: [
    { required: true, message: '请输入样本数', trigger: 'blur' },
    {
      type: 'number',
      min: MIN_PERIODS,
      max: MAX_PERIODS,
      message: `样本数需在 ${MIN_PERIODS} 到 ${MAX_PERIODS} 之间`,
      trigger: 'blur',
    },
  ],
  seed: [
    { required: true, message: '请输入随机种子', trigger: 'blur' },
    {
      type: 'number',
      min: 0,
      message: '种子不能为负',
      trigger: 'blur',
    },
  ],
  base_price: [
    { required: true, message: '请输入起始价格', trigger: 'blur' },
    {
      type: 'number',
      min: MIN_BASE_PRICE,
      message: `起始价格不能低于 ${MIN_BASE_PRICE}`,
      trigger: 'blur',
    },
  ],
  initial_capital: [
    { required: true, message: '请输入初始资金', trigger: 'blur' },
    {
      type: 'number',
      min: MIN_INITIAL_CAPITAL,
      max: MAX_INITIAL_CAPITAL,
      message: `初始资金需在 ${MIN_INITIAL_CAPITAL} 到 ${MAX_INITIAL_CAPITAL} 之间`,
      trigger: 'blur',
    },
  ],
  start_date: [
    { required: true, message: '请选择起始日期', trigger: 'change' },
  ],
}

async function handleSubmit(): Promise<void> {
  // 同步拦截重复点击，不等校验完成
  if (submitting.value) {
    return
  }

  if (!formRef.value) {
    return
  }

  submitting.value = true

  try {
    const valid = await formRef.value.validate().catch(() => false)
    if (!valid) {
      return
    }

    // 策略参数范围本地预校验（后端仍会再校验一次）
    for (const spec of currentParams.value) {
      const value = form.parameters[spec.key]
      if (
        value === undefined ||
        value < spec.min ||
        value > spec.max
      ) {
        paramError.value = `${spec.key} 需在 ${spec.min} 到 ${spec.max} 之间`
        return
      }
    }

    if (
      form.strategy_type === 'ma_cross' &&
      form.parameters.fast_period >= form.parameters.slow_period
    ) {
      paramError.value = 'fast_period 必须小于 slow_period'
      return
    }

    paramError.value = ''

    // 只发送 Demo 接口支持的字段。
    // 绝不传 symbol / end_date —— 后端 extra="forbid"，传入即 422。
    const payload: DemoBacktestRequest = {
      strategy_type: form.strategy_type,
      parameters: { ...form.parameters },
      mode: form.mode,
    }

    // 未改动的字段不发送，交给后端默认值。
    // 这样前端不必重复维护后端的默认值。
    if (touched.periods) payload.periods = form.periods
    if (touched.seed) payload.seed = form.seed
    if (touched.base_price) payload.base_price = form.base_price
    if (touched.initial_capital) {
      payload.initial_capital = form.initial_capital
    }
    if (touched.start_date) payload.start_date = form.start_date

    emit('submit', payload)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-card shadow="never" class="demo-form">
    <template #header>
      <div class="card-header">
        <span class="title">模拟回测参数</span>
        <el-tag type="warning" size="small" effect="plain">
          合成数据
        </el-tag>
      </div>
    </template>

    <el-form
      ref="formRef"
      :model="form"
      :rules="rules"
      label-width="104px"
      @submit.prevent="handleSubmit"
    >
      <el-row :gutter="16">
        <el-col :xs="24" :sm="12" :md="8">
          <el-form-item label="行情模式" prop="mode">
            <el-select v-model="form.mode" style="width: 100%">
              <el-option
                v-for="item in modes"
                :key="item.mode"
                :label="`${item.label}（${item.mode}）`"
                :value="item.mode"
              />
            </el-select>
            <div class="field-hint">
              日波动率 {{ (currentMode.sigma * 100).toFixed(1) }}%
            </div>
          </el-form-item>
        </el-col>

        <el-col :xs="24" :sm="12" :md="8">
          <el-form-item label="策略">
            <el-select
              v-model="form.strategy_type"
              style="width: 100%"
            >
              <el-option
                v-for="item in specs"
                :key="item.strategy_type"
                :label="`${item.label}（${item.strategy_type}）`"
                :value="item.strategy_type"
              />
            </el-select>
          </el-form-item>
        </el-col>

        <el-col :xs="24" :sm="12" :md="8">
          <el-form-item label="样本数" prop="periods">
            <el-input-number
              v-model="form.periods"
              :min="MIN_PERIODS"
              :max="MAX_PERIODS"
              :step="100"
              :controls="false"
              style="width: 100%"
              @change="touched.periods = true"
            />
            <div class="field-hint">
              交易日数量，指标预热会占用其中一部分
            </div>
          </el-form-item>
        </el-col>

        <el-col :xs="24" :sm="12" :md="8">
          <el-form-item label="随机种子" prop="seed">
            <el-input-number
              v-model="form.seed"
              :min="0"
              :step="1"
              step-strictly
              :controls="false"
              style="width: 100%"
              @change="touched.seed = true"
            />
            <div class="field-hint">
              相同种子必然生成相同数据
            </div>
          </el-form-item>
        </el-col>

        <el-col :xs="24" :sm="12" :md="8">
          <el-form-item label="起始价格" prop="base_price">
            <el-input-number
              v-model="form.base_price"
              :min="MIN_BASE_PRICE"
              :step="1"
              :controls="false"
              style="width: 100%"
              @change="touched.base_price = true"
            />
            <div class="field-hint">
              不能低于 {{ MIN_BASE_PRICE }}
            </div>
          </el-form-item>
        </el-col>

        <el-col :xs="24" :sm="12" :md="8">
          <el-form-item label="初始资金" prop="initial_capital">
            <el-input-number
              v-model="form.initial_capital"
              :min="MIN_INITIAL_CAPITAL"
              :max="MAX_INITIAL_CAPITAL"
              :step="10000"
              :controls="false"
              style="width: 100%"
              @change="touched.initial_capital = true"
            />
          </el-form-item>
        </el-col>

        <el-col :xs="24" :sm="12" :md="8">
          <el-form-item label="起始日期" prop="start_date">
            <el-date-picker
              v-model="form.start_date"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="选择起始日期"
              style="width: 100%"
              @change="touched.start_date = true"
            />
            <div class="field-hint">
              周末与假日会自动顺延到下一个工作日
            </div>
          </el-form-item>
        </el-col>
      </el-row>

      <el-divider content-position="left">
        <span class="divider-text">策略参数</span>
      </el-divider>

      <el-row :gutter="16">
        <el-col
          v-for="spec in currentParams"
          :key="spec.key"
          :xs="24"
          :sm="12"
          :md="8"
        >
          <el-form-item :label="spec.key">
            <el-input-number
              v-model="form.parameters[spec.key]"
              :min="spec.min"
              :max="spec.max"
              :step="spec.type === 'float' ? 0.1 : 1"
              :step-strictly="spec.type !== 'float'"
              :precision="spec.type === 'float' ? 2 : undefined"
              style="width: 100%"
            />
          </el-form-item>
        </el-col>
      </el-row>

      <el-alert
        v-if="paramError"
        :title="paramError"
        type="error"
        show-icon
        :closable="false"
        class="param-error"
      />

      <div class="form-actions">
        <el-button
          type="primary"
          :loading="props.loading || submitting"
          @click="handleSubmit"
        >
          {{ props.loading || submitting ? '回测中…' : '开始模拟回测' }}
        </el-button>
        <span class="hint">
          行情模式来自 /api/demo/modes，参数范围来自 /api/strategy/types
        </span>
      </div>
    </el-form>
  </el-card>
</template>

<style scoped>
.demo-form {
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

.divider-text {
  font-size: 13px;
  color: #909399;
}

.field-hint {
  font-size: 12px;
  color: #c0c4cc;
  line-height: 1.4;
  margin-top: 2px;
}

.param-error {
  margin-bottom: 12px;
}

.form-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.hint {
  font-size: 12px;
  color: #909399;
}
</style>
