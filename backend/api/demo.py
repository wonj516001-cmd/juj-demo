"""
JUJ Demo 回测 API —— 基于合成行情的独立演示路径。

=============================================================
本模块是什么
=============================================================
一条与真实行情链路**完全独立**的回测入口：

    /api/demo/backtest   合成行情 + 现有引擎 -> 回测结果
    /api/demo/modes      列出可用的合成行情模式

=============================================================
本模块不是什么
=============================================================
1. **不导入 engine.data_loader**
   因此不可能调用 load_stock_data()，
     也不可能触碰 data\\stock 下的任何真实行情文件。

2. **不导入 database / redis / main**
   因此导入本模块不会触发 init_database()，
   不会执行任何 DDL/DML，不连接 PostgreSQL 或 Redis。

3. **不读取任何文件、不访问任何外部 API**
   行情数据全部由 engine.demo_data 在内存中生成。

=============================================================
与真实行情链路的隔离验证
=============================================================
本模块的导入列表可以在源码中直接核对。
若未来有人在这里加入 load_stock_data，
tests/test_demo.py 的 TestDemoPathIsolation 会立即失败。

=============================================================
数据性质
=============================================================
本接口返回的一切结果均基于**模拟数据，非真实行情**。
响应中恒定携带：

    data_source: "synthetic"
    is_demo: true
    disclaimer: "模拟数据，非真实行情，不代表实际交易表现"

这些结果不得被解读为任何真实投资表现或收益预期。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Annotated, Optional

from fastapi import APIRouter, HTTPException
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
)

from api.json_utils import to_json_safe
from api.schemas import (
    EquityPoint,
    ErrorCode,
    StrategyBacktestResponse,
    TradeRecord,
)
from engine.backtest import run_backtest
from engine.demo_data import (
    DATA_SOURCE,
    DEFAULT_BASE_PRICE,
    DEFAULT_SEED,
    DISCLAIMER,
    MARKET_MODES,
    MAX_PERIODS,
    build_buy_and_hold,
    describe_mode,
    generate_demo_ohlcv,
    list_modes,
)
from engine.strategy_factory import create_strategy
from engine.strategy_validator import (
    SUPPORTED_STRATEGY_TYPES,
    normalize_strategy_parameters,
)


logger = logging.getLogger("juj.api.demo")


router = APIRouter(
    prefix="/api/demo",
    tags=["demo"],
)


# ==========================================================
# 1. 请求模型
# ==========================================================

def _reject_bool(value):
    """
    在 Pydantic 类型转换之前拒绝布尔值。

    为什么必须单独前置拦截：
        bool 是 Python int 的子类，
        Pydantic 会先把 True 转成 1，
        导致后续的 min/max 范围检查把 True 当成合法数字。
    """

    if isinstance(value, bool):
        raise ValueError("不接受布尔值")

    return value


#: 带 bool 拦截的整数类型
StrictInt = Annotated[int, BeforeValidator(_reject_bool)]

#: 带 bool 拦截的浮点类型
StrictFloat = Annotated[float, BeforeValidator(_reject_bool)]


# ----------------------------------------------------------
# parameters 结构约束
# ----------------------------------------------------------
#
# 键数上限由 Field(max_length=...) 声明式实现，
# pydantic 在 field_validator 之前执行，且按键的数量计算。
#
# 深度上限由 _check_parameters 显式实现，
# 因为 pydantic 没有可用的嵌套深度约束。
# ----------------------------------------------------------

#: 键数量上限。
#:
#: 当前策略白名单最多 3 个参数（见 engine/strategy_validator.py
#: 的 STRATEGY_SPECS），20 留足未来扩展余量，
#: 同时限制单个请求能塞进内存的参数总量。
MAX_PARAMETERS_ITEMS = 20

#: 嵌套深度上限。
#:
#: 合法参数的深度恒为 1（{"fast_period": 20}），
#: 深度 2 起均为无意义嵌套 —— 白名单只接受标量值。
#: 设为 3 是为容错留一层余量，同时仍能挡住深层攻击载荷。
#:
#: 层级定义：顶层 parameters 本身记为第 0 层，
#: 它的直接键值记为第 1 层，依此类推。
MAX_PARAMETERS_NESTING_DEPTH = 3


def _count_nesting_depth(
    value: object,
    depth: int = 0,
) -> int:
    """
    计算 parameters 的最大嵌套深度。

    层级约定：
        顶层 parameters 自身为第 0 层，
        顶层键对应的值为第 1 层。

    同时遍历 dict 的键与值、以及 list 的所有元素，
    防止用多层数组绕过检查。

    为什么必须显式限制递归：
        JSON 允许任意深度的嵌套，若无上限，
        超深结构会导致递归深度耗尽或占用过多 CPU，
        因此超过 MAX_PARAMETERS_NESTING_DEPTH 后立即返回，
        不再继续向下遍历 —— 不对无限深的数据做递归。

    只遍历 dict 与 list；其余类型都是叶子节点。

    说明：
        本函数与 api/strategy.py 中的同名函数实现一致。
        此处重复实现而非跨模块 import，是因为
        本模块刻意不依赖任何生产链路模块
        （见本文件顶部「本模块不是什么」）。
    """

    if depth > MAX_PARAMETERS_NESTING_DEPTH:
        # 已超限，立即返回，不再递归
        return depth

    if isinstance(value, dict):
        # 键与值都要看：超大键名同样是攻击面
        return max(
            (
                _count_nesting_depth(node, depth + 1)
                for key, item in value.items()
                for node in (key, item)
            ),
            default=depth,
        )

    if isinstance(value, list):
        return max(
            (
                _count_nesting_depth(item, depth + 1)
                for item in value
            ),
            default=depth,
        )

    return depth


# ----------------------------------------------------------
# 数值边界
# ----------------------------------------------------------

#: 起始价格下限。
#:
#: ==============================================================
#: 为什么是 10.0（修订记录）
#: ==============================================================
#: 本常量曾被设为 0.01，当时的理由是：
#:     「生成器 round(4) 的归零阈值是 5e-5，
#:      0.01 即使下跌 99% 仍为 1e-4，高于阈值。」
#:
#: 该推理是错的。它只考虑了单点价格下跌，
#: 没有考虑长期路径的累积效应。
#:
#: 失效机理：
#:     engine/demo_data.py:499 在 validate_ohlcv **之前**
#:     把价格 round(4)，任何 < 5e-5 的值都会变成 0.0。
#:
#:     high_vol 是零均值几何布朗运动
#:     （sigma=0.045，单日波动约 4.5%，
#:      见 MARKET_PROFILES["high_vol"]），
#:     4000 个交易日的对数价格是无界随机游走，
#:     价格衰减到 1e-5 完全可能。
#:
#: 实测（base_price=0.01，high_vol，periods=4000，100 seeds）：
#:     0.01 -> 失败率 34%
#:     0.5  -> 失败率  7%
#:     1.0  -> 失败率  5%
#:     2.0  -> 失败率  3%
#:     5.0  -> 失败率  1%
#:     10.0 -> 失败率  0%
#:
#: 实测（base_price=10.0，periods=4000）：
#:     high_vol 400 seeds  -> 0 失败
#:     trend    200 seeds  -> 0 失败
#:     range    200 seeds  -> 0 失败
#:
#: ==============================================================
#: 边界说明（重要，不要误读为绝对保证）
#: ==============================================================
#: 10.0 是**在已抽样范围内**实测零失败的取值，
#: 不是数学意义上的安全保证。
#:
#: 几何布朗运动的价格路径无界，
#: 理论上存在使价格衰减到 5e-5 以下的 seed。
#: 本常量只是把该概率压到抽样范围内不可观测。
#:
#: 换言之：10.0 显著降低风险，但并未消除风险。
#: 若将来出现 base_price=10.0 的失败报告，
#: 应当调整本常量或修改生成器，而不是当作偶发问题忽略。
MIN_BASE_PRICE = 10.0

#: 初始资金下限，与真实行情链路保持一致。
#: 来源：api/strategy.py 的 MIN_INITIAL_CAPITAL。
MIN_INITIAL_CAPITAL = 1.0

#: 初始资金上限，与真实行情链路保持一致。
#: 来源：api/strategy.py 的 MAX_INITIAL_CAPITAL。
#:
#: Demo 与真实链路对同一概念必须用同一套口径，
#: 否则两条链路会对同样的输入给出不同的接受/拒绝结果。
MAX_INITIAL_CAPITAL = 1e12

#: datetime.date 能表示的最大日期。
#:
#: 越过该点后 pd.bdate_range 与 pd.to_datetime 都会失败，
#: 因此必须在请求校验阶段就拒绝。
MAX_SUPPORTED_DATE = date(9999, 12, 31)


class DemoBacktestRequest(BaseModel):
    """
    Demo 回测请求。

    与真实的 StrategyBacktestRequest **完全独立**：

    - 没有 symbol（合成数据没有股票代码概念）
    - 没有 start_date / end_date（由 periods + start_date 决定区间）
    - 多出 mode / seed / periods / base_price

    这样做的目的是**结构性隔离**：
    Demo 请求在模型层就不可能指向真实行情。
    """

    model_config = ConfigDict(extra="forbid")

    strategy_type: str = Field(
        default="kdj",
        description="策略类型，复用现有策略白名单",
        examples=["kdj"],
    )

    mode: str = Field(
        default="trend",
        description="行情模式：trend / range / high_vol",
        examples=["trend"],
    )

    seed: StrictInt = Field(
        default=DEFAULT_SEED,
        ge=0,
        description=(
            "随机种子。相同种子必然生成完全相同的数据，"
            "因此回测结果可复现。"
        ),
        examples=[20240101],
    )

    periods: StrictInt = Field(
        default=600,
        ge=2,
        le=MAX_PERIODS,
        description="生成的交易日数量",
        examples=[600],
    )

    start_date: str = Field(
        default="2020-01-01",
        description="起始日期，格式 YYYY-MM-DD",
        examples=["2020-01-01"],
    )

    base_price: StrictFloat = Field(
        default=DEFAULT_BASE_PRICE,
        ge=MIN_BASE_PRICE,
        description=(
            f"起始价格，必须不小于 {MIN_BASE_PRICE}。"
            "价格过低时，长周期高波动行情下价格会衰减到"
            "四舍五入精度以下（被舍入为 0），"
            "导致生成的数据违反正数约束。"
        ),
        examples=[10.0],
    )

    initial_capital: StrictFloat = Field(
        default=100000.0,
        ge=MIN_INITIAL_CAPITAL,
        le=MAX_INITIAL_CAPITAL,
        description=(
            "初始资金，范围与真实行情链路一致："
            f"{MIN_INITIAL_CAPITAL} ~ {MAX_INITIAL_CAPITAL}。"
        ),
        examples=[100000.0],
    )

    parameters: dict = Field(
        default_factory=dict,
        description="策略参数，缺省时使用策略默认值",
        max_length=MAX_PARAMETERS_ITEMS,
    )

    @field_validator("strategy_type")
    @classmethod
    def _check_strategy_type(cls, value: str) -> str:
        """这里只做非空检查，白名单校验交给既有 validator。"""

        if not isinstance(value, str) or not value.strip():
            raise ValueError("strategy_type 不能为空")

        return value.strip()

    @field_validator("parameters")
    @classmethod
    def _check_parameters(cls, value: dict) -> dict:
        """
        校验 parameters 的结构约束。

        为什么必须在请求模型层限制，
        而不是只依赖 strategy_validator 的白名单校验：

            白名单校验发生在回测流程中段
            （demo_backtest 内调用 normalize_strategy_parameters），
            而请求解析阶段已经把这个结构完整收进了内存。
            若不提前拦截，一个深度嵌套或键数超限的
            parameters 会先被完整构造出来，
            才在后续被拒绝 —— 这正是「拒绝发生在回测执行之前」
            所要避免的情形。

        键数上限已由 Field(max_length=...) 声明式实现，
        此处只补深度上限与类型兜底。
        """

        if value is None:
            return {}

        if not isinstance(value, dict):
            raise ValueError("parameters 必须是 JSON 对象")

        if _count_nesting_depth(value) > MAX_PARAMETERS_NESTING_DEPTH:
            raise ValueError(
                f"parameters 嵌套层级过深，"
                f"最多允许 {MAX_PARAMETERS_NESTING_DEPTH} 层"
            )

        return value

    @field_validator("start_date")
    @classmethod
    def _check_start_date(cls, value: str) -> str:
        """
        校验日期格式与可生成范围。

        只做格式与合法性校验，不查询任何交易日历数据源。

        为什么还要校验区间上界：
            生成器用 pd.bdate_range 生成工作日序列，
            它【不会】校验结果日期是否超出 datetime 支持范围。
            因此 start_date='9999-12-01' 能通过生成阶段，
            却会在后续裁剪时因日期溢出而失败，
            且该失败发生在异常处理之外，会变成 500。

            这里提前拒绝，让错误明确归因于 start_date。
        """

        if not isinstance(value, str) or not value.strip():
            raise ValueError("start_date 不能为空")

        from datetime import date, timedelta

        normalized = value.strip()

        try:
            parsed = date.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError(
                "start_date 格式不正确，应为 YYYY-MM-DD"
            ) from exc

        # 预留足够天数容纳 periods 个工作日。
        #
        # 系数 3 的依据：一年约 250 个工作日，
        # 最坏情况（全是节假日）下日历天数约为工作日的 3 倍，
        # 因此 max_periods * 3 天足以覆盖任何合法组合。
        #
        # datetime.date 的上界是 9999-12-31，
        # 越过该点后任何日期运算与 pandas 解析都会失败。
        #
        # 关键：必须用【减法】求阈值，不能用
        # `parsed + horizon > MAX_DATE` ——
        # 当 parsed 本身已接近上界时，加法会先抛
        # OverflowError，那个异常同样会穿透异常处理。
        horizon = timedelta(
            days=MAX_PERIODS * 3 + 30
        )

        latest_safe_start = MAX_SUPPORTED_DATE - horizon

        if parsed > latest_safe_start:
            raise ValueError(
                "start_date 过于靠后，"
                f"无法生成 {MAX_PERIODS} 个交易日的序列。"
                f"起始日期不得晚于 "
                f"{latest_safe_start.isoformat()}"
            )

        return normalized

    @field_validator("base_price", "initial_capital")
    @classmethod
    def _check_positive(cls, value: float) -> float:
        """显式拒绝非有限数值。"""

        import math

        if not math.isfinite(value):
            raise ValueError("必须是有限数值")

        if value <= 0:
            raise ValueError("必须大于 0")

        return value


# ==========================================================
# 2. 响应模型
# ==========================================================

class DemoBacktestResponse(StrategyBacktestResponse):
    """
    Demo 回测响应。

    继承 StrategyBacktestResponse 的全部字段，
    因此前端现有组件（EquityChart / TradeTable / MetricCards）
    在阶段 D 可以直接复用，无需为Demo 单独写一套类型。

    新增字段全部与「模拟数据标记」和「合成数据元信息」相关。
    """

    data_source: str = Field(
        default=DATA_SOURCE,
        description="数据来源，恒为 synthetic",
        examples=["synthetic"],
    )

    is_demo: bool = Field(
        default=True,
        description="是否为演示数据，恒为 true",
        examples=[True],
    )

    disclaimer: str = Field(
        default=DISCLAIMER,
        description="免责声明，必须在界面显著展示",
        examples=[DISCLAIMER],
    )

    mode: str = Field(
        description="行情模式",
        examples=["trend"],
    )

    mode_label: str = Field(
        description="行情模式中文标签",
        examples=["趋势"],
    )

    seed: int = Field(
        description="本次使用的随机种子",
        examples=[20240101],
    )

    periods: int = Field(
        description="本次生成的交易日数量",
        examples=[600],
    )

    benchmark: list[dict] = Field(
        default_factory=list,
        description=(
            "买入持有基准序列，每行含 date / equity。"
            "已按权益曲线的日期范围裁剪，两条曲线逐日对齐。"
            "零成本理想曲线，不含手续费与滑点。"
        ),
    )

    equity_start_date: str = Field(
        description=(
            "权益曲线实际首日。"
            "与 start_date 的区别：start_date 是请求的模拟区间起点，"
            "而策略指标预热期内无法产生交易，"
            "引擎会丢弃预热期数据，因此曲线起点晚于 start_date。"
            "该字段从实际权益曲线推导，不做任何假设。"
        ),
        examples=["2020-01-13"],
    )

    equity_end_date: str = Field(
        description=(
            "权益曲线实际末日。"
            "预热只丢弃开头数据，不丢弃结尾，"
            "因此通常与 end_date 相同。"
            "该字段从实际权益曲线推导，不做任何假设。"
        ),
        examples=["2021-07-13"],
    )

    benchmark_return: float = Field(
        description=(
            "买入持有基准总收益率。"
            "按【完整模拟区间】的首末收盘价计算，"
            "不受权益曲线预热期裁剪影响。"
        ),
        examples=[0.215],
    )

    benchmark_curve_return: float = Field(
        description=(
            "基准在【权益曲线日期区间】内的收益率。"
            "与 total_return 使用完全相同的起止日期"
            "（equity_start_date ~ equity_end_date）与收益定义"
            "（期初值-> 期末值）。"
            "excess_return 即为 total_return - 本字段。"
            "与 benchmark_return 的差额来自策略预热期："
            "后者按完整模拟区间计算，不能用于超额比较。"
        ),
        examples=[0.1497],
    )

    excess_return: Optional[float] = Field(
        default=None,
        description=(
            "同区间超额收益 = 策略总收益率 - benchmark_curve_return。"
            "两侧区间完全一致（均为权益曲线区间 "
            "equity_start_date ~ equity_end_date），"
            "因此该值是可解释的。"
            "策略跑输基准时为负数。"
            "不要用 benchmark_return 计算超额收益 —— "
            "那是完整模拟区间口径，与策略区间不一致。"
        ),
        examples=[0.05],
    )


class DemoModesResponse(BaseModel):
    """可用行情模式列表。"""

    modes: list[dict]

    data_source: str = DATA_SOURCE

    is_demo: bool = True

    disclaimer: str = DISCLAIMER


# ==========================================================
# 3. 内部工具
# ==========================================================

def _raise_demo_error(
    message: str,
    code: str,
    field: Optional[str] = None,
) -> None:
    """
    抛出统一的 Demo 校验错误（400）。

    与 api.strategy 的处理方式一致：
        详细信息只写服务端日志，
        对外只返回稳定错误码与简短说明。
    """

    logger.info(
        "demo validation failed: code=%s field=%s message=%s",
        code,
        field,
        message,
    )

    error: dict = {
        "code": code,
        "message": message,
    }

    if field is not None:
        error["field"] = field

    raise HTTPException(
        status_code=400,
        detail=error,
    )


def _build_demo_trade_record(
    trade: dict,
) -> dict:
    """
    组装单笔交易记录。

    与 api.strategy._build_trade_record 口径完全一致：

        return_rate = pnl / (entry_price * shares)

    分母无效时返回 None（JSON null），
    不返回 0，也不抛异常。

    这里刻意不 import api.strategy 的私有函数，
    以免 Demo 模块与真实行情模块产生耦合 ——
    即使真实链路被重构，Demo 也不受影响。
    """

    entry_price = float(trade["entry_price"])
    shares = int(trade["shares"])

    invested = entry_price * shares

    if invested > 0:
        return_rate = float(trade["pnl"]) / invested
    else:
        return_rate = None

    return {
        "side": trade["side"],

        "entry_date": trade["entry_date"],
        "entry_price": entry_price,
        "shares": shares,

        "exit_date": trade["exit_date"],
        "exit_price": float(trade["exit_price"]),

        "commission": float(trade["commission"]),
        "exit_commission": float(trade["exit_commission"]),
        "stamp_duty": float(trade["stamp_duty"]),

        "pnl": float(trade["pnl"]),
        "return_rate": return_rate,

        "forced_exit": bool(trade.get("forced_exit", False)),
    }


# ==========================================================
# 4. API 路由
# ==========================================================

@router.get(
    "/modes",
    summary="查询可用的合成行情模式",
)
def demo_modes() -> DemoModesResponse:
    """
    返回全部可用的合成行情模式。

    供前端渲染模式选择器，避免硬编码。
    """

    return DemoModesResponse(modes=list_modes())


@router.post(
    "/backtest",
    summary="使用合成行情执行回测",
    response_model=DemoBacktestResponse,
    responses={
        400: {"description": "请求参数不合法"},
        500: {"description": "回测执行失败"},
    },
)
def demo_backtest(
    request: DemoBacktestRequest,
) -> DemoBacktestResponse:
    """
    用合成行情执行一次回测。

    执行链路：

        demo_data.generate_demo_ohlcv   内存生成，不落盘
              |
              v
        strategy_factory.create_strategy  复用现有策略
              |
              v
        backtest.run_backtest           复用现有引擎
              |
              v
        to_json_safe                    复用既有 JSON 清洗

    注意：
        全程不触碰 load_stock_data，
        不读取 data/stock 下的任何文件。
    """

    # ----------------------------------------------------------
    # 1. 模式校验
    # ----------------------------------------------------------

    try:
        mode = describe_mode(request.mode)
    except ValueError as exc:
        _raise_demo_error(
            message=str(exc),
            code=ErrorCode.VALIDATION_ERROR,
            field="mode",
        )
        raise  # pragma: no cover

    # ----------------------------------------------------------
    # 2. 策略参数校验
    #
    # 复用既有 normalize_strategy_parameters，
    # 因此 Demo 与真实行情使用完全相同的白名单与范围约束。
    # ----------------------------------------------------------

    try:
        parameters = normalize_strategy_parameters(
            strategy_type=request.strategy_type,
            parameters=request.parameters,
        )

    except ValueError as exc:
        raw_message = str(exc)

        if "不支持的策略类型" in raw_message:
            code = ErrorCode.INVALID_STRATEGY_TYPE
            field = "strategy_type"
        else:
            code = ErrorCode.INVALID_PARAMETER
            field = "parameters"

        _raise_demo_error(
            message=raw_message,
            code=code,
            field=field,
        )
        raise  # pragma: no cover

    # ----------------------------------------------------------
    # 3. 生成合成行情
    #
    # 这里是 Demo 与真实链路的分界点：
    # 数据在内存中生成，绝不调用 load_stock_data。
    # ----------------------------------------------------------

    try:
        frame = generate_demo_ohlcv(
            periods=request.periods,
            mode=request.mode,
            seed=request.seed,
            start_date=request.start_date,
            base_price=request.base_price,
        )

    except ValueError as exc:
        # 归因到正确的字段。
        #
        # 该分支的错误来自 generate_demo_ohlcv 内部，
        # 可能是行情模式非法，也可能是数值参数不合法。
        # 统一标成 field="mode" 会让用户去改一个无关字段，
        # 因此按消息内容区分归因。
        #
        # 注意：请求模型已在更早阶段拦下 base_price 越界
        # （ge=MIN_BASE_PRICE），因此这里的 base_price 错误
        # 只可能是数值下溢等极端情况，属于兜底。
        raw_message = str(exc)

        if "行情模式" in raw_message:
            field = "mode"
        elif "base_price" in raw_message:
            field = "base_price"
        elif "periods" in raw_message:
            field = "periods"
        elif "seed" in raw_message:
            field = "seed"
        elif "start_date" in raw_message:
            field = "start_date"
        elif "合成数据" in raw_message:
            field = "base_price"
        else:
            field = None

        _raise_demo_error(
            message=raw_message,
            code=ErrorCode.VALIDATION_ERROR,
            field=field,
        )
        raise  # pragma: no cover

    except Exception as exc:
        logger.exception(
            "demo data generation failed: mode=%s",
            request.mode,
        )

        raise HTTPException(
            status_code=500,
            detail={
                "code": ErrorCode.BACKTEST_FAILED,
                "message": "合成行情生成失败，请稍后重试",
            },
        ) from exc

    # ----------------------------------------------------------
    # 4. 创建策略
    # ----------------------------------------------------------

    try:
        strategy = create_strategy(
            strategy_type=request.strategy_type,
            parameters=parameters,
        )

    except ValueError as exc:
        logger.warning(
            "demo strategy creation failed: type=%s reason=%s",
            request.strategy_type,
            exc,
        )

        _raise_demo_error(
            message="策略参数不合法",
            code=ErrorCode.INVALID_PARAMETER,
            field="parameters",
        )
        raise  # pragma: no cover

    # ----------------------------------------------------------
    # 5. 执行回测
    # ----------------------------------------------------------

    try:
        result = run_backtest(
            df=frame,
            strategy=strategy,
            initial_capital=request.initial_capital,
        )

    except ValueError as exc:
        #
        # 数据充分性判定与真实链路一致：
        # 策略指标计算后剩余行数 < 2 时，
        # 说明是周期相对区间太短，属于用户输入问题。
        #
        try:
            usable_rows = len(
                strategy.generate_signals(frame)
            )
        except Exception:
            usable_rows = None

        if usable_rows is not None and usable_rows < 2:
            _raise_demo_error(
                message=(
                    "所选条件下有效数据不足，"
                    "请增加 periods 或缩短指标周期"
                ),
                code=ErrorCode.INSUFFICIENT_DATA,
                field="periods",
            )

        logger.exception(
            "demo backtest failed: mode=%s strategy=%s",
            request.mode,
            request.strategy_type,
        )

        raise HTTPException(
            status_code=500,
            detail={
                "code": ErrorCode.BACKTEST_FAILED,
                "message": "回测执行失败，请稍后重试",
            },
        )

    except HTTPException:
        raise

    except Exception as exc:
        logger.exception(
            "demo backtest failed: mode=%s strategy=%s reason=%s",
            request.mode,
            request.strategy_type,
            exc,
        )

        raise HTTPException(
            status_code=500,
            detail={
                "code": ErrorCode.BACKTEST_FAILED,
                "message": "回测执行失败，请稍后重试",
            },
        ) from exc

    # ----------------------------------------------------------
    # 6. 构造买入持有基准
    # ----------------------------------------------------------
    #
    # 两套口径，必须严格区分：
    #
    #   benchmark_return
    #       完整模拟区间的首末收盘价涨幅。
    #       它衡量「用户请求的这段时间里单纯持有能赚多少」，
    #       因此必须用完整区间，作为独立指标单独展示。
    #
    #   benchmark_curve_return
    #       裁剪到与equity_curve 相同日期范围后的涨幅。
    #       与 total_return 区间完全一致，
    #       因此**超额收益必须用它**，见下方注释。
    #
    #   benchmark（曲线）
    #       逐日序列，与 benchmark_curve_return 同区间，
    #       用于前端画图。
    #
    # 两者不能混用：
    #   实测中完整区间收益 9.87%，
    #   而预热期之后那一段是14.97%，相差 5.1 个百分点。
    #   若用完整区间基准去减权益区间策略收益，
    #   excess_return 会被预热期的涨跌系统性污染，
    #   方向取决于预热期是涨是跌，解读完全不可靠。
    # ----------------------------------------------------------

    equity_curve = result["equity_curve"]

    # 权益曲线的实际日期范围（引擎已丢弃策略预热期）
    equity_start = str(equity_curve[0]["date"])[:10]
    equity_end = str(equity_curve[-1]["date"])[:10]

    # 只捕获 ValueError，不捕获 Exception。
    #
    # 原因：
    #     build_buy_and_hold 内部可能因日期越界抛
    #     DateParseError（pandas 的 ValueError 子类），
    #     那是用户输入问题，应返回 400。
    #
    #     但若用 except Exception，会把代码缺陷
    #     （属性错误、类型错误等）也伪装成「用户输入错误」，
    #     掩盖真实问题。因此只处理明确的 ValueError。
    #
    #     其他异常类型继续向上抛出，
    #     由 main.py 的兜底处理器转成 500。
    try:
        benchmark_frame = build_buy_and_hold(
            frame,
            start_date=equity_start,
            end_date=equity_end,
        )

    except ValueError as exc:
        # 归因到 start_date：
        # 该分支只可能是日期裁剪失败。
        logger.info(
            "demo benchmark alignment failed: "
            "equity_start=%s equity_end=%s reason=%s",
            equity_start,
            equity_end,
            exc,
        )

        _raise_demo_error(
            message=(
                "无法按权益曲线区间对齐基准数据，"
                "请调整 start_date 或 periods"
            ),
            code=ErrorCode.VALIDATION_ERROR,
            field="start_date",
        )
        raise  # pragma: no cover

    # 完整区间基准收益（保留原始定义，不受裁剪影响）
    first_close = float(frame["close"].iloc[0])
    last_close = float(frame["close"].iloc[-1])

    benchmark_return = (
        last_close / first_close - 1
        if first_close > 0
        else 0.0
    )

    # 裁剪区间基准收益 —— 与 total_return 同区间，用于超额计算
    #
    # 分母是【裁剪后区间】的首日收盘价，
    # 分子是完整 frame 的末日收盘价。
    #
    # 为什么分子可以直接取frame 的末日：
    #   engine/backtest.py 只在开头 dropna 丢弃预热期，
    #   不丢弃结尾，因此 equity_end == frame 最后一天，
    #   分子与裁剪后区间的末日收盘价是同一个值。
    #   这一点由 tests/test_demo.py 的基准曲线对齐用例覆盖。
    #
    # 区间与 total_return 完全一致：
    #   total_return = equity_curve[-1] / equity_curve[0] - 1
    #                 = equity_curve[-1] / initial_capital - 1
    #   其中 equity_curve[0] 是预热后首个交易日，
    #   回测循环 i=0 时不交易，故其权益恰等于 initial_capital。
    trimmed_first_close = float(
        benchmark_frame["equity"].iloc[0]
    )

    benchmark_curve_return = (
        last_close / trimmed_first_close - 1
        if trimmed_first_close > 0
        else 0.0
    )

    strategy_return = float(result["total_return"])

    # 超额收益：两侧区间严格一致，均为 equity_start_date ~ equity_end_date。
    excess_return = strategy_return - benchmark_curve_return

    benchmark_rows = [
        {
            "date": row["trade_date"],
            "equity": float(row["equity"]),
        }
        for _, row in benchmark_frame.iterrows()
    ]

    # ----------------------------------------------------------
    # 7. 组装响应
    #
    # 复用既有 StrategyBacktestResponse 的字段结构，
    # 新增合成数据标记与基准信息。
    # ----------------------------------------------------------

    payload = {
        # --- 合成数据标记（阶段 B 的核心要求）---
        "data_source": DATA_SOURCE,
        "is_demo": True,
        "disclaimer": DISCLAIMER,

        # --- 本次生成参数 ---
        "mode": mode["mode"],
        "mode_label": mode["label"],
        "seed": request.seed,
        "periods": request.periods,

        # --- 策略与区间 ---
        "strategy_type": request.strategy_type,
        "strategy": type(strategy).__name__,
        "symbol": "DEMO.SYNTHETIC",
        # --- 区间标注 ---
        #
        # start_date / end_date
        #     请求的模拟区间（完整生成范围）
        #
        # equity_start_date / equity_end_date
        #     权益曲线的实际范围（已扣除策略预热期）
        #
        # 两者由 equity_start / equity_end 赋值，
        # 那两个局部变量直接来自 equity_curve 的首末点，
        # 因此是从实际数据推导，不硬编码也不复制原始行情。
        "start_date": str(frame["trade_date"].iloc[0])[:10],
        "end_date": str(frame["trade_date"].iloc[-1])[:10],
        "equity_start_date": equity_start,
        "equity_end_date": equity_end,

        # --- 资金 ---
        "initial_capital": result["initial_capital"],
        "final_capital": result["final_capital"],

        # --- 既有指标（阶段 C 之前只有这14 项）---
        "total_return": result["total_return"],
        "annual_return": result["annual_return"],
        "max_drawdown": result["max_drawdown"],
        "sharpe_ratio": result["sharpe_ratio"],
        "total_trades": result["total_trades"],
        "winning_trades": result["winning_trades"],
        "losing_trades": result["losing_trades"],
        "win_rate": result["win_rate"],
        "profit_factor": result["profit_factor"],

        # --- 基准对比 ---
        "benchmark": benchmark_rows,
        "benchmark_return": benchmark_return,
        "benchmark_curve_return": benchmark_curve_return,
        "excess_return": excess_return,

        # --- 明细 ---
        "equity_curve": [
            {
                "date": point["date"],
                "equity": float(point["equity"]),
                "cash": float(point["cash"]),
                "shares": int(point["shares"]),
                "position_value": float(
                    point["position_value"]
                ),
            }
            for point in equity_curve
        ],

        "trades": [
            _build_demo_trade_record(trade)
            for trade in result["trades"]
        ],
    }

    # to_json_safe 把 inf / NaN 统一转为 None，
    # 与真实链路共用同一个清洗函数。
    return DemoBacktestResponse(
        **to_json_safe(payload)
    )
