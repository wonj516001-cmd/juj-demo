import pandas as pd

from engine.strategy import Strategy


class KDJStrategy(Strategy):
    """
    KDJ 金叉 / 死叉策略。

    规则：
    - K 上穿 D：BUY
    - K 下穿 D：SELL
    - 其他：HOLD

    默认参数：
    - RSV 周期：9
    - K 平滑：3
    - D 平滑：3

    注意：
    当前版本只负责：
    1. 计算 KDJ
    2. 产生交易信号

    不负责：
    - 资金
    - 仓位
    - 手续费
    - 滑点
    - T+1
    - 回测指标
    """

    def __init__(
        self,
        rsv_period: int = 9,
        k_period: int = 3,
        d_period: int = 3,
    ):
        self.rsv_period = rsv_period
        self.k_period = k_period
        self.d_period = d_period

    def generate_signals(
        self,
        df: pd.DataFrame,
    ) -> pd.DataFrame:

        df = df.copy()

        required_columns = [
            "trade_date",
            "high",
            "low",
            "close",
        ]

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
        # 1. 计算 RSV
        # ======================================================

        lowest_low = (
            df["low"]
            .rolling(window=self.rsv_period)
            .min()
        )

        highest_high = (
            df["high"]
            .rolling(window=self.rsv_period)
            .max()
        )

        price_range = highest_high - lowest_low

        df["rsv"] = (
            (df["close"] - lowest_low)
            / price_range
            * 100
        )

        # ======================================================
        # 2. 计算 K / D / J
        # ======================================================

        df["k"] = (
            df["rsv"]
            .ewm(
                alpha=1 / self.k_period,
                adjust=False,
            )
            .mean()
        )

        df["d"] = (
            df["k"]
            .ewm(
                alpha=1 / self.d_period,
                adjust=False,
            )
            .mean()
        )

        df["j"] = (
            3 * df["k"]
            - 2 * df["d"]
        )

        # ======================================================
        # 3. 产生交易信号
        # ======================================================

        df["signal"] = 0

        golden_cross = (
            (df["k"] > df["d"])
            & (df["k"].shift(1) <= df["d"].shift(1))
        )

        death_cross = (
            (df["k"] < df["d"])
            & (df["k"].shift(1) >= df["d"].shift(1))
        )

        df.loc[golden_cross, "signal"] = 1
        df.loc[death_cross, "signal"] = -1

        # ======================================================
        # 4. 删除 KDJ 尚未形成的无效数据
        # ======================================================

        df = df.dropna(
            subset=["rsv", "k", "d", "j"]
        ).reset_index(drop=True)

        return df