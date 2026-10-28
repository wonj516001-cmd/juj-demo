import math
import pandas as pd


def calculate_max_drawdown(equity_curve: pd.Series) -> float:
    """
    计算最大回撤

    返回：
        例如 -0.25 表示最大回撤 -25%
    """

    if equity_curve.empty:
        return 0.0

    rolling_max = equity_curve.cummax()

    drawdown = (
        equity_curve / rolling_max - 1
    )

    return float(drawdown.min())


def calculate_annual_return(
    initial_capital: float,
    final_capital: float,
    start_date,
    end_date,
) -> float:
    """
    计算年化收益率

    返回：
        小数形式，例如 0.12 = 12%
    """

    if initial_capital <= 0:
        return 0.0

    if final_capital <= 0:
        return -1.0

    start_date = pd.to_datetime(start_date)
    end_date = pd.to_datetime(end_date)

    days = (end_date - start_date).days

    if days <= 0:
        return 0.0

    years = days / 365.25

    return (
        (final_capital / initial_capital)
        ** (1 / years)
        - 1
    )


def calculate_sharpe_ratio(
    equity_curve: pd.Series,
    periods_per_year: int = 252,
) -> float:
    """
    根据每日权益曲线计算 Sharpe Ratio。

    默认：
        252 个交易日 / 年
        无风险利率暂时按照 0 处理。
    """

    if len(equity_curve) < 2:
        return 0.0

    returns = equity_curve.pct_change().dropna()

    if returns.empty:
        return 0.0

    std = returns.std()

    if std == 0 or pd.isna(std):
        return 0.0

    sharpe = (
        returns.mean()
        / std
        * math.sqrt(periods_per_year)
    )

    return float(sharpe)


def calculate_trade_metrics(
    trades: list[dict],
) -> dict:
    """
    根据已经完成的交易计算：

    - 交易次数
    - 胜率
    - 盈亏总额
    - 盈利总额
    - 亏损总额
    - Profit Factor
    """

    completed_trades = [
        trade
        for trade in trades
        if "pnl" in trade
    ]

    total_trades = len(completed_trades)

    if total_trades == 0:
        return {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate": 0.0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "profit_factor": 0.0,
        }

    pnls = [
        float(trade["pnl"])
        for trade in completed_trades
    ]

    winning_pnls = [
        pnl for pnl in pnls
        if pnl > 0
    ]

    losing_pnls = [
        pnl for pnl in pnls
        if pnl < 0
    ]

    winning_trades = len(winning_pnls)
    losing_trades = len(losing_pnls)

    win_rate = (
        winning_trades / total_trades
    )

    gross_profit = sum(winning_pnls)

    gross_loss = abs(sum(losing_pnls))

    if gross_loss == 0:
        profit_factor = float("inf")
    else:
        profit_factor = (
            gross_profit / gross_loss
        )

    return {
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "win_rate": win_rate,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
    }


def calculate_metrics(
    initial_capital: float,
    final_capital: float,
    equity_curve: list[dict],
    trades: list[dict],
) -> dict:
    """
    汇总所有回测指标。
    """

    if not equity_curve:
        return {
            "initial_capital": initial_capital,
            "final_capital": final_capital,
            "total_return": 0.0,
            "annual_return": 0.0,
            "max_drawdown": 0.0,
            "sharpe_ratio": 0.0,
            "total_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
        }

    equity_df = pd.DataFrame(
        equity_curve
    )

    equity_df["date"] = pd.to_datetime(
        equity_df["date"]
    )

    equity_series = (
        equity_df
        .set_index("date")["equity"]
    )

    start_date = equity_df["date"].iloc[0]
    end_date = equity_df["date"].iloc[-1]

    total_return = (
        final_capital / initial_capital - 1
    )

    annual_return = calculate_annual_return(
        initial_capital=initial_capital,
        final_capital=final_capital,
        start_date=start_date,
        end_date=end_date,
    )

    max_drawdown = calculate_max_drawdown(
        equity_series
    )

    sharpe_ratio = calculate_sharpe_ratio(
        equity_series
    )

    trade_metrics = calculate_trade_metrics(
        trades
    )

    return {
        "initial_capital": initial_capital,
        "final_capital": final_capital,

        "total_return": total_return,

        "annual_return": annual_return,

        "max_drawdown": max_drawdown,

        "sharpe_ratio": sharpe_ratio,

        **trade_metrics,
    }