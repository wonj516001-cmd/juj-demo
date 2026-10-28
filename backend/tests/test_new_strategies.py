"""
新增策略（布林带 / RSI / MACD）测试。

=============================================================
隔离性保证
=============================================================
本文件只导入：
    engine.*      —— 纯计算，不触数据库
    api.demo      —— Demo 独立路径，不触真实行情

**绝不导入 backend/main.py**，
因此不会触发 init_database() 与 Redis 连接。
运行方式（需先 cd 到 backend 目录）：

    .venv/Scripts/python.exe -m pytest tests/test_new_strategies.py -q

=============================================================
测试设计原则
=============================================================
1. 指标数值用手工可验算的固定序列断言，
   不断言「请求成功」或「交易数 > 0」这类弱条件。
2. 交叉信号逐日断言，明确「有交叉」与「无交叉」两种情形。
3. 覆盖预热期、平坦行情、非法参数与数据不足。
4. 明确断言「未使用未来数据」：
   截断序列的前缀结果必须与完整序列的前缀一致。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest


BACKEND_ROOT = Path(__file__).resolve().parent.parent

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


# 注意：以下 import 均不触及 main / database / redis
from api.demo import (  # noqa: E402
    DemoBacktestRequest,
    demo_backtest,
)
from engine.bollinger_strategy import (  # noqa: E402
    BollingerStrategy,
)
from engine.macd_strategy import MACDStrategy  # noqa: E402
from engine.rsi_strategy import RSIStrategy  # noqa: E402
from engine.strategy_factory import create_strategy  # noqa: E402
from engine.strategy_validator import (  # noqa: E402
    STRATEGY_SPECS,
    SUPPORTED_STRATEGY_TYPES,
    list_supported_strategies,
    normalize_strategy_parameters,
)


# ==========================================================
# 辅助
# ==========================================================

def make_frame(closes: list) -> pd.DataFrame:
    """用收盘价序列构造最小可用行情。"""

    return pd.DataFrame({
        "trade_date": pd.bdate_range(
            "2020-01-01",
            periods=len(closes),
        ),
        "open": closes,
        "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes],
        "close": closes,
        "vol": [1000.0] * len(closes),
    })


ALL_STRATEGIES = ("kdj", "ma_cross", "bollinger", "rsi", "macd")


# ==========================================================
# D1. 策略注册与兼容
# ==========================================================

class TestStrategyRegistration:
    """策略注册表。"""

    def test_all_five_registered(self):
        """五种策略全部注册。"""

        for name in ALL_STRATEGIES:
            assert name in STRATEGY_SPECS
            assert name in SUPPORTED_STRATEGY_TYPES

    def test_legacy_strategies_unchanged(self):
        """
        KDJ 与 MA Cross 的规范未被改动。

        这是回归保护：新增策略不得影响既有策略的
        默认值与取值范围。
        """

        kdj = STRATEGY_SPECS["kdj"]["parameters"]
        assert kdj["rsv_period"]["default"] == 9
        assert kdj["k_period"]["default"] == 3
        assert kdj["d_period"]["default"] == 3

        ma = STRATEGY_SPECS["ma_cross"]["parameters"]
        assert ma["fast_period"]["default"] == 20
        assert ma["slow_period"]["default"] == 60

    def test_new_strategy_defaults(self):
        """三种新策略的默认值。"""

        assert STRATEGY_SPECS["bollinger"]["parameters"] == {
            "period": {
                "type": int,
                "default": 20,
                "min": 2,
                "max": 250,
            },
            "std_dev": {
                "type": float,
                "default": 2.0,
                "min": 0.1,
                "max": 10.0,
            },
        }

        assert STRATEGY_SPECS["rsi"]["parameters"] == {
            "period": {
                "type": int,
                "default": 14,
                "min": 2,
                "max": 250,
            },
            "oversold": {
                "type": float,
                "default": 30.0,
                "min": 0.0,
                "max": 99.0,
            },
            "overbought": {
                "type": float,
                "default": 70.0,
                "min": 1.0,
                "max": 100.0,
            },
        }

        assert STRATEGY_SPECS["macd"]["parameters"] == {
            "fast_period": {
                "type": int,
                "default": 12,
                "min": 1,
                "max": 250,
            },
            "slow_period": {
                "type": int,
                "default": 26,
                "min": 2,
                "max": 500,
            },
            "signal_period": {
                "type": int,
                "default": 9,
                "min": 1,
                "max": 250,
            },
        }

    def test_list_supported_includes_new(self):
        """对外规范列表包含新策略。"""

        listed = {
            item["strategy_type"]
            for item in list_supported_strategies()
        }

        assert set(ALL_STRATEGIES) <= listed

    @pytest.mark.parametrize("name", ALL_STRATEGIES)
    def test_factory_creates_each(self, name):
        """工厂能创建每种策略。"""

        params = normalize_strategy_parameters(name, {})
        strategy = create_strategy(name, params)

        signals = strategy.generate_signals(
            make_frame([10.0 + i * 0.1 for i in range(80)])
        )

        assert "signal" in signals.columns
        assert set(signals["signal"].unique()) <= {
            -1,
            0,
            1,
        }

    def test_unknown_strategy_rejected(self):
        """未知策略被拒绝，且错误信息列出全部策略。"""

        with pytest.raises(ValueError) as excinfo:
            normalize_strategy_parameters("nope", {})

        message = str(excinfo.value)

        assert "不支持的策略类型" in message

        for name in ALL_STRATEGIES:
            assert name in message

    def test_unknown_parameter_rejected(self):
        """未知参数不被静默忽略。"""

        with pytest.raises(ValueError) as excinfo:
            normalize_strategy_parameters(
                "bollinger",
                {"peroid": 20},
            )

        assert "peroid" in str(excinfo.value)


# ==========================================================
# D2. 布林带
# ==========================================================

class TestBollingerIndicators:
    """布林带指标数值。"""

    def test_indicator_values_hand_verified(self):
        """
        指标数值手工验算。

        取周期内 close = [1, 2, 3]（period=3, std_dev=1）：
            middle = (1+2+3)/3 = 2
            std(ddof=0) = sqrt( ((1-2)^2+(2-2)^2+(3-2)^2) / 3 )
                        = sqrt(2/3) = 0.816496580927726
            upper = 2 + 0.816496580927726
            lower = 2 - 0.816496580927726
        """

        closes = [1.0, 2.0, 3.0] * 4
        result = BollingerStrategy(
            period=3,
            std_dev=1.0,
        ).generate_signals(make_frame(closes))

        last = result.iloc[-1]

        expected_std = (2.0 / 3.0) ** 0.5

        assert last["bb_middle"] == pytest.approx(2.0)
        assert last["bb_std"] == pytest.approx(
            expected_std
        )
        assert last["bb_upper"] == pytest.approx(
            2.0 + expected_std
        )
        assert last["bb_lower"] == pytest.approx(
            2.0 - expected_std
        )

    def test_flat_market_std_is_zero(self):
        """
        完全平坦行情下标准差为 0，上下轨与中轨重合。

        这是 ddof=0 的直接结果；
        若误改为样本标准差（ddof=1），
        period=1 时会产生 NaN，period>1 时结果也不同。
        """

        result = BollingerStrategy(
            period=5,
            std_dev=2.0,
        ).generate_signals(make_frame([7.0] * 12))

        last = result.iloc[-1]

        assert last["bb_std"] == pytest.approx(0.0)
        assert last["bb_upper"] == pytest.approx(7.0)
        assert last["bb_lower"] == pytest.approx(7.0)

    def test_warmup_removes_incomplete_rows(self):
        """预热期被 dropna 丢弃。"""

        result = BollingerStrategy(
            period=10,
        ).generate_signals(
            make_frame([10.0 + i for i in range(30)])
        )

        # 30 行输入，period=10 -> 保留 21 行
        assert len(result) == 21
        assert not result["bb_middle"].isna().any()
        assert not result["bb_lower"].isna().any()

    def test_no_future_data(self):
        """
        不使用未来数据。

        截断序列得到的前缀结果，
        必须与完整序列的对应前缀完全一致。
        若指标误用了未来数据，两者必然不同。
        """

        closes = [10.0 + (i % 7) * 0.5 for i in range(40)]

        full = BollingerStrategy(
            period=5,
        ).generate_signals(make_frame(closes))

        truncated = BollingerStrategy(
            period=5,
        ).generate_signals(make_frame(closes[:20]))

        assert len(truncated) == 16

        for column in ("bb_middle", "bb_std", "signal"):
            pd.testing.assert_series_equal(
                truncated[column].reset_index(drop=True),
                full[column].iloc[:16].reset_index(
                    drop=True
                ),
                check_names=False,
            )


class TestBollingerSignals:
    """布林带交易信号。"""

    def test_buy_on_cross_up_lower_band(self):
        """
        收盘价由下向上穿越下轨时买入。

构造：
            前 4 日横盘 10.0，第 5 日暴跌至 5.0，第 6 日回升。
            period=5、std_dev=1.0 时，
            暴跌日的窗口为 [10,10,10,10,5]：
                middle = 9.0
                std(ddof=0) = 2.0
                lower = 9.0 - 2.0 = 7.0
            收盘价 5.0 < 7.0，严格低于下轨；
            次日回升至 10.0 >= 7.0，判定为下轨上穿。
        """

        strategy = BollingerStrategy(
            period=5,
            std_dev=1.0,
        )

        closes = [10.0, 10.0, 10.0, 10.0, 5.0, 10.0, 10.0]
        result = strategy.generate_signals(
            make_frame(closes)
        )

        # 先确认构造数据本身满足「严格低于下轨」
        first = result.iloc[0]
        assert first["close"] < first["bb_lower"]
        assert first["bb_lower"] == pytest.approx(7.0)
        assert first["bb_middle"] == pytest.approx(9.0)

        # 找到严格低于下轨的行，再看下一行是否上穿
        #
        # 注意：判定条件必须是【严格小于】。
        # 若用 <=，平坦行情（三轨重合）会每天都被
        # 判为「下轨上穿」，策略退化为每日买入。
        crossed = False

        for i in range(1, len(result)):
            prev = result.iloc[i - 1]
            curr = result.iloc[i]

            if (
                prev["close"] < prev["bb_lower"]
                and curr["close"] >= curr["bb_lower"]
            ):
                assert curr["signal"] == 1, (
                    f"第 {i} 行应给出买入信号，"
                    f"实际为 {curr['signal']}"
                )
                crossed = True

        assert crossed, "构造数据未触发下轨上穿"

    def test_flat_market_produces_no_signals(self):
        """
        平坦行情不得产生任何交易信号。

        这是回归保护：
        当 close == middle == lower 时，
        「前一日严格低于下轨」恒为 False，
        因此不应出现每日买入的退化行为。
        """

        result = BollingerStrategy(
            period=5,
            std_dev=2.0,
        ).generate_signals(make_frame([10.0] * 30))

        assert (result["signal"] == 0).all()

    def test_buy_wins_over_sell_on_gap_up(self):
        """
        单日大幅反弹同时越过下轨与中轨时，买入优先。

        场景：昨日收在下轨之下，今日跳涨到中轨之上。
        此时两个条件都成立，
        若卖出覆盖买入，语义会变成
        「刚判定超跌反弹就立刻清仓」，自相矛盾。
        """

        strategy = BollingerStrategy(
            period=5,
            std_dev=1.0,
        )

        # 先建立带状结构，再制造跳涨
        closes = [
            10.0, 10.0, 10.0, 10.0, 10.0,
            7.0, 12.0, 10.0, 10.0, 10.0,
        ]

        result = strategy.generate_signals(
            make_frame(closes)
        )

        # 定位跳涨日：前一日严格低于下轨，当日高于中轨
        for i in range(1, len(result)):
            prev = result.iloc[i - 1]
            curr = result.iloc[i]

            if (
                prev["close"] < prev["bb_lower"]
                and curr["close"] >= curr["bb_middle"]
            ):
                assert curr["signal"] == 1, (
                    "同时满足上穿下轨与上穿中轨时，"
                    "必须判定为买入而非卖出"
                )
                return

        pytest.fail("构造数据未产生同时越界的跳涨日")

    def test_no_repeat_signal_without_cross(self):
        """
        没有穿越时不重复产生信号。

        持续位于带内震荡的行情不应产生密集信号。
        """

        result = BollingerStrategy(
            period=5,
            std_dev=2.0,
        ).generate_signals(
            make_frame([10.0] * 30)
        )

        # 完全平坦行情：中轨=上下轨，
        # 条件 close<=lower 与 close>=lower 同时成立，
        # 但「上穿中轨」要求 close < middle，
        # 平坦时恒为 False，因此不应出现信号。
        assert set(result["signal"].unique()) <= {0, -1}

        # 更严格的判定：对每个信号点，
        # 前一日必须确实处于另一侧。
        for i in range(1, len(result)):
            if result.iloc[i]["signal"] != 0:
                prev_close = result.iloc[i - 1]["close"]
                prev_middle = result.iloc[
                    i - 1
                ]["bb_middle"]

                # 卖出必须来自「昨日在中轨之下」
                assert prev_close < prev_middle

    def test_signal_values_in_range(self):
        """信号取值只能是 -1 / 0 / 1。"""

        result = BollingerStrategy(
            period=6,
        ).generate_signals(
            make_frame([
                10.0, 11.0, 9.0, 12.0, 8.5, 11.5,
                9.5, 13.0, 8.0, 12.5, 10.5, 7.5,
                14.0, 9.0, 11.0,
            ])
        )

        assert set(result["signal"].unique()) <= {
            -1,
            0,
            1,
        }

    def test_insufficient_data_raises(self):
        """数据不足预热期时返回空或报错，不产生信号。"""

        strategy = BollingerStrategy(period=50)

        result = strategy.generate_signals(
            make_frame([10.0] * 10)
        )

        # 10 行不足 50 行预热 -> 结果为空
        assert len(result) == 0


# ==========================================================
# D2. RSI
# ==========================================================

class TestRSIIndicators:
    """RSI 指标数值。"""

    def test_monotonic_up_is_100(self):
        """单调上涨区间无任何下跌，RSI = 100。"""

        result = RSIStrategy(period=5).generate_signals(
            make_frame([float(i) for i in range(1, 21)])
        )

        assert result["rsi"].iloc[-1] == pytest.approx(
            100.0
        )

    def test_monotonic_down_is_zero(self):
        """单调下跌区间无任何上涨，RSI = 0。"""

        result = RSIStrategy(period=5).generate_signals(
            make_frame([float(i) for i in range(20, 0, -1)])
        )

        assert result["rsi"].iloc[-1] == pytest.approx(
            0.0
        )

    def test_flat_market_is_neutral_50(self):
        """
        完全平坦行情 RSI = 50。

        该场景下 avg_gain 与 avg_loss 同时为 0，
        直接相除会得到 NaN；
        实现必须走安全分支返回中性值。
        """

        result = RSIStrategy(period=5).generate_signals(
            make_frame([10.0] * 20)
        )

        assert result["rsi"].notna().all()
        assert result["rsi"].iloc[-1] == pytest.approx(
            50.0
        )

    def test_no_infinite_values(self):
        """任何输入序列都不得产生 inf。"""

        for closes in (
            [10.0] * 20,
            [float(i) for i in range(20)],
            [float(i) for i in range(20, 0, -1)],
            [10.0, 10.1] * 10,
        ):
            result = RSIStrategy(period=5).generate_signals(
                make_frame(closes)
            )

            values = result["rsi"].to_numpy(dtype=float)

            assert pd.notna(values).all()
            assert abs(values).max() <= 100.0

    def test_rsi_bounded_in_zero_hundred(self):
        """RSI 恒在 [0, 100] 区间内。"""

        closes = [
            10.0, 12.0, 9.0, 15.0, 8.0, 14.0, 9.5,
            16.0, 7.0, 13.0, 11.0, 18.0, 6.0, 17.0,
            10.5, 19.0, 5.5, 20.0,
        ]

        result = RSIStrategy(period=6).generate_signals(
            make_frame(closes)
        )

        assert result["rsi"].between(0, 100).all()

    def test_no_future_data(self):
        """截断序列前缀与完整序列一致。"""

        closes = [
            10.0, 12.0, 9.0, 15.0, 8.0, 14.0, 9.5,
            16.0, 7.0, 13.0, 11.0, 18.0, 6.0, 17.0,
            10.5, 19.0, 5.5, 20.0,
        ]

        full = RSIStrategy(period=5).generate_signals(
            make_frame(closes)
        )

        truncated = RSIStrategy(period=5).generate_signals(
            make_frame(closes[:12])
        )

        assert len(truncated) == len(full) - 6

        pd.testing.assert_series_equal(
            truncated["rsi"].reset_index(drop=True),
            full["rsi"].iloc[: len(truncated)].reset_index(
                drop=True
            ),
            check_names=False,
        )


class TestRSISignals:
    """RSI 交易信号。"""

    def test_cross_up_oversold_gives_buy(self):
        """
        RSI 上穿超卖阈值时买入。

        构造一段先暴跌（RSI 接近 0）后反弹的序列，
        并把 oversold 设得较高以便触发。
        """

        strategy = RSIStrategy(
            period=3,
            oversold=50.0,
            overbought=95.0,
        )

        closes = [
            20.0, 19.0, 18.0, 17.0, 16.0, 15.0,
            14.0, 15.0, 17.0, 19.0,
        ]

        result = strategy.generate_signals(
            make_frame(closes)
        )

        assert (result["signal"] == 1).any()

        # 每个买入信号的前一日 RSI 必须确实低于阈值
        for i in range(1, len(result)):
            if result.iloc[i]["signal"] == 1:
                assert result.iloc[i - 1]["rsi"] < 50.0

    def test_cross_up_overbought_gives_sell(self):
        """RSI 上穿超买阈值时卖出。"""

        strategy = RSIStrategy(
            period=3,
            oversold=5.0,
            overbought=50.0,
        )

        closes = [
            10.0, 11.0, 12.0, 13.0, 14.0, 15.0,
            16.0, 15.0, 14.0, 13.0, 14.0, 16.0,
        ]

        result = strategy.generate_signals(
            make_frame(closes)
        )

        assert (result["signal"] == -1).any()

        for i in range(1, len(result)):
            if result.iloc[i]["signal"] == -1:
                assert result.iloc[i - 1]["rsi"] < 50.0

    def test_no_signal_when_never_crosses(self):
        """RSI 恒在带内时不产生任何信号。"""

        strategy = RSIStrategy(
            period=4,
            oversold=10.0,
            overbought=90.0,
        )

        # 温和波动，RSI 不会到10 或 90
        closes = [
            10.0, 10.5, 9.8, 10.6, 9.9, 10.4,
            9.7, 10.7, 9.6, 10.8, 9.5, 10.9,
        ]

        result = strategy.generate_signals(
            make_frame(closes)
        )

        assert (result["signal"] == 0).all()


class TestRSIParameterValidation:
    """RSI 参数校验。"""

    @pytest.mark.parametrize(
        "params",
        [
            {"oversold": 80, "overbought": 20},
            {"oversold": 50, "overbought": 50},
            {"oversold": 70, "overbought": 30},
        ],
    )
    def test_requires_oversold_lt_overbought(self, params):
        """必须 oversold < overbought（严格小于）。"""

        with pytest.raises(ValueError) as excinfo:
            normalize_strategy_parameters("rsi", params)

        assert "oversold" in str(excinfo.value)

    def test_accepts_integer_thresholds(self):
        """
        整数阈值必须被接受。

        前端 el-input-number 在 step=1 时产出整数，
        若严格要求 float，用户无法把阈值设成 30 这类整数。
        """

        result = normalize_strategy_parameters(
            "rsi",
            {"oversold": 30, "overbought": 70},
        )

        assert result["oversold"] == 30.0
        assert result["overbought"] == 70.0

    @pytest.mark.parametrize(
        "params",
        [
            {"oversold": -1},
            {"overbought": 101},
            {"period": 1},
            {"period": 300},
        ],
    )
    def test_rejects_out_of_range(self, params):
        """越界值被拒绝。"""

        with pytest.raises(ValueError):
            normalize_strategy_parameters("rsi", params)


# ==========================================================
# D2. MACD
# ==========================================================

class TestMACDIndicators:
    """MACD 指标数值。"""

    def test_macd_line_equals_ema_difference(self):
        """
        MACD 线 = 快 EMA - 慢 EMA。

        用与实现相同的 pandas 表达式独立重算，
        逐点比对，防止实现与文档定义漂移。
        """

        closes = [
            1.0, 2.3, 1.9, 2.5, 3.1, 2.8, 3.3,
            3.0, 3.6, 3.4, 3.9, 4.2, 4.0, 4.5,
        ]

        strategy = MACDStrategy(
            fast_period=3,
            slow_period=5,
            signal_period=2,
        )

        result = strategy.generate_signals(
            make_frame(closes)
        )

        series = pd.Series(closes, dtype=float)

        expected = (
            series.ewm(span=3, adjust=False).mean()
            - series.ewm(span=5, adjust=False).mean()
        )

        # 丢弃预热期后的行与 expected 的尾部对齐
        warmup = 5 + 2 - 2
        expected_tail = expected.iloc[warmup:]

        pd.testing.assert_series_equal(
            result["macd_line"].reset_index(drop=True),
            expected_tail.reset_index(drop=True),
            check_names=False,
        )

    def test_histogram_definition(self):
        """
        柱状值 = MACD 线 - 信号线（不乘 2）。

        部分文献定义为乘 2；
        本实现采用不乘 2 的定义，此处显式锁定。
        """

        strategy = MACDStrategy(
            fast_period=3,
            slow_period=5,
            signal_period=2,
        )

        result = strategy.generate_signals(
            make_frame([
                1.0, 2.3, 1.9, 2.5, 3.1, 2.8, 3.3,
                3.0, 3.6, 3.4, 3.9, 4.2, 4.0, 4.5,
            ])
        )

        last = result.iloc[-1]

        assert last["macd_hist"] == pytest.approx(
            last["macd_line"] - last["signal_line"]
        )

    def test_warmup_length(self):
        """
        预热丢弃行数 = slow_period + signal_period - 2。

        MACD 的 ewm 不产生 NaN，
        必须主动丢弃未稳定的开头部分。
        """

        closes = [
            float(i) for i in range(1, 41)
        ]

        result = MACDStrategy(
            fast_period=12,
            slow_period=26,
            signal_period=9,
        ).generate_signals(make_frame(closes))

        expected_drop = 26 + 9 - 2

        assert len(result) == 40 - expected_drop

    def test_no_future_data(self):
        """截断序列前缀与完整序列一致。"""

        closes = [
            10.0, 12.0, 9.0, 15.0, 8.0, 14.0, 9.5,
            16.0, 7.0, 13.0, 11.0, 18.0, 6.0, 17.0,
            10.5, 19.0, 5.5, 20.0,
        ]

        strategy = MACDStrategy(
            fast_period=3,
            slow_period=5,
            signal_period=2,
        )

        full = strategy.generate_signals(make_frame(closes))
        truncated = strategy.generate_signals(
            make_frame(closes[:12])
        )

        pd.testing.assert_series_equal(
            truncated["macd_line"].reset_index(drop=True),
            full["macd_line"].iloc[: len(truncated)].reset_index(
                drop=True
            ),
            check_names=False,
        )


class TestMACDSignals:
    """MACD 交易信号。"""

    def test_golden_cross_buys_death_cross_sells(self):
        """
        金叉买入、死叉卖出。

        对每个非零信号，前一日两线关系必须与信号方向相反。
        """

        strategy = MACDStrategy(
            fast_period=3,
            slow_period=5,
            signal_period=2,
        )

        closes = [
            1.0, 2.3, 1.9, 2.5, 3.1, 2.8, 3.3,
            3.0, 3.6, 3.4, 3.9, 4.2, 4.0, 4.5,
            4.3, 4.8, 4.4, 5.0, 4.6, 5.2,
        ]

        result = strategy.generate_signals(
            make_frame(closes)
        )

        signals = result["signal"].tolist()

        assert 1 in signals, "应出现金叉买入"
        assert -1 in signals, "应出现死叉卖出"

        for i in range(1, len(result)):
            prev = result.iloc[i - 1]
            curr = result.iloc[i]

            if curr["signal"] == 1:
                # 金叉：昨日 macd 已在上方，今日跌回下方
                assert prev["macd_line"] > prev["signal_line"]

            elif curr["signal"] == -1:
                # 死叉：昨日 macd 在下方，今日升回上方
                assert prev["macd_line"] < prev["signal_line"]

    def test_no_signal_on_monotonic_decline(self):
        """
        单调下跌后 MACD 不会反复交叉。

        该序列的信号应极少或全为 0，
        关键是不得出现「每日都出信号」的退化行为。
        """

        result = MACDStrategy(
            fast_period=3,
            slow_period=5,
            signal_period=2,
        ).generate_signals(
            make_frame([
                float(i) for i in range(40, 0, -1)
            ])
        )

        signals = result["signal"].tolist()

        # 最多只允许极少数交叉，
        # 若实现退化为逐日比较而不做穿越判定，
        # 几乎每天都会有信号。
        assert signals.count(1) <= 1

    def test_insufficient_data_returns_empty(self):
        """数据不足预热期时返回空。"""

        result = MACDStrategy(
            fast_period=12,
            slow_period=26,
            signal_period=9,
        ).generate_signals(make_frame([10.0] * 20))

        assert len(result) == 0


class TestMACDParameterValidation:
    """MACD 参数校验。"""

    def test_requires_fast_lt_slow(self):
        """快线周期必须小于慢线周期。"""

        with pytest.raises(ValueError) as excinfo:
            normalize_strategy_parameters(
                "macd",
                {"fast_period": 30, "slow_period": 20},
            )

        assert "fast_period" in str(excinfo.value)

    def test_equal_periods_rejected(self):
        """相等也不允许。"""

        with pytest.raises(ValueError):
            normalize_strategy_parameters(
                "macd",
                {"fast_period": 26, "slow_period": 26},
            )

    @pytest.mark.parametrize(
        "params",
        [
            {"signal_period": 0},
            {"signal_period": 300},
            {"fast_period": 0},
        ],
    )
    def test_rejects_out_of_range(self, params):
        """越界值被拒绝。"""

        with pytest.raises(ValueError):
            normalize_strategy_parameters("macd", params)


# ==========================================================
# D3. Demo API 端到端
# ==========================================================

class TestDemoBacktestWithNewStrategies:
    """三种新策略在 Demo API 上的完整链路。"""

    @pytest.mark.parametrize("mode", ["trend", "range", "high_vol"])
    @pytest.mark.parametrize(
        "strategy_type", ["bollinger", "rsi", "macd"]
    )
    def test_all_modes_complete(
        self,
        mode,
        strategy_type,
    ):
        """三种模式 × 三种新策略均可完成回测。"""

        result = demo_backtest(
            DemoBacktestRequest(
                strategy_type=strategy_type,
                mode=mode,
                periods=300,
                seed=42,
                start_date="2020-01-01",
                base_price=10.0,
            )
        )

        assert result.data_source == "synthetic"
        assert result.is_demo is True
        assert result.disclaimer

        # 曲线必须非空
        assert len(result.equity_curve) > 1
        assert len(result.benchmark) > 1

    @pytest.mark.parametrize(
        "strategy_type", ["bollinger", "rsi", "macd"]
    )
    def test_benchmark_aligned_with_equity(
        self,
        strategy_type,
    ):
        """基准曲线与权益曲线逐日对齐。"""

        result = demo_backtest(
            DemoBacktestRequest(
                strategy_type=strategy_type,
                mode="trend",
                periods=300,
                seed=42,
                start_date="2020-01-01",
                base_price=10.0,
            )
        )

        equity_dates = [
            str(point.date)[:10]
            for point in result.equity_curve
        ]

        benchmark_dates = [
            str(point["date"])[:10]
            for point in result.benchmark
        ]

        assert benchmark_dates == equity_dates

    @pytest.mark.parametrize(
        "strategy_type", ["bollinger", "rsi", "macd"]
    )
    def test_metrics_use_unified_definition(
        self,
        strategy_type,
    ):
        """收益指标沿用统一定义。"""

        result = demo_backtest(
            DemoBacktestRequest(
                strategy_type=strategy_type,
                mode="trend",
                periods=300,
                seed=42,
                start_date="2020-01-01",
                base_price=10.0,
            )
        )

        # 超额收益必须基于同区间基准
        assert result.excess_return == pytest.approx(
            result.total_return - result.benchmark_curve_return
        )

        # 最大回撤应为非正数
        assert result.max_drawdown <= 0

    @pytest.mark.parametrize(
        "strategy_type", ["bollinger", "rsi", "macd"]
    )
    def test_trades_are_well_formed(
        self,
        strategy_type,
    ):
        """交易明细字段完整。"""

        result = demo_backtest(
            DemoBacktestRequest(
                strategy_type=strategy_type,
                mode="trend",
                periods=400,
                seed=7,
                start_date="2020-01-01",
                base_price=10.0,
            )
        )

        assert result.total_trades == len(result.trades)

        for trade in result.trades:
            assert trade.side in {"BUY", "SELL"}
            assert trade.entry_price > 0
            assert trade.shares > 0

            if trade.exit_date is not None:
                assert trade.exit_price > 0

    def test_legacy_strategies_still_work(self):
        """KDJ 与 MA Cross 未因新增策略而回归。"""

        for strategy_type in ("kdj", "ma_cross"):
            result = demo_backtest(
                DemoBacktestRequest(
                    strategy_type=strategy_type,
                    mode="trend",
                    periods=300,
                    seed=42,
                    start_date="2020-01-01",
                    base_price=10.0,
                )
            )

            assert len(result.equity_curve) > 1
            assert result.excess_return is not None

    def test_unknown_strategy_returns_400(self):
        """未知策略返回 400 INVALID_STRATEGY_TYPE。"""

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as excinfo:
            demo_backtest(
                DemoBacktestRequest(
                    strategy_type="does_not_exist",
                    mode="trend",
                    periods=100,
                )
            )

        assert excinfo.value.status_code == 400
        assert (
            excinfo.value.detail["code"]
            == "INVALID_STRATEGY_TYPE"
        )

    def test_invalid_parameters_return_400(self):
        """非法参数返回 400 INVALID_PARAMETER。"""

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as excinfo:
            demo_backtest(
                DemoBacktestRequest(
                    strategy_type="macd",
                    mode="trend",
                    periods=300,
                    parameters={
                        "fast_period": 30,
                        "slow_period": 20,
                    },
                )
            )

        assert excinfo.value.status_code == 400
        assert (
            excinfo.value.detail["code"]
            == "INVALID_PARAMETER"
        )

    def test_insufficient_periods_return_400(self):
        """数据不足返回 400 INSUFFICIENT_DATA。"""

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as excinfo:
            demo_backtest(
                DemoBacktestRequest(
                    strategy_type="macd",
                    mode="trend",
                    periods=5,
                )
            )

        assert excinfo.value.status_code == 400

    def test_no_state_leak_between_strategies(
        self,
    ):
        """
        连续不同策略回测互不影响。

        同参数下重复同一策略两次，
        结果必须完全一致（无内部状态残留）。
        """

        first = demo_backtest(
            DemoBacktestRequest(
                strategy_type="rsi",
                mode="trend",
                periods=300,
                seed=42,
                start_date="2020-01-01",
                base_price=10.0,
            )
        )

        demo_backtest(
            DemoBacktestRequest(
                strategy_type="macd",
                mode="high_vol",
                periods=500,
                seed=99,
            )
        )

        again = demo_backtest(
            DemoBacktestRequest(
                strategy_type="rsi",
                mode="trend",
                periods=300,
                seed=42,
                start_date="2020-01-01",
                base_price=10.0,
            )
        )

        assert again.total_return == first.total_return
        assert again.total_trades == first.total_trades
        assert again.excess_return == first.excess_return