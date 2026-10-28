import pandas as pd

from engine.strategy import Strategy


class MACrossStrategy(Strategy):
    """
    MA20 / MA60 策略。

    注意：
    当前版本严格保持 JUJ 旧版回测逻辑。

    这里的 BUY / SELL 判断实际上是：
        MA_fast > MA_slow -> BUY
        MA_fast < MA_slow -> SELL

    暂时不修改为真正的“金叉/死叉”事件。
    """

    def __init__(
        self,
        fast_period: int = 20,
        slow_period: int = 60,
    ):
        self.fast_period = fast_period
        self.slow_period = slow_period

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

        df["trade_date"] = pd.to_datetime(df["trade_date"])

        df = (
            df.sort_values("trade_date")
            .reset_index(drop=True)
        )

        df["ma_fast"] = (
            df["close"]
            .rolling(window=self.fast_period)
            .mean()
        )

        df["ma_slow"] = (
            df["close"]
            .rolling(window=self.slow_period)
            .mean()
        )

        df["signal"] = 0

        df.loc[
            df["ma_fast"] > df["ma_slow"],
            "signal",
        ] = 1

        df.loc[
            df["ma_fast"] < df["ma_slow"],
            "signal",
        ] = -1

        # 只保留指标已经完整形成的数据
        df = df.dropna(
            subset=["ma_fast", "ma_slow"]
        ).reset_index(drop=True)

        return df
