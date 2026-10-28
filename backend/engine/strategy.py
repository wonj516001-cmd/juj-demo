from abc import ABC, abstractmethod

import pandas as pd


class Strategy(ABC):
    """
    JUJ 策略基类。

    策略只负责：
    1. 根据市场数据产生交易信号
    2. 保存策略自己的参数

    策略不负责：
    - 资金管理
    - 仓位计算
    - 手续费
    - 滑点
    - 买卖执行
    - 收益率计算
    """

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        根据行情数据生成交易信号。

        返回的数据必须包含 signal 列：

        1  = BUY
        0  = HOLD
        -1 = SELL
        """
        raise NotImplementedError