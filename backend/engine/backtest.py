from collections.abc import Callable

import pandas as pd

from engine.metrics import calculate_metrics
from engine.strategy import Strategy


class BacktestCancelled(Exception):
    """
    回测被协作式取消。

    抛出时机：
        主回测循环每次迭代开始处，
        cancel_check() 返回 True 时。

    重要说明：
        这是一种【协作式取消】，不是硬超时，
        也不是线程强制终止。

        引擎只在循环内检查取消条件，
        因此以下阶段无法被中断：
            - load_stock_data（数据加载）
            - strategy.generate_signals（指标计算）
            - calculate_metrics（指标汇总）

        调用方必须自行处理本异常，
        引擎不会也不应该吞掉它。
    """

    def __init__(self, reason: str = "cancelled") -> None:
        super().__init__(reason)
        self.reason = reason


def run_backtest(
    df: pd.DataFrame,
    strategy: Strategy,
    initial_capital: float = 100000.0,

    commission_rate: float = 0.0003,
    stamp_duty_rate: float = 0.0005,
    slippage_rate: float = 0.0001,

    lot_size: int = 100,

    cancel_check: Callable[[], bool] | None = None,
) -> dict:
    """
    JUJ 通用回测引擎。

    Strategy：
        只负责产生交易信号。

    Backtest Engine：
        负责资金、仓位、手续费、滑点、T+1执行、
        权益曲线以及交易记录。

    当前版本严格保持旧版回测行为。

    cancel_check：
        协作式取消回调（可选）。

        - 为 None 时，完全不检查取消条件，
          执行路径与改造前逐字节一致。
        - 非 None 时，在主循环每次迭代开始处调用一次；
          返回 True 立即抛出 BacktestCancelled。

        限制：
            仅覆盖主循环（约占总体耗时 90%）。
            数据加载、指标计算、指标汇总三段无法中断，
            取消生效前最多还会执行完这三段。
    """

    if df.empty:
        raise ValueError("backtest data is empty")

    required_columns = [
        "trade_date",
        "open",
        "close",
    ]

    missing_columns = [
        column
        for column in required_columns
        if column not in df.columns
    ]

    if missing_columns:
        raise ValueError(
            f"backtest data missing fields: {missing_columns}"
        )

    # ==========================================================
    # 1. Strategy 生成信号
    # ==========================================================

    df = strategy.generate_signals(df)

    if "signal" not in df.columns:
        raise ValueError(
            "strategy result must contain 'signal' column"
        )

    df = df.copy()

    df["trade_date"] = pd.to_datetime(
        df["trade_date"]
    )

    df = (
        df.sort_values("trade_date")
        .reset_index(drop=True)
    )

    # ==========================================================
    # 2. 删除 MA 尚未形成的无效数据
    #
    # 保持旧版行为：
    # 旧 backtest.py 会删除 MA20 / MA60 为 NaN 的数据。
    #
    # 当前策略生成的结果中存在 ma_fast / ma_slow，
    # 所以这里继续保持这个行为。
    # ==========================================================

    if "ma_fast" in df.columns and "ma_slow" in df.columns:

        df = df.dropna(
            subset=["ma_fast", "ma_slow"]
        ).reset_index(drop=True)

    if len(df) < 2:
        raise ValueError(
            "valid data insufficient for backtest"
        )

    # ==========================================================
    # 3. 初始化账户
    # ==========================================================

    cash = float(initial_capital)

    shares = 0

    entry_price = None
    entry_date = None

    trades = []

    equity_curve = []

    # ==========================================================
    # 4. 逐日回测
    # ==========================================================

    for i in range(len(df)):

        # ------------------------------------------------------
        # 协作式取消检查
        #
        # 必须位于每次迭代的最开始处，
        # 即在处理当前交易日（df.iloc[i]）之前。
        #
        # cancel_check 为 None 时完全跳过，
        # 不引入任何额外开销或行为差异。
        # ------------------------------------------------------

        if cancel_check is not None and cancel_check():
            raise BacktestCancelled(
                reason=f"cancelled at iteration {i}"
            )

        today = df.iloc[i]

        today_date = today["trade_date"]

        today_open = float(today["open"])

        today_close = float(today["close"])

        # ------------------------------------------------------
        # T+1 执行
        #
        # 使用前一个交易日的 signal
        # 决定今天开盘是否交易。
        # ------------------------------------------------------

        if i > 0:

            previous = df.iloc[i - 1]

            previous_signal = int(
                previous["signal"]
            )

            # ==================================================
            # BUY
            # ==================================================

            if shares == 0 and previous_signal == 1:

                execution_price = (
                    today_open
                    * (1 + slippage_rate)
                )

                total_cost_per_share = (
                    execution_price
                    * (1 + commission_rate)
                )

                shares_to_buy = (
                    int(
                        cash
                        / (
                            total_cost_per_share
                            * lot_size
                        )
                    )
                    * lot_size
                )

                if shares_to_buy > 0:

                    trade_value = (
                        shares_to_buy
                        * execution_price
                    )

                    commission = (
                        trade_value
                        * commission_rate
                    )

                    total_cost = (
                        trade_value
                        + commission
                    )

                    cash -= total_cost

                    shares = shares_to_buy

                    entry_price = execution_price

                    entry_date = today_date

                    trades.append({
                        "side": "BUY",
                        "entry_date": today_date,
                        "entry_price": execution_price,
                        "shares": shares_to_buy,
                        "commission": commission,
                    })

            # ==================================================
            # SELL
            # ==================================================

            elif shares > 0 and previous_signal == -1:

                execution_price = (
                    today_open
                    * (1 - slippage_rate)
                )

                trade_value = (
                    shares
                    * execution_price
                )

                commission = (
                    trade_value
                    * commission_rate
                )

                stamp_duty = (
                    trade_value
                    * stamp_duty_rate
                )

                net_proceeds = (
                    trade_value
                    - commission
                    - stamp_duty
                )

                cash += net_proceeds

                gross_pnl = (
                    execution_price
                    - entry_price
                ) * shares

                total_costs = (
                    trades[-1]["commission"]
                    + commission
                    + stamp_duty
                )

                net_pnl = (
                    gross_pnl
                    - total_costs
                )

                trades[-1].update({
                    "exit_date": today_date,
                    "exit_price": execution_price,
                    "exit_commission": commission,
                    "stamp_duty": stamp_duty,
                    "pnl": net_pnl,
                    "forced_exit": False,
                })

                shares = 0

                entry_price = None
                entry_date = None

        # ======================================================
        # 每日收盘权益
        # ======================================================

        equity = (
            cash
            + shares * today_close
        )

        equity_curve.append({
            "date": today_date,
            "equity": equity,

            # 以下为前端绘图所需的持仓明细。
            # 全部为当日收盘后的状态，
            # 满足恒等式 equity == cash + position_value
            "cash": cash,
            "shares": shares,
            "position_value": shares * today_close,
        })

    # ==========================================================
    # 5. 最后一天强制平仓
    # ==========================================================

    if shares > 0:

        last_row = df.iloc[-1]

        final_date = last_row["trade_date"]

        final_price = float(
            last_row["close"]
        )

        trade_value = (
            shares
            * final_price
        )

        commission = (
            trade_value
            * commission_rate
        )

        stamp_duty = (
            trade_value
            * stamp_duty_rate
        )

        net_proceeds = (
            trade_value
            - commission
            - stamp_duty
        )

        cash += net_proceeds

        gross_pnl = (
            final_price
            - entry_price
        ) * shares

        total_costs = (
            trades[-1]["commission"]
            + commission
            + stamp_duty
        )

        net_pnl = (
            gross_pnl
            - total_costs
        )

        trades[-1].update({
            "exit_date": final_date,
            "exit_price": final_price,
            "exit_commission": commission,
            "stamp_duty": stamp_duty,
            "pnl": net_pnl,
            "forced_exit": True,
        })

        shares = 0

        # 最后一天强制平仓：
        #
        # 资产全部转为现金，因此最后一个权益点必须
        # 同步反映平仓后的状态，否则会与上面的
        # equity_curve 记录自相矛盾
        # （equity 是平仓后现金，而 shares 仍是平仓前股数）。
        #
        # 平仓价用的是最后一天收盘价，
        # 因此平仓后 position_value 为 0。
        equity_curve[-1]["equity"] = cash
        equity_curve[-1]["cash"] = cash
        equity_curve[-1]["shares"] = 0
        equity_curve[-1]["position_value"] = 0.0

    # ==========================================================
    # 6. 计算指标
    # ==========================================================

    final_capital = cash

    metrics = calculate_metrics(
        initial_capital=initial_capital,
        final_capital=final_capital,
        equity_curve=equity_curve,
        trades=trades,
    )

    return {
        **metrics,

        "initial_capital": initial_capital,
        "final_capital": final_capital,

        "commission_rate": commission_rate,
        "stamp_duty_rate": stamp_duty_rate,
        "slippage_rate": slippage_rate,
        "lot_size": lot_size,

        "trades": trades,
        "equity_curve": equity_curve,
    }


# ==============================================================
# 兼容旧接口
# ==============================================================

def run_ma_cross_backtest(
    df: pd.DataFrame,
    initial_capital: float = 100000.0,
    fast_period: int = 20,
    slow_period: int = 60,

    commission_rate: float = 0.0003,
    stamp_duty_rate: float = 0.0005,
    slippage_rate: float = 0.0001,

    lot_size: int = 100,
) -> dict:

    from engine.ma_strategy import MACrossStrategy

    strategy = MACrossStrategy(
        fast_period=fast_period,
        slow_period=slow_period,
    )

    return run_backtest(
        df=df,
        strategy=strategy,
        initial_capital=initial_capital,
        commission_rate=commission_rate,
        stamp_duty_rate=stamp_duty_rate,
        slippage_rate=slippage_rate,
        lot_size=lot_size,
    )