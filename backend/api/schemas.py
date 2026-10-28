"""
JUJ API 统一响应与错误规范。

=============================================================
成功响应
=============================================================

所有回测接口返回严格 JSON，字段规则如下：

    strategy_type        string           策略类型
    strategy             string           策略类名
    symbol               string           股票代码
    start_date           string           回测开始日期
    end_date             string           回测结束日期
    initial_capital      number           初始资金
    final_capital        number           最终资金
    total_return         number           总收益率（小数，0.3484 = 34.84%）
    annual_return        number           年化收益率（小数）
    max_drawdown         number           最大回撤（负数，-0.2943 = -29.43%）
    sharpe_ratio         number           夏普比率
    total_trades         integer          完成的交易次数
    winning_trades       integer          盈利交易次数
    losing_trades        integer          亏损交易次数
    win_rate             number           胜率（小数）
    profit_factor        number | null    盈亏比

    profit_factor 规则：
        有亏损交易时，为有限数值。
        无亏损交易时返回 null（数学上无定义）。
        绝不会返回 Infinity —— Infinity 不是合法 JSON。

=============================================================
错误响应
=============================================================

所有错误统一为：

    {
        "error": {
            "code": "INVALID_STRATEGY_TYPE",
            "message": "面向用户的简短说明",
            "field": "strategy_type"        // 可选
        }
    }

设计约束：
    - message 绝不包含本地绝对路径、堆栈、SQL 或内部异常细节
    - 详细错误只写入服务端日志
    - 客户端应基于 code 做分支，而不是解析 message
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


# ==========================================================
# 错误码
# ==========================================================

class ErrorCode:
    """
    对外暴露的稳定错误码。

    客户端请依赖这些字符串做分支处理。
    """

    VALIDATION_ERROR = "VALIDATION_ERROR"
    INVALID_STRATEGY_TYPE = "INVALID_STRATEGY_TYPE"
    INVALID_PARAMETER = "INVALID_PARAMETER"
    INVALID_SYMBOL = "INVALID_SYMBOL"
    INVALID_DATE_RANGE = "INVALID_DATE_RANGE"
    INVALID_INITIAL_CAPITAL = "INVALID_INITIAL_CAPITAL"
    DATA_NOT_FOUND = "DATA_NOT_FOUND"
    DATA_LOAD_FAILED = "DATA_LOAD_FAILED"
    DATA_EMPTY = "DATA_EMPTY"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    BACKTEST_FAILED = "BACKTEST_FAILED"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorDetail(BaseModel):
    """
    错误详情。
    """

    code: str = Field(
        description="稳定的机器可读错误码",
        examples=["INVALID_STRATEGY_TYPE"],
    )

    message: str = Field(
        description="面向用户的简短说明，不含内部细节",
        examples=["不支持的策略类型：foo"],
    )

    field: Optional[str] = Field(
        default=None,
        description="出错字段名（如果可定位）",
        examples=["strategy_type"],
    )


class ErrorResponse(BaseModel):
    """
    统一错误响应体。
    """

    error: ErrorDetail


# ==========================================================
# 成功响应
# ==========================================================

class EquityPoint(BaseModel):
    """
    权益曲线上的单点。

    对应回测引擎每日收盘后的账户状态。

    恒等式（所有点均成立）：
        equity == cash + position_value

    字段语义：
        date           当日交易日
        equity         当日收盘总权益（现金 + 持仓市值）
        cash           当日收盘现金余额
                      （已扣买入佣金、已加卖出净额并扣除印花税）
        shares         当日收盘持股数
                      （T+1 语义：反映当日开盘执行后的持仓）
        position_value 当日收盘持仓市值 = shares × 当日收盘价

    说明：
        最后一天若发生强制平仓，
        该点会同步为平仓后状态（shares=0, position_value=0.0）。
    """

    date: str = Field(
        description="交易日，格式 YYYY-MM-DD",
        examples=["2020-01-14"],
    )

    equity: float = Field(
        description="当日收盘总权益",
        examples=[100000.0],
    )

    cash: float = Field(
        description="当日收盘现金余额",
        examples=[100000.0],
    )

    shares: int = Field(
        description="当日收盘持股数",
        examples=[6700],
    )

    position_value: float = Field(
        description="当日收盘持仓市值",
        examples=[0.0],
    )


class TradeRecord(BaseModel):
    """
    单笔完整交易（开仓到平仓配对）。

    一笔记录对应一次完整的买入 + 卖出，
    手续费与印花税均已计入 pnl。
    """

    side: str = Field(
        description="交易方向，固定为 BUY（记录以开仓为起点）",
        examples=["BUY"],
    )

    entry_date: str = Field(
        description="开仓日期，格式 YYYY-MM-DD",
        examples=["2020-02-06"],
    )

    entry_price: float = Field(
        description="开仓成交价（含滑点）",
        examples=[14.811481],
    )

    shares: int = Field(
        description="成交股数（100 的整数倍）",
        examples=[6700],
    )

    exit_date: str = Field(
        description="平仓日期，格式 YYYY-MM-DD",
        examples=["2020-02-25"],
    )

    exit_price: float = Field(
        description="平仓成交价（含滑点）",
        examples=[14.9985],
    )

    commission: float = Field(
        description="买入佣金",
        examples=[29.771077],
    )

    exit_commission: float = Field(
        description="卖出佣金",
        examples=[30.146985],
    )

    stamp_duty: float = Field(
        description="卖出印花税",
        examples=[50.244975],
    )

    pnl: float = Field(
        description=(
            "该笔净盈亏，已扣除双边佣金与印花税"
        ),
        examples=[1142.864263],
    )

    return_rate: Optional[float] = Field(
        default=None,
        description=(
            "单笔收益率 = pnl / (entry_price × shares)，"
            "分母为买入名义金额。"
            "分母无效（entry_price<=0 或 shares=0）时返回 null。"
        ),
        examples=[0.0115],
    )

    forced_exit: bool = Field(
        description=(
            "是否为回测最后一天强制平仓"
        ),
        examples=[False],
    )


class StrategyBacktestResponse(BaseModel):
    """
    策略回测结果。

    所有浮点字段均为有限数值或 null，
    保证响应是严格合法的 JSON。
    """

    strategy_type: str = Field(
        description="策略类型",
        examples=["kdj"],
    )

    strategy: str = Field(
        description="实际使用的策略类名",
        examples=["KDJStrategy"],
    )

    symbol: str = Field(
        description="股票代码",
        examples=["000001.SZ"],
    )

    start_date: str = Field(
        description="回测开始日期",
        examples=["2020-01-01"],
    )

    end_date: str = Field(
        description="回测结束日期",
        examples=["2026-09-24"],
    )

    initial_capital: float = Field(
        description="初始资金",
        examples=[100000.0],
    )

    final_capital: float = Field(
        description="期末资金",
        examples=[134838.48],
    )

    total_return: float = Field(
        description="总收益率，小数形式，0.3484 表示 34.84%",
        examples=[0.3484],
    )

    annual_return: float = Field(
        description="年化收益率，小数形式",
        examples=[0.0457],
    )

    max_drawdown: float = Field(
        description="最大回撤，负数，-0.2943 表示 -29.43%",
        examples=[-0.2943],
    )

    sharpe_ratio: float = Field(
        description="夏普比率，无风险利率按 0 处理",
        examples=[0.3288],
    )

    total_trades: int = Field(
        description="完成的交易次数",
        examples=[130],
    )

    winning_trades: int = Field(
        description="盈利交易次数",
        examples=[50],
    )

    losing_trades: int = Field(
        description="亏损交易次数",
        examples=[80],
    )

    win_rate: float = Field(
        description="胜率，小数形式",
        examples=[0.3846],
    )

    profit_factor: Optional[float] = Field(
        default=None,
        description=(
            "盈亏比 = 总盈利 / 总亏损。"
            "无亏损交易时返回 null（数学上无定义），"
            "不会返回 Infinity。"
        ),
        examples=[1.158],
    )

    # ----------------------------------------------------------
    # 以下为前端绘图所需的明细数据
    #
    # 默认返回完整数据，由前端自行抽样绘图，
    # 服务端不做抽样，避免破坏后续交互。
    # ----------------------------------------------------------

    equity_curve: list[EquityPoint] = Field(
        default_factory=list,
        description=(
            "完整权益曲线，每个交易日一个点，"
            "按日期升序。"
            "有有效数据时长度等于交易日数，不会为空。"
        ),
    )

    trades: list[TradeRecord] = Field(
        default_factory=list,
        description=(
            "完整交易明细，一笔对应一次完整买卖。"
            "无交易时为空数组。"
        ),
    )
