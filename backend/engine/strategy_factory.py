"""
策略工厂。

按strategy_type 创建具体 Strategy 实例。

职责边界：
    只负责「类型 -> 类」的映射与参数传递，
    不做任何参数校验。
    调用方（API 层）必须先经normalize_strategy_parameters，
    因此本文件不再重复校验，避免两套规则漂移。

新增策略时必须同时登记：
    1. engine/strategy_validator.py 的 STRATEGY_SPECS
    2. 本文件的 create_strategy
两处遗漏任意一处都会导致「能选不能用」或「不能用却能选」。
"""

from engine.ma_strategy import MACrossStrategy
from engine.kdj_strategy import KDJStrategy
from engine.bollinger_strategy import BollingerStrategy
from engine.macd_strategy import MACDStrategy
from engine.rsi_strategy import RSIStrategy


def create_strategy(
    strategy_type: str,
    parameters: dict,
):
    """
    根据 strategy_type 创建具体 Strategy。
    """

    if strategy_type == "ma_cross":
        return MACrossStrategy(
            fast_period=parameters.get(
                "fast_period",
                20,
            ),
            slow_period=parameters.get(
                "slow_period",
                60,
            ),
        )

    elif strategy_type == "kdj":
        return KDJStrategy(
            rsv_period=parameters.get(
                "rsv_period",
                9,
            ),
            k_period=parameters.get(
                "k_period",
                3,
            ),
            d_period=parameters.get(
                "d_period",
                3,
            ),
        )

    elif strategy_type == "bollinger":
        return BollingerStrategy(
            period=parameters.get("period", 20),
            std_dev=parameters.get("std_dev", 2.0),
        )

    elif strategy_type == "rsi":
        return RSIStrategy(
            period=parameters.get("period", 14),
            oversold=parameters.get("oversold", 30.0),
            overbought=parameters.get(
                "overbought",
                70.0,
            ),
        )

    elif strategy_type == "macd":
        return MACDStrategy(
            fast_period=parameters.get(
                "fast_period",
                12,
            ),
            slow_period=parameters.get(
                "slow_period",
                26,
            ),
            signal_period=parameters.get(
                "signal_period",
                9,
            ),
        )

    else:
        raise ValueError(
            f"unsupported strategy type: {strategy_type}"
        )