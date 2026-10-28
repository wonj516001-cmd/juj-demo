"""
布林带（Bollinger Bands）策略。

=============================================================
指标定义（唯一权威口径，测试与实现必须一致）
=============================================================
中轨 middle = close 的 period 日滚动简单均值（SMA）
上轨 upper = middle + std_dev × 标准差
下轨 lower = middle - std_dev × 标准差

标准差使用【总体标准差】（pandas 默认 ddof=0）：
    std = sqrt( Σ(x - mean)² / period )

为什么用 ddof=0 而不是样本标准差（ddof=1）：
    period 个样本的样本标准差在 period=1 时无定义（除零），
    会额外产生一个 NaN 预热点；
    总体标准差在 period=1 时恒为 0，定义良好。
    这一点必须与测试保持一致，否则无法手工验算。

滚动窗口不做居中（center=False）：
    指标值只由当日及历史数据构成，绝不使用未来数据。

=============================================================
交易规则
=============================================================
BUY  ：收盘价由【下向上】穿越下轨
SELL ：收盘价由【下向上】穿越中轨
HOLD ：其他

为什么卖出用「上穿中轨」而不是「下穿下轨」：
    与引擎的 T+1 成交约定配合，
    上穿中轨是价格脱离超跌区并恢复趋势的确认信号，
    避免在带状区间内反复触发。
    规则一旦确定即固定，不因回测表现调整。

=============================================================
与引擎的接口约定
=============================================================
返回的 DataFrame 必须包含 signal 列（1/0/-1）。
预热期数据通过 dropna 丢弃，
丢弃后剩余首行即 equity_curve 的实际起点，
这与基准曲线裁剪逻辑（api/demo.py）依赖同一前提。
"""

import pandas as pd

from engine.strategy import Strategy


class BollingerStrategy(Strategy):
    """布林带回归策略。"""

    def __init__(
        self,
        period: int = 20,
        std_dev: float = 2.0,
    ):
        self.period = period
        self.std_dev = std_dev

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
        # 1. 计算三轨
        # ======================================================

        rolling = df["close"].rolling(
            window=self.period
        )

        df["bb_middle"] = rolling.mean()

        # ddof=0（总体标准差），理由见模块文档。
        df["bb_std"] = rolling.std(ddof=0)

        df["bb_upper"] = (
            df["bb_middle"] + self.std_dev * df["bb_std"]
        )

        df["bb_lower"] = (
            df["bb_middle"] - self.std_dev * df["bb_std"]
        )

        # ======================================================
        # 2. 产生交易信号
        #
        # 两次穿越都使用 shift(1) 的前一日状态，
        # 因此判定「是否发生穿越」只看当日与昨日，
        # 不引入任何未来数据。
        # ======================================================

        df["signal"] = 0

        # ------------------------------------------------------
        # 买入：收盘价由下向上穿越下轨
        # ------------------------------------------------------

        below_lower_yesterday = (
            df["close"].shift(1) <= df["bb_lower"].shift(1)
        )

        at_or_above_lower_today = (
            df["close"] >= df["bb_lower"]
        )

        cross_up_lower = (
            below_lower_yesterday & at_or_above_lower_today
        )

        # ------------------------------------------------------
        # 卖出：收盘价由下向上穿越中轨
        # ------------------------------------------------------

        below_middle_yesterday = (
            df["close"].shift(1) < df["bb_middle"].shift(1)
        )

        at_or_above_middle_today = (
            df["close"] >= df["bb_middle"]
        )

        cross_up_middle = (
            below_middle_yesterday & at_or_above_middle_today
        )

        # ------------------------------------------------------
        # 优先级：买入优先于卖出
        #
        # 原因：
        #     行情单日大幅反弹时，收盘价可能同时越过下轨与中轨，
        #     此时两个条件都成立。
        #     若直接依次赋值，后写入的卖出会覆盖买入信号，
        #     结果是「刚判定超跌反弹就立刻清仓」，
        #     语义自相矛盾。
        #
        #     因此卖出必须排除当日同时发生下轨上穿的情况。
        #
        # 另一个必须处理的情况：完全平坦行情。
        #     此时 close == middle == lower，
        #     「<= lower」与「>= lower」同时恒为真，
        #     若不排除，会在每一天都判定为下轨上穿，
        #     退化为「每日买入」。
        #     故下轨穿越要求前一日**严格低于**下轨。
        # ------------------------------------------------------

        strictly_below_lower_yesterday = (
            df["close"].shift(1) < df["bb_lower"].shift(1)
        )

        cross_up_lower = (
            strictly_below_lower_yesterday
            & at_or_above_lower_today
        )

        df.loc[cross_up_lower, "signal"] = 1

        # 卖出：当日未发生下轨上穿时才允许
        sell_mask = cross_up_middle & ~cross_up_lower

        df.loc[sell_mask, "signal"] = -1

        # ======================================================
        # 3. 删除三轨尚未形成的无效数据
        #
        # 与 KDJ / MA 的处理方式一致：
        # 预热期不产生任何交易。
        # ======================================================

        df = df.dropna(
            subset=["bb_middle", "bb_std", "bb_upper", "bb_lower"]
        ).reset_index(drop=True)

        return df