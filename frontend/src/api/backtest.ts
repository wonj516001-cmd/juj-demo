/**
 * 回测 API 请求封装。
 *
 * 约定：
 *   - 后端地址只在 vite.config.ts 的 proxy 中配置，
 *     业务代码一律使用相对路径 /api/xxx，
 *     避免把地址硬编码进组件。
 *   - 统一把后端错误转换为 ApiCallError，
 *     组件只需 catch 一次即可拿到可展示的信息。
 */

import axios, { AxiosError, type AxiosInstance } from 'axios'

import type {
  ApiErrorCode,
  ApiErrorResponse,
  StrategyTypesResponse,
} from '@/types/backtest'

/** 请求超时（毫秒）。回测实测约 0.4s，30s 足够宽裕。 */
const REQUEST_TIMEOUT = 30_000

const http: AxiosInstance = axios.create({
  baseURL: '/api',
  timeout: REQUEST_TIMEOUT,
  headers: { 'Content-Type': 'application/json' },
})

/**
 * 面向用户的中文提示。
 *
 * 只依据已核实的错误码，不解析后端 message 文案，
 * 避免后端文案调整导致前端失效。
 *
 * 导出说明：
 *   Demo 接口（api/demo.ts）复用本表，
 *   避免维护两套错误码映射。
 */
export const CODE_MESSAGES: Record<ApiErrorCode, string> = {
  VALIDATION_ERROR: '请求参数格式不正确，请检查输入',
  INVALID_STRATEGY_TYPE: '不支持的策略类型',
  INVALID_PARAMETER: '策略参数不合法，请检查取值范围',
  INVALID_SYMBOL: '股票代码格式不正确',
  INVALID_DATE_RANGE: '日期区间不合法，开始日期不能晚于结束日期',
  INVALID_INITIAL_CAPITAL: '初始资金不合法',
  DATA_NOT_FOUND: '未找到该股票的行情数据，请确认代码是否正确',
  DATA_LOAD_FAILED: '行情数据加载失败，请稍后重试',
  DATA_EMPTY: '所选日期区间内没有行情数据，请调整日期',
  INSUFFICIENT_DATA: '所选区间内有效数据不足，请扩大日期范围',
  BACKTEST_FAILED: '回测执行失败，请稍后重试',
  NOT_FOUND: '接口不存在',
  METHOD_NOT_ALLOWED: '请求方法不被允许',
  INTERNAL_ERROR: '服务内部错误，请稍后重试',
}

/** 统一错误类型，组件只需判断 code。 */
export class ApiCallError extends Error {
  readonly code: ApiErrorCode | 'NETWORK_ERROR' | 'TIMEOUT'
  readonly field: string | null
  readonly status: number | null

  constructor(
    message: string,
    code: ApiCallError['code'],
    status: number | null,
    field: string | null = null,
  ) {
    super(message)
    this.name = 'ApiCallError'
    this.code = code
    this.status = status
    this.field = field
  }
}

/**
 * 把 Axios 错误转换为 ApiCallError。
 *
 * 只提取后端返回的结构化错误，绝不把
 * 堆栈、服务器路径或原始异常透出到界面。
 *
 * 导出说明：
 *   Demo 接口（api/demo.ts）复用本函数，
 *   避免维护两套错误转换逻辑。
 */
export function toApiError(error: unknown): ApiCallError {
  if (error instanceof ApiCallError) {
    return error
  }

  const axiosError = error as AxiosError<ApiErrorResponse>

  if (axiosError?.code === 'ECONNABORTED') {
    return new ApiCallError(
      '请求超时，请检查后端服务是否运行',
      'TIMEOUT',
      null,
    )
  }

  if (axiosError?.response) {
    const status = axiosError.response.status
    const detail = axiosError.response.data?.error

    if (detail?.code) {
      // 优先使用错误码对应的中文提示；
      // 后端 message 作为兜底（它已是面向用户的简短说明）。
      const mapped = CODE_MESSAGES[detail.code]
      return new ApiCallError(
        mapped ?? detail.message ?? '请求失败',
        detail.code,
        status,
        detail.field ?? null,
      )
    }

    const fallback =
      status >= 500
        ? '服务异常，请稍后重试'
        : '请求失败，请稍后重试'

    return new ApiCallError(fallback, 'INTERNAL_ERROR', status)
  }

  // 无 response：网络层错误（后端未启动、跨域被拦截等）
  return new ApiCallError(
    '无法连接后端服务，请确认后端已启动',
    'NETWORK_ERROR',
    null,
  )
}

/**
 * 获取支持的策略与参数规范。
 *
 * 该接口由后端真实提供（GET /api/strategy/types），
 * 在 Demo 模式下同样可用——它只描述策略元信息，
 * 不涉及任何行情数据。
 */
export async function fetchStrategyTypes(): Promise<StrategyTypesResponse> {
  try {
    const { data } = await http.get<StrategyTypesResponse>(
      '/strategy/types',
    )
    return data
  } catch (error) {
    throw toApiError(error)
  }
}
