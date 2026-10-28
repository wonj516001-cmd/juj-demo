"""
MACD 指数平滑异同移动平均策略。

=============================================================
指标定义（唯一权威口径，测试与实现必须一致）
=============================================================
指数移动平均采用 ewm(span=n, adjust=False)：

    EMA_n 的一阶递推：ema[i] = ema[i-1] + alpha × (x[i] - ema[i-1])
    alpha = 2 / (n + 1)

这与 pandas 的 ewm(span=n, adjust=False) 完全等价，
也与 RSI / KDJ 中使用的平滑口径同源。

定义：

    ema_fast  = close.ewm(span=fast_period,  adjust=False).mean()
    ema_slow  = close.ewm(span=slow_period,  adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal_period, adjust=False).mean()
    histogram = macd_line - signal_line

关于 adjust=False（重要）：
    adjust=True 会把权重反向归一化到「样本不足时也按已有数据
    重新缩放」的口径，使首值不同于标准 EMA 递推；
    标准 MACD 使用 adjust=False，本实现与之保持一致。

关于 histogram：
    定义为两线之差（未乘 2）。
    部分文献定义为 (macd - signal) × 2，
    两者只差一个常数倍，方向与过零时刻完全相同。
    本实现采用不乘 2 的定义，不用于交易判定，
    仅作为可读的辅助输出列。

=============================================================
交易规则
=============================================================
BUY  ：macd_line 由下向上穿越 signal_line（金叉）
SELL ：macd_line 由上向下穿越 signal_line（死叉）
HOLD ：其他

金叉判定使用前一日的两线关系：
    只比较当日与昨日，不使用未来数据。

=============================================================
预热期
=============================================================
ewm 本身不产生 NaN，因此必须【主动】构造预热期：
没有足够历史数据时，前若干行的 EMA 尚未稳定，
若参与交易会产生虚假交叉。

本实现丢弃前 slow_period + signal_period - 2 行，
使剩余数据的 EMA 递推已经充分展开。
丢弃后剩余首行即 equity_curve 的实际起点。
"""

import pandas as pd

from engine.strategy import Strategy


class MACDStrategy(Strategy):
    """MACD 金叉买入 / 死叉卖出策略。"""

    def __init__(
        self,
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9,
    ):
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.signal_period = signal_period

    def generate_signals(
        self,
        df: pd.DataFrame,
    ) -> pd.DataFrame:

        df = df.copy()

        required_columns = ["trade_date", "close"]

        missing_columns = [
            column
            for column in required_columns
            if column not in df.columns
        ]

        if missing_columns:
            raise ValueError(
                f"strategy data missing fields: {missing_columns}"
            )

        if df.empty:
            raise ValueError("strategy data is empty")

        df["trade_date"] = pd.to_datetime(
            df["trade_date"]
        )

        df = (
            df.sort_values("trade_date")
            .reset_index(drop=True)
        )

        # ======================================================
        # 1. 计算 MACD
        # ======================================================

        df["ema_fast"] = df["close"].ewm(
            span=self.fast_period,
            adjust=False,
        ).mean()

        df["ema_slow"] = df["close"].ewm(
            span=self.slow_period,
            adjust=False,
        ).mean()

        df["macd_line"] = (
            df["ema_fast"] - df["ema_slow"]
        )

        df["signal_line"] = df["macd_line"].ewm(
            span=self.signal_period,
            adjust=False,
        ).mean()

        # 定义：两线之差，不乘 2。
        df["macd_hist"] = (
            df["macd_line"] - df["signal_line"]
        )

        # ======================================================
        # 2. 主动构造预热期
        # ======================================================

        warmup = (
            self.slow_period + self.signal_period - 2
        )

        if warmup > 0:
            df = df.iloc[warmup:].reset_index(
                drop=True
            )

        # ======================================================
        # 3. 产生交易信号
        # ======================================================

        df["signal"] = 0

        above_signal_yesterday = (
            df["macd_line"].shift(1)
            > df["signal_line"].shift(1)
        )

        at_or_below_signal_today = (
            df["macd_line"] <= df["signal_line"]
        )

        golden_cross = (
            above_signal_yesterday & at_or_below_signal_today
        )

        below_signal_yesterday = (
            df["macd_line"].shift(1)
            < df["signal_line"].shift(1)
        )

        at_or_above_signal_today = (
            df["macd_line"] >= df["signal_line"]
        )

        death_cross = (
            below_signal_yesterday & at_or_above_signal_today
        )

        df.loc[golden_cross, "signal"] = 1
        df.loc[death_cross, "signal"] = -1

        return df