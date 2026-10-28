/**
 * JUJ 回测 API 类型定义。
 *
 * 本文件严格对应后端实际契约，字段来源：
 *   - 真实请求：backend/api/strategy.py 的 StrategyBacktestRequest
 *   - 真实响应：backend/api/schemas.py 的 StrategyBacktestResponse
 *     （并已用真实响应样本逐字段核对）
 *   - Demo 请求：backend/api/demo.py 的 DemoBacktestRequest
 *   - Demo 响应：backend/api/demo.py 的 DemoBacktestResponse
 *     （字段经 model_json_schema 与实际响应逐项核对）
 *
 * 注意：
 *   profit_factor 与 return_rate 允许为 null，
 *   后端在数学上无定义时返回 null（不会返回 Infinity）。
 */

/** 后端支持的策略类型。 */
export type StrategyType =
  | 'ma_cross'
  | 'kdj'
  | 'bollinger'
  | 'rsi'
  | 'macd'

/** 回测请求体。 */
export interface BacktestRequest {
  strategy_type: StrategyType
  /** 策略参数；不同策略的键不同，缺省时后端使用默认值。 */
  parameters: Record<string, number>
  symbol: string
  /** YYYY-MM-DD */
  start_date: string
  /** YYYY-MM-DD */
  end_date: string
  initial_capital: number
}

/** 权益曲线上的单点。恒等式：equity === cash + position_value */
export interface EquityPoint {
  /** YYYY-MM-DD */
  date: string
  /** 当日收盘总权益 */
  equity: number
  /** 当日收盘现金 */
  cash: number
  /** 当日收盘持股数 */
  shares: number
  /** 当日收盘持仓市值 */
  position_value: number
}

/** 单笔完整交易（开仓到平仓配对）。 */
export interface TradeRecord {
  side: string
  /** YYYY-MM-DD */
  entry_date: string
  entry_price: number
  shares: number
  /** YYYY-MM-DD */
  exit_date: string
  exit_price: number
  commission: number
  exit_commission: number
  stamp_duty: number
  /** 净盈亏（已扣双边佣金与印花税） */
  pnl: number
  /** = pnl / (entry_price * shares)，分母无效时为 null */
  return_rate: number | null
  /** 是否为回测最后一天强制平仓 */
  forced_exit: boolean
}

/** 回测响应。 */
export interface BacktestResponse {
  strategy_type: string
  strategy: string
  symbol: string
  start_date: string
  end_date: string
  initial_capital: number
  final_capital: number
  /** 小数形式，0.3484 表示 34.84% */
  total_return: number
  annual_return: number
  /** 负数，-0.2943 表示 -29.43% */
  max_drawdown: number
  sharpe_ratio: number
  total_trades: number
  winning_trades: number
  losing_trades: number
  /** 小数形式 */
  win_rate: number
  /** 无亏损交易时为 null */
  profit_factor: number | null
  equity_curve: EquityPoint[]
  trades: TradeRecord[]
}

/**
 * 后端错误码。
 *
 * 来源：backend/api/schemas.py 的 ErrorCode。
 * 前端应基于此做分支，不要解析 message 文案。
 */
export type ApiErrorCode =
  | 'VALIDATION_ERROR'
  | 'INVALID_STRATEGY_TYPE'
  | 'INVALID_PARAMETER'
  | 'INVALID_SYMBOL'
  | 'INVALID_DATE_RANGE'
  | 'INVALID_INITIAL_CAPITAL'
  | 'DATA_NOT_FOUND'
  | 'DATA_LOAD_FAILED'
  | 'DATA_EMPTY'
  | 'INSUFFICIENT_DATA'
  | 'BACKTEST_FAILED'
  | 'NOT_FOUND'
  | 'METHOD_NOT_ALLOWED'
  | 'INTERNAL_ERROR'

/** 后端统一错误响应体。 */
export interface ApiErrorDetail {
  code: ApiErrorCode
  message: string
  field?: string | null
}

export interface ApiErrorResponse {
  error: ApiErrorDetail
}

/**
 * GET /api/strategy/types 的响应。
 *
 * 由后端 strategy_validator 的 STRATEGY_SPECS 生成，
 * 可用于动态渲染策略参数表单，避免前端硬编码范围。
 */
export interface StrategyParamSpec {
  /**
   * 后端声明的参数类型：'int' 或 'float'。
   *
   * 前端据此决定输入框的步长：
   *   int   → step=1 且 step-strictly
   *   float → 允许小数
   * 若一律用 step=1，布林带的 std_dev 等小数参数将无法编辑。
   */
  type: string
  default: number
  min: number
  max: number
}

export interface StrategySpec {
  strategy_type: StrategyType
  label: string
  parameters: Record<string, StrategyParamSpec>
}

export interface StrategyTypesResponse {
  strategies: StrategySpec[]
}

// ==========================================================
// Demo（合成行情）相关类型
// ==========================================================
//
// 背景：
//   Demo 走完全独立的接口 /api/demo/*，数据由后端在内存中生成，
//   不读取任何真实行情。
//
// 契约关系（已用 model_json_schema 核对）：
//   DemoBacktestResponse 继承 StrategyBacktestResponse，
//   后端真实回测响应的 18 个字段是它的严格子集。
//   因此前端可直接复用 EquityChart / TradeTable / MetricCards。

/**
 * 合成行情模式。
 *
 * 注意与 StrategyType 区分：
 *   StrategyType  —— 策略（ma_cross / kdj）
 *   DemoMarketMode —— 行情形态（trend / range / high_vol）
 *
 * 两者是完全不同的概念，不可混用。
 * 来源：backend/engine/demo_data.py 的 MARKET_MODES。
 */
export type DemoMarketMode = 'trend' | 'range' | 'high_vol'

/**
 * 买入持有基准曲线上的单点。
 *
 * ⚠️ 与 EquityPoint 的区别（关键）：
 *   后端 DemoBacktestResponse.benchmark 声明为 `list[dict]`，
 *   而 equity_curve 声明为 `list[EquityPoint]`。
 *   benchmark 每点只有 date 与 equity 两个字段，
 *   没有 cash / shares / position_value。
 *
 *   因此**不可**把 benchmark 声明为 EquityPoint[]，
 *   否则消费时会因缺失字段产生类型问题。
 */
export interface BenchmarkPoint {
  /** YYYY-MM-DD */
  date: string
  /**
   * 零成本理想曲线：
   *   不含佣金、印花税与滑点，
   *   因此会略高于同等条件下的策略成交结果。
   */
  equity: number
}

/**
 * Demo 回测响应 = 真实回测响应 + 合成数据标识 + 基准对比。
 */
export interface DemoBacktestResponse extends BacktestResponse {
  /** 恒为 'synthetic'，与真实行情响应（无此字段）区分 */
  data_source: 'synthetic'
  /** 恒为 true */
  is_demo: true
  /** 必须在界面显著展示 */
  disclaimer: string

  /** 行情形态 */
  mode: DemoMarketMode
  /** 行情形态中文标签（趋势 / 震荡 / 高波动） */
  mode_label: string
  /** 本次随机种子；相同种子必然生成相同数据 */
  seed: number
  /** 请求生成的交易日数量 */
  periods: number

  /**
   * 基准曲线。
   * 已按 equity_curve 的日期范围裁剪，与权益曲线逐日对齐，
   * 可直接用于在同一坐标系中对比。
   */
  benchmark: BenchmarkPoint[]

  /**
   * 基准总收益率，按【完整模拟区间】的首末收盘价计算，
   * 不受策略预热期裁剪影响。
   */
  benchmark_return: number

  /**
   * 基准在【权益曲线日期区间】内的收益率。
   *
   * 与 total_return 使用完全相同的起止日期与收益定义
   * （equity_start_date ~ equity_end_date，期初 -> 期末）。
   *
   * excess_return 即为 total_return - 本字段。
   * 与 benchmark_return 的差额来自策略预热期，
   * 因此不可用 benchmark_return 计算超额收益。
   */
  benchmark_curve_return: number

  /**
   * 同区间超额收益 = total_return - benchmark_curve_return。
   *
   * 两侧区间完全一致，因此该值可直接解读。
   *
   * ⚠️ 后端类型为 Optional，可能为 null，消费处必须判空。
   */
  excess_return: number | null

  /**
   * 权益曲线实际首日。
   *
   * 与 start_date 的区别：
   *   start_date       —— 请求的模拟区间起点
   *   equity_start_date —— 权益曲线实际起点
   *
   * 策略指标预热期内无法产生交易，
   * 引擎会丢弃预热期数据，因此两者通常不同。
   * 例如 MA 策略预热 59 个交易日，两者可相差两个多月。
   */
  equity_start_date: string

  /**
   * 权益曲线实际末日。
   * 预热只丢弃开头数据不丢弃结尾，通常与 end_date 相同。
   */
  equity_end_date: string
}

/**
 * Demo 回测请求体。
 *
 * ⚠️ 与 BacktestRequest 的结构差异：
 *   没有 symbol（合成数据没有股票代码概念）
 *   没有 end_date（区间长度由 periods 决定）
 *   多出 mode / seed / periods / base_price
 *
 * 后端 DemoBacktestRequest 的全部字段都有默认值，
 * 因此这里全部声明为可选。
 *
 * 数值约束（来自后端 model_json_schema）：
 *   seed            >= 0
 *   periods         2 ~ 4000
 *   base_price      >= 10
 *   initial_capital 1 ~ 1e12
 */
export interface DemoBacktestRequest {
  /** 策略类型，复用现有白名单 */
  strategy_type?: StrategyType
  /** 行情形态 */
  mode?: DemoMarketMode
  /** 随机种子，决定可复现性 */
  seed?: number
  /** 生成的交易日数量 */
  periods?: number
  /** 起始日期 YYYY-MM-DD；起始日为非工作日时会顺延到下一个工作日 */
  start_date?: string
  /** 起始价格；低于下界后端会返回 422 */
  base_price?: number
  /** 初始资金 */
  initial_capital?: number
  /** 策略参数；缺省时后端使用策略默认值 */
  parameters?: Record<string, number>
}

/**
 * 单个行情模式的描述项。
 *
 * 来源：后端 GET /api/demo/modes 返回的 modes 数组元素。
 * 数值字段用于展示行情特征，非必填参数。
 */
export interface DemoModeOption {
  /** 模式标识 */
  mode: DemoMarketMode
  /** 中文标签 */
  label: string
  /** 对数价格漂移（趋势强度） */
  mu: number
  /** 对数收益标准差（波动率） */
  sigma: number
  /** 均值回复强度；0 表示纯随机游走 */
  kappa: number
  /** 恒为 'synthetic' */
  data_source: 'synthetic'
  /** 恒为 true */
  is_demo: true
  /** 免责声明 */
  disclaimer: string
}

/**
 * GET /api/demo/modes 的响应。
 *
 * 三个标识字段在后端有默认值，
 * 但接口实际总会返回，因此声明为必填。
 */
export interface DemoModesResponse {
  modes: DemoModeOption[]
  data_source: 'synthetic'
  is_demo: true
  disclaimer: string
}
