/**
 * JUJ Demo 回测 API —— 合成行情演示接口。
 *
 * ==============================================================
 * 与真实行情接口的关系
 * ==============================================================
 * 本模块对应后端 /api/demo/*，数据由后端在内存中生成，
 * **不读取任何真实行情文件**。
 *
 * 请求约定与真实链路完全一致：
 *   - baseURL 为 '/api'，经 vite dev proxy 转发到后端 8001
 *     （见 vite.config.ts 的 server.proxy）
 *   - Content-Type 与超时沿用同一约定
 *   - 错误统一经 toApiError 转为 ApiCallError
 *
 * ==============================================================
 * 复用策略
 * ==============================================================
 * 刻意不在本文件复制错误码表或错误转换逻辑：
 *   - CODE_MESSAGES 与 toApiError 直接从 ./backtest 导入
 *   - Demo 只会产生 4 种错误码
 *     （VALIDATION_ERROR / INVALID_STRATEGY_TYPE /
 *      INVALID_PARAMETER / INSUFFICIENT_DATA），
 *     它们全部已在 ApiErrorCode 中声明，
 *     因此复用后自动获得与真实链路一致的中文提示。
 *
 * ==============================================================
 * 数据性质
 * ==============================================================
 * 响应恒定携带 data_source="synthetic"、is_demo=true
 * 与免责声明，必须在界面显著展示。
 */

import axios, { type AxiosInstance } from 'axios'

import { toApiError } from './backtest'
import type {
  DemoBacktestRequest,
  DemoBacktestResponse,
  DemoModesResponse,
} from '@/types/backtest'

/**
 * Demo 专用 axios 实例。
 *
 * 与 ./backtest 中的实例配置相同但独立创建：
 *   - 独立实例让 Demo 与真实链路在拦截器、
 *     后续扩展（如鉴权头）上互不影响；
 *   - baseURL 与代理路径保持一致，因此
 *     浏览器看到的都是同源请求，无需 CORS。
 *
 * 超时沿用 30 秒：回测实测远快于此，
 * 4000 周期在本机约 1.3 秒。
 */
const http: AxiosInstance = axios.create({
  baseURL: '/api',
  timeout: 30_000,
  headers: { 'Content-Type': 'application/json' },
})

/**
 * 查询可用的合成行情模式。
 *
 * GET /api/demo/modes
 *
 * 返回的模式列表供前端渲染选择器，
 * 避免在前端硬编码模式清单。
 *
 * 该接口不会失败 —— 它不依赖任何外部数据源，
 * 只返回代码内定义的常量。
 */
export async function fetchDemoModes(): Promise<DemoModesResponse> {
  try {
    const { data } = await http.get<DemoModesResponse>(
      '/demo/modes',
    )
    return data
  } catch (error) {
    throw toApiError(error)
  }
}

/**
 * 使用合成行情执行回测。
 *
 * POST /api/demo/backtest
 *
 * 参数 payload 的全部字段在后端都有默认值，
 * 因此可以传入空对象使用后端默认配置。
 *
 * 注意：
 *   请求体中【不允许】出现 symbol。
 *   后端 DemoBacktestRequest 的 extra="forbid"，
 *   传入该字段会得到 422。
 *   这是刻意的结构性隔离——
 *   Demo 在模型层就不可能指向真实行情。
 *
 * 错误语义（后端已核实）：
 *   400 VALIDATION_ERROR       模式非法 / 日期越界 / base_price 过低
 *   400 INVALID_STRATEGY_TYPE  策略类型不在白名单
 *   400 INVALID_PARAMETER      策略参数不合法
 *   400 INSUFFICIENT_DATA      样本数不足以完成指标预热
 *
 * @throws ApiCallError 失败时抛出，code 见 ApiErrorCode
 */
export async function runDemoBacktest(
  payload: DemoBacktestRequest,
): Promise<DemoBacktestResponse> {
  try {
    const { data } = await http.post<DemoBacktestResponse>(
      '/demo/backtest',
      payload,
    )
    return data
  } catch (error) {
    throw toApiError(error)
  }
}

/**
 * 重新导出 ApiCallError（错误类）。
 *
 * 目的：让调用方只需从本模块导入，
 * 不必再去 ./backtest 找错误类型。
 *
 * 这只是再导出，没有新建任何错误定义。
 */
export { ApiCallError } from './backtest'

/**
 * 重新导出错误码与 Demo 相关类型。
 *
 * ApiErrorCode 是纯类型，
 * 声明在 @/types/backtest 而非 ./backtest
 * （./backtest 只导入它，并未再导出）。
 *
 * 同样只是再导出，不新建定义。
 */
export type {
  ApiErrorCode,
  DemoBacktestRequest,
  DemoBacktestResponse,
  DemoMarketMode,
  DemoModeOption,
  DemoModesResponse,
  BenchmarkPoint,
} from '@/types/backtest'
