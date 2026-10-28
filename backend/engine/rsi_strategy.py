"""
RSI 相对强弱指标策略。

=============================================================
指标定义（唯一权威口径，测试与实现必须一致）
=============================================================
采用 Wilder 平滑（与 KDJ 中的 ewm 口径一致）：

    1. 单日涨跌：
         delta  = close.diff()
         gain   = delta.clip(lower=0)
         loss   = -delta.clip(upper=0)

    2. Wilder 平均（alpha = 1 / period, adjust=False）：
         avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
         avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()

    3. RSI：
         RS = avg_gain / avg_loss
         RSI = 100 - 100 / (1 + RS) = 100 × avg_gain / avg_gain+avg_loss

为什么用 ewm(alpha=1/period) 而非简单滚动均值：
    Wilder 的原始定义是「过去 period 日的指数加权平均」，
    ewm(alpha=1/period) 正是该递推的闭式表达，
    且与本项目 KDJ 策略的平滑口径统一，便于理解与对照。

边界情况处理（必须安全，不得抛异常）：
  - 首行delta 为 NaN → 预热期，dropna 丢弃。
  - avg_loss == 0（区间内无任何下跌）→ avg_gain > 0 时 RSI = 100；
    此时除法必须走安全分支，不能产生 inf 或 NaN。
  - avg_gain 与 avg_loss 同时为 0（完全平坦的行情）→ RSI = 50，
    取中性值，避免 0/0。
  - close 恒定不变 → delta 恒为 0 → RSI 恒为 50，不产生信号。

=============================================================
交易规则
=============================================================
BUY  ：RSI 从超卖阈值【下方向上】穿越 oversold
SELL ：RSI 从超买阈值【下方向上】穿越 overbought
HOLD ：其他

注意 SELL 的方向是「上穿超买」而非「下穿超买」：
    与布林带同理，这是在带内震荡行情下的出场设计，
    规则固定，不因回测表现调整。

交叉判定使用前一日 RSI：
    只比较当日与昨日，不使用未来数据。
"""

import pandas as pd

from engine.strategy import Strategy


class RSIStrategy(Strategy):
    """RSI 超卖买入 / 超买卖出策略。"""

    def __init__(
        self,
        period: int = 14,
        oversold: float = 30.0,
        overbought: float = 70.0,
    ):
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

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
        # 1. 计算 RSI
        # ======================================================

        delta = df["close"].diff()

        gain = delta.clip(lower=0.0)
        loss = -delta.clip(upper=0.0)

        alpha = 1.0 / self.period

        avg_gain = gain.ewm(
            alpha=alpha,
            adjust=False,
        ).mean()

        avg_loss = loss.ewm(
            alpha=alpha,
            adjust=False,
        ).mean()

        # ------------------------------------------------------
        # 安全除法
        #
        # 分母为 avg_gain + avg_loss，可能为 0（完全平坦行情），
        # 此时直接相除会得到 NaN，必须显式分支。
        #
        # 三种情形：
        #   avg_loss == 0 且 avg_gain > 0  → RSI = 100
        #   avg_gain == 0 且 avg_loss == 0  → RSI = 50（中性）
        #   其余                            → 标准公式
        # ------------------------------------------------------

        total = avg_gain + avg_loss

        rsi = 100.0 * avg_gain / total

        only_gains = (avg_loss <= 0) & (avg_gain > 0)
        flat = (avg_gain <= 0) & (avg_loss <= 0)

        rsi = rsi.mask(only_gains, 100.0)
        rsi = rsi.mask(flat, 50.0)

        df["rsi"] = rsi

        # ======================================================
        # 2. 产生交易信号
        # ======================================================

        df["signal"] = 0

        below_oversold_yesterday = (
            df["rsi"].shift(1) < self.oversold
        )

        at_or_above_oversold_today = (
            df["rsi"] >= self.oversold
        )

        cross_up_oversold = (
            below_oversold_yesterday & at_or_above_oversold_today
        )

        below_overbought_yesterday = (
            df["rsi"].shift(1) < self.overbought
        )

        at_or_above_overbought_today = (
            df["rsi"] >= self.overbought
        )

        cross_up_overbought = (
            below_overbought_yesterday
            & at_or_above_overbought_today
        )

        # ------------------------------------------------------
        # 优先级：买入优先于卖出
        #
        # 原因：
        #     RSI 由超卖区直接反弹到超买区时
        #     （例如从 25 直接跳到 75），
        #     「上穿超卖」与「上穿超买」会同时成立。
        #     若依次赋值，卖出会覆盖买入，
        #     语义变成「刚超卖反弹就立刻清仓」。
        #
        #     因此与布林带保持同一规则：
        #     当日发生超卖上穿时，不允许同时判定超买上穿。
        #
        # 注意 oversold < overbought 是 validator 的硬约束，
        # 两个阈值不可能相等，规则不依赖该约束的正确性。
        # ------------------------------------------------------

        df.loc[cross_up_oversold, "signal"] = 1

        sell_mask = (
            cross_up_overbought & ~cross_up_oversold
        )

        df.loc[sell_mask, "signal"] = -1

        # ======================================================
        # 3. 删除预热期数据
        # ======================================================

        df = df.dropna(
            subset=["rsi"]
        ).reset_index(drop=True)

        return df