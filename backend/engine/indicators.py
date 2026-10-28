import pandas as pd


def add_moving_averages(
    df: pd.DataFrame,
    fast_period: int = 20,
    slow_period: int = 60,
) -> pd.DataFrame:

    df = df.copy()

    df["ma_fast"] = (
        df["close"]
        .rolling(window=fast_period)
        .mean()
    )

    df["ma_slow"] = (
        df["close"]
        .rolling(window=slow_period)
        .mean()
    )

    return df