"""
JUJ Demo 回测 API —— 独立测试。

=============================================================
隔离性保证
=============================================================
本文件**不导入 main**，因此不会触发
main.py 模块级的 init_database()。

也不导入 database / redis / 真实行情加载器，
在数据库与网络均不可用时可以完整运行。

被测对象：
    backend/engine/demo_data.py  合成行情生成器
    backend/api/demo.py          Demo API 路由函数

不测：
    真实行情链路（由 tests/test_engine_offline.py 覆盖）
    指标扩展（阶段 C 尚未实施）

运行：
      cd backend
    .venv\\Scripts\\python.exe -m pytest tests/test_demo.py -v
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException
from pydantic import ValidationError


BACKEND_ROOT = Path(__file__).resolve().parent.parent

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


# 注意：以下 import 均不触及 main / database / redis
from api import demo as demo_api  # noqa: E402
from api.demo import (  # noqa: E402
    DemoBacktestRequest,
    demo_backtest,
    demo_modes,
)
from engine import demo_data as demo_data_module  # noqa: E402
from engine.backtest import run_backtest  # noqa: E402
from engine.demo_data import (  # noqa: E402
    DATA_SOURCE,
    DEFAULT_SEED,
    DISCLAIMER,
    MARKET_MODES,
    build_buy_and_hold,
    generate_demo_ohlcv,
    list_modes,
    validate_ohlcv,
)
from engine.strategy_factory import create_strategy  # noqa: E402
from engine.strategy_validator import (  # noqa: E402
    normalize_strategy_parameters,
)


def make_frame(
    periods: int = 300,
    mode: str = "trend",
    seed: int = 42,
) -> pd.DataFrame:
    """生成一份默认测试用行情。"""

    return generate_demo_ohlcv(
        periods=periods,
        mode=mode,
        seed=seed,
        start_date="2020-01-01",
        base_price=10.0,
    )


# ==========================================================
# 1. 可复现性
# ==========================================================

class TestReproducibility:
    """
    固定 seed 必须生成完全相同的数据。
    """

    @pytest.mark.parametrize(
        "mode",
        MARKET_MODES,
        ids=list(MARKET_MODES),
    )
    def test_same_seed_same_data(self, mode):
        """相同 seed 两次生成结果逐字节相同。"""

        first = make_frame(mode=mode, seed=12345)
        second = make_frame(mode=mode, seed=12345)

        assert first.equals(second)

        first_hash = hashlib.sha256(
            first.to_csv(index=False).encode()
        ).hexdigest()

        second_hash = hashlib.sha256(
            second.to_csv(index=False).encode()
        ).hexdigest()

        assert first_hash == second_hash

    @pytest.mark.parametrize(
        "mode",
        MARKET_MODES,
        ids=list(MARKET_MODES),
    )
    def test_different_seed_different_data(self, mode):
        """不同 seed 必须产生不同数据（否则 seed 无意义）。"""

        first = make_frame(mode=mode, seed=1)
        second = make_frame(mode=mode, seed=2)

        assert not first.equals(second)

    def test_default_seed_is_fixed(self):
        """默认 seed 是常量，不随时间变化。"""

        assert DEFAULT_SEED == 20240101

        first = generate_demo_ohlcv(
            periods=100,
            mode="trend",
            seed=DEFAULT_SEED,
            start_date="2020-01-01",
            base_price=10.0,
        )

        second = generate_demo_ohlcv(
            periods=100,
            mode="trend",
            seed=DEFAULT_SEED,
            start_date="2020-01-01",
            base_price=10.0,
        )

        assert first.equals(second)

    def test_generation_does_not_pollute_global_random(self):
        """
        生成过程不得污染全局随机状态。

        使用 default_rng 而非 np.random 全局函数，
        因此调用前后 np.random 的状态不变。
        """

        np.random.seed(999)

        before = np.random.get_state()[1].copy()

        make_frame()

        after = np.random.get_state()[1].copy()

        assert np.array_equal(before, after)

    def test_backtest_result_reproducible(self):
        """相同参数两次回测结果完全一致。"""

        def run_once() -> dict:
            return demo_backtest(
                DemoBacktestRequest(
                    mode="trend",
                    seed=777,
                    periods=400,
                    strategy_type="kdj",
                )
            )

        first = run_once()
        second = run_once()

        assert first.final_capital == second.final_capital
        assert first.total_return == second.total_return
        assert first.total_trades == second.total_trades


# ==========================================================
# 2. OHLCV 合法性
# ==========================================================

class TestOHLCVConstraints:
    """
    生成数据必须满足全部 OHLCV 约束。
    """

    @pytest.mark.parametrize(
        "mode",
        MARKET_MODES,
        ids=list(MARKET_MODES),
    )
    def test_price_relationship(self, mode):
        """high >= max(open,close)，low <= min(open,close)。"""

        frame = make_frame(mode=mode)

        body_high = frame[["open", "close"]].max(axis=1)
        body_low = frame[["open", "close"]].min(axis=1)

        assert (frame["high"] >= body_high - 1e-9).all()
        assert (frame["low"] <= body_low + 1e-9).all()

    @pytest.mark.parametrize(
        "mode",
        MARKET_MODES,
        ids=list(MARKET_MODES),
    )
    def test_prices_positive_and_finite(self, mode):
        """所有价格为正且有限。"""

        frame = make_frame(mode=mode)

        for column in ("open", "high", "low", "close"):
            values = frame[column].to_numpy(dtype=np.float64)

            assert np.isfinite(values).all()
            assert (values > 0).all()

    @pytest.mark.parametrize(
        "mode",
        MARKET_MODES,
        ids=list(MARKET_MODES),
    )
    def test_volume_non_negative(self, mode):
        """成交量非负。"""

        frame = make_frame(mode=mode)

        assert (frame["vol"] >= 0).all()

    @pytest.mark.parametrize(
        "mode",
        MARKET_MODES,
        ids=list(MARKET_MODES),
    )
    def test_dates_strictly_increasing_and_unique(self, mode):
        """日期严格递增且无重复。"""

        frame = make_frame(mode=mode)

        dates = frame["trade_date"]

        assert dates.duplicated().sum() == 0
        assert dates.is_monotonic_increasing

    @pytest.mark.parametrize(
        "mode",
        MARKET_MODES,
        ids=list(MARKET_MODES),
    )
    def test_row_count_matches_request(self, mode):
        """行数等于请求的样本数。"""

        for periods in (50, 100, 300, 800):
            frame = make_frame(periods=periods, mode=mode)
            assert len(frame) == periods

    @pytest.mark.parametrize(
        "mode",
        MARKET_MODES,
        ids=list(MARKET_MODES),
    )
    def test_data_source_column_marked(self, mode):
        """逐行标记数据来源为 synthetic。"""

        frame = make_frame(mode=mode)

        assert (frame["data_source"] == DATA_SOURCE).all()

    def test_three_modes_produce_distinct_shapes(self):
        """三种模式的统计特征应明显不同。"""

        trend = make_frame(periods=600, mode="trend")["close"]
        swing = make_frame(periods=600, mode="range")["close"]
        noisy = make_frame(periods=600, mode="high_vol")["close"]

        # 高波动模式的日波动显著大于其他两者
        assert noisy.pct_change().std() > trend.pct_change().std()
        assert noisy.pct_change().std() > swing.pct_change().std()

        # 震荡模式的收盘价围绕起始价，不会单边发散
        swing_ratio = swing.max() / swing.min()
        trend_ratio = trend.max() / trend.min()

        assert swing_ratio < trend_ratio

    def test_validate_ohlcv_rejects_broken_data(self):
        """
        自校验必须能拦住不合规数据。

        故意破坏 high/low 后应抛 ValueError。
        """

        frame = make_frame()

        broken_high = frame.copy()
        broken_high.loc[broken_high.index[5], "high"] = 0.01
        broken_high.loc[broken_high.index[5], "low"] = 0.01

        with pytest.raises(ValueError, match="high"):
            validate_ohlcv(broken_high)

        broken_low = frame.copy()
        broken_low.loc[broken_low.index[7], "low"] = 999.0

        with pytest.raises(ValueError, match="low"):
            validate_ohlcv(broken_low)

        negative = frame.copy()
        negative.loc[negative.index[3], "close"] = -1.0

        with pytest.raises(ValueError, match="close"):
            validate_ohlcv(negative)

    def test_validate_ohlcv_rejects_duplicate_dates(self):
        """重复日期必须被自校验拦下。"""

        frame = make_frame()

        duplicated = pd.concat(
            [frame, frame.tail(1)],
            ignore_index=True,
        )

        with pytest.raises(ValueError, match="重复"):
            validate_ohlcv(duplicated)


# ==========================================================
# 3. 输入校验
# ==========================================================

class TestDemoRequestValidation:
    """
    请求模型的输入校验。
    """

    @pytest.mark.parametrize(
        "mode",
        ["invalid", "TREND_", "ma_cross", "", "  ", "随机"],
        ids=[
            "invalid_word",
            "wrong_suffix",
            "strategy_name",
            "empty",
            "spaces",
            "chinese",
        ],
    )
    def test_invalid_mode_rejected(self, mode):
        """非法行情模式必须被拒绝。"""

        with pytest.raises(ValueError, match="不支持的行情模式"):
            generate_demo_ohlcv(
                periods=100,
                mode=mode,
                seed=1,
                start_date="2020-01-01",
                base_price=10.0,
            )

    def test_invalid_mode_via_api_returns_400(self):
        """非法模式经 API 返回 400 INVALID_STRATEGY_TYPE 类错误。"""

        request = DemoBacktestRequest(
            mode="nonexistent_mode",
            periods=100,
        )

        with pytest.raises(HTTPException) as excinfo:
            demo_backtest(request)

        assert excinfo.value.status_code == 400

    @pytest.mark.parametrize(
        "mode",
        MARKET_MODES,
        ids=list(MARKET_MODES),
    )
    def test_mode_case_insensitive(self, mode):
        """模式名大小写不敏感。"""

        upper = generate_demo_ohlcv(
            100, mode.upper(), 1, "2020-01-01", 10.0
        )

        lower = generate_demo_ohlcv(
            100, mode, 1, "2020-01-01", 10.0
        )

        assert upper.equals(lower)

    @pytest.mark.parametrize(
        "periods",
        [0, 1, -5],
        ids=["zero", "one", "negative"],
    )
    def test_too_few_periods_rejected(self, periods):
        """样本数过少必须被拒绝。"""

        with pytest.raises(ValueError, match="periods"):
            generate_demo_ohlcv(
                periods=periods,
                mode="trend",
                seed=1,
                start_date="2020-01-01",
                base_price=10.0,
            )

    def test_too_many_periods_rejected(self):
        """样本数超上限必须被拒绝。"""

        with pytest.raises(ValueError, match="periods"):
            generate_demo_ohlcv(
                periods=99999,
                mode="trend",
                seed=1,
                start_date="2020-01-01",
                base_price=10.0,
            )

    def test_periods_upper_bound_in_request(self):
        """请求模型的 periods 上限与生成器一致。"""

        assert DemoBacktestRequest().periods <= 4000

        with pytest.raises(ValidationError):
            DemoBacktestRequest(periods=99999)

        with pytest.raises(ValidationError):
            DemoBacktestRequest(periods=0)

    @pytest.mark.parametrize(
        "seed",
        [-1, 2**32, 2**40],
        ids=["negative", "boundary_over", "far_over"],
    )
    def test_invalid_seed_rejected(self, seed):
        """非法种子必须被拒绝。"""

        with pytest.raises(ValueError, match="seed"):
            generate_demo_ohlcv(
                periods=100,
                mode="trend",
                seed=seed,
                start_date="2020-01-01",
                base_price=10.0,
            )

    @pytest.mark.parametrize(
        "seed",
        [True, False],
        ids=["true", "false"],
    )
    def test_bool_seed_rejected(self, seed):
        """
        布尔种子必须被拒绝。

        bool 是 int 子类，若不单独拦截，
        True 会被当成种子 1 静默通过。
        """

        with pytest.raises(ValueError, match="布尔"):
            generate_demo_ohlcv(
                periods=100,
                mode="trend",
                seed=seed,
                start_date="2020-01-01",
                base_price=10.0,
            )

        with pytest.raises(ValidationError):
            DemoBacktestRequest(seed=seed)

    @pytest.mark.parametrize(
        "base_price",
        [0, -1, -0.5],
        ids=["zero", "negative", "small_negative"],
    )
    def test_invalid_base_price_rejected(self, base_price):
        """起始价格必须为正。"""

        with pytest.raises(ValueError, match="base_price"):
            generate_demo_ohlcv(
                periods=100,
                mode="trend",
                seed=1,
                start_date="2020-01-01",
                base_price=base_price,
            )

        with pytest.raises(ValidationError):
            DemoBacktestRequest(base_price=base_price)

    @pytest.mark.parametrize(
        "base_price",
        [1e-9, 1e-7, 0.001, 0.5, 1.0, 5.0, 9.999],
        ids=[
            "1e_9",
            "1e_7",
            "one_thousandth",
            "half",
            "one",
            "five",
            "just_below_min",
        ],
    )
    def test_base_price_below_min_rejected(
        self, base_price
    ):
        """
        低于下界的正数起始价格必须被请求模型拒绝。

        根因（P1-2）：
            生成器在 validate_ohlcv 之前会把价格 round(4)
            （engine/demo_data.py:499），
            小于 5e-5 的价格会被舍入为 0.0，
            从而违反「价格必须为正」。

            但真正决定下界的是**长期路径累积**：
            high_vol + 4000 周期下，
            base_price=0.01 实测失败率 34%，
            因此下界必须远高于 5e-5。

        错误必须明确归因到 base_price。
        """

        with pytest.raises(ValidationError) as excinfo:
            DemoBacktestRequest(base_price=base_price)

        error = excinfo.value.errors()[0]

        assert error["loc"] == ("base_price",)
        assert error["type"] == "greater_than_equal"

    @pytest.mark.parametrize(
        "base_price",
        [10.0, 50.0, 100.0, 1000.0],
        ids=[
            "at_min",
            "five_times_min",
            "ten_times_min",
            "thousand",
        ],
    )
    def test_base_price_boundaries_accepted(
        self, base_price
    ):
        """
        下界及以上的价格必须被接受。

        最小值 10.0 是修复后的合法下界，
        必须能够完整跑通回测。
        """

        request = DemoBacktestRequest(
            base_price=base_price,
            periods=200,
            mode="trend",
            seed=42,
        )

        assert request.base_price == base_price

        result = demo_backtest(request)

        assert result.final_capital > 0
        assert len(result.equity_curve) > 0

    def test_base_price_min_is_ten(self):
        """
        下界常量必须是 10.0。

        修订记录：
            该常量曾被设为 0.01，
            当时的理由是「0.01 跌 99% 仍有 1e-4 > 5e-5 阈值」。
            该推理只覆盖单点下跌，未考虑长期路径累积，
            实测失败率高达 34%（见下一个回归测试）。

        本用例只锁定常量值本身；
        其合理性由test_base_price_high_vol_long_period 验证。
        """

        from api.demo import MIN_BASE_PRICE

        assert MIN_BASE_PRICE == 10.0

        # 默认值必须仍在下界之上，
        # 否则默认请求会被自己的约束拒绝
        from engine.demo_data import DEFAULT_BASE_PRICE

        assert DEFAULT_BASE_PRICE >= MIN_BASE_PRICE

    @pytest.mark.parametrize(
        "seed",
        [1, 7, 42, 99, 20240101],
        ids=["s1", "s7", "s42", "s99", "default"],
    )
    def test_base_price_high_vol_long_period(self, seed):
        """
        回归测试：下界 + 最恶劣组合必须能跑通。

        组合：
            base_price = MIN_BASE_PRICE（10.0，恰好在下界）
            mode       = high_vol（sigma=0.045，最大波动）
            periods    = 4000（上限，最大路径长度）

        这正是旧下限 0.01 会失败 34% 的场景。
        本用例验证真实生成+回测链路，
        而非只验证常量或简单乘法。

        注意：使用固定 seed 保证可复现。
        零失败是抽样结论，不是数学保证——
        几何布朗运动的价格路径无界。
        """

        from api.demo import MIN_BASE_PRICE

        request = DemoBacktestRequest(
            base_price=MIN_BASE_PRICE,
            mode="high_vol",
            periods=4000,
            seed=seed,
            start_date="2020-01-01",
        )

        result = demo_backtest(request)

        # 完整跑通
        assert result.final_capital > 0
        assert len(result.equity_curve) > 4000 - 100
        assert result.data_source == "synthetic"

        # 权益恒等式在长序列下仍成立
        for point in result.equity_curve[:50]:
            assert point.equity == pytest.approx(
                point.cash + point.position_value,
                rel=1e-6,
                abs=1e-3,
            )

    @pytest.mark.parametrize(
        "mode",
        ["trend", "range"],
        ids=["trend", "range"],
    )
    def test_base_price_min_all_modes_long_period(self, mode):
        """
        下界在另外两种模式下也必须能跑通最长周期。

        与 high_vol 用例互补，
        确保下界不是只对单一模式成立。
        """

        from api.demo import MIN_BASE_PRICE

        result = demo_backtest(
            DemoBacktestRequest(
                base_price=MIN_BASE_PRICE,
                mode=mode,
                periods=4000,
                seed=42,
                start_date="2020-01-01",
            )
        )

        assert result.final_capital > 0
        assert len(result.equity_curve) > 3900

    def test_old_base_price_min_would_fail(self):
        """
        记录旧下限为何不可接受。

        旧下界0.01 在 high_vol + 4000 周期下
        实测约 34% 的 seed 会失败。

        本用例不依赖随机性，
        而是用一个已实测失败的固定 seed 来证明：
        0.01 确实无法支撑该场景。
        """

        from engine.demo_data import generate_demo_ohlcv

        # 该 seed 在 base_price=0.01 时实测失败
        # （若将来生成器改动导致不再失败，本用例会提示复核）
        failing_seed = 1

        with pytest.raises(ValueError, match="必须为正数"):
            generate_demo_ohlcv(
                periods=4000,
                mode="high_vol",
                seed=failing_seed,
                start_date="2020-01-01",
                base_price=0.01,
            )

        # 同一 seed 在新下界下必须成功
        from api.demo import MIN_BASE_PRICE

        frame = generate_demo_ohlcv(
            periods=4000,
            mode="high_vol",
            seed=failing_seed,
            start_date="2020-01-01",
            base_price=MIN_BASE_PRICE,
        )

        assert (frame["low"] > 0).all()

    def test_base_price_error_field_is_correct(self):
        """
        base_price 相关错误必须归因到 base_price，不能是 mode。

        这是 P1-2 的核心：
            修复前所有生成错误都被标成 field="mode"，
            用户会去改一个无关字段。

        验证方式：
            直接调用生成器触发下溢场景（绕过请求模型，
            因为请求模型现在已在更早阶段拦下）。
        """

        from fastapi import HTTPException

        from engine.demo_data import generate_demo_ohlcv

        # 确认生成器在该值下确实会失败
        with pytest.raises(ValueError):
            generate_demo_ohlcv(
                periods=100,
                mode="trend",
                seed=1,
                start_date="2020-01-01",
                base_price=1e-9,
            )

        # 请求模型层应给出 field=base_price 的 422
        with pytest.raises(ValidationError) as excinfo:
            DemoBacktestRequest(base_price=1e-9)

        assert excinfo.value.errors()[0]["loc"] == (
            "base_price",
        )

        # 正常请求不应触发 mode 归因错误
        result = demo_backtest(
            DemoBacktestRequest(
                periods=200, mode="trend", seed=42
            )
        )

        assert result.mode == "trend"
        assert result.data_source == "synthetic"

    def test_invalid_mode_error_field(self):
        """非法的 mode 仍应归因到 mode。"""

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as excinfo:
            demo_backtest(
                DemoBacktestRequest(
                    periods=200,
                    mode="not_a_mode",
                )
            )

        assert excinfo.value.status_code == 400
        assert excinfo.value.detail["field"] == "mode"

    @pytest.mark.parametrize(
        "start_date",
        [
            "not-a-date",
            "2020/01/01",
            "20-01-01",
            "",
        ],
        ids=[
            "garbage",
            "slashes",
            "two_digit_year",
            "empty",
        ],
    )
    def test_invalid_start_date_rejected(self, start_date):
        """
        非法日期必须被拒绝。

        说明：
            "20200101" 这种紧凑格式在 Python 3.11+
            的 date.fromisoformat 中是**合法**的，
            会被正确解析，因此不列入本用例。

        实际影响：
            传入 "20200101" 时，生成器会把它当作 2020-01-01 使用，
            行为正常，不会报错，也不会产生错误数据。
        """

        with pytest.raises(ValidationError):
            DemoBacktestRequest(start_date=start_date)

    @pytest.mark.parametrize(
        "start_date",
        [
            "9999-12-01",
            "9999-11-01",
            "9999-01-01",
            "9998-01-01",
            "9990-01-01",
        ],
        ids=[
            "y9999_m12",
            "y9999_m11",
            "y9999_m01",
            "y9998",
            "y9990",
        ],
    )
    def test_extreme_late_start_date_rejected(
        self, start_date
    ):
        """
        过于靠后的起始日期必须被拒绝（P1-1）。

        根因：
            pd.bdate_range 不校验结果日期是否超出
            datetime 支持范围，因此 start_date='9999-12-01'
            能通过生成阶段，却在后续裁剪时因日期溢出失败，
            且该失败位于异常处理之外，会变成 500。

        修复后：请求校验阶段直接拒绝，
        错误明确归因于 start_date。
        """

        with pytest.raises(ValidationError) as excinfo:
            DemoBacktestRequest(start_date=start_date)

        error = excinfo.value.errors()[0]

        assert error["loc"] == ("start_date",)

    @pytest.mark.parametrize(
        "start_date",
        ["0001-01-01", "1970-01-01", "2000-01-01"],
        ids=["y1", "y1970", "y2000"],
    )
    def test_early_start_date_still_accepted(
        self, start_date
    ):
        """
        较早的起始日期不受新约束影响，必须仍然可用。

        防止修复过头，把合法输入一并拒绝。

        注意：
            起始日可能被顺延。
            例如 2000-01-01 是周六，
            pd.bdate_range 会顺延到下一个工作日 2000-01-03。
            因此只断言能成功生成且日期不早于请求值。
        """

        request = DemoBacktestRequest(
            start_date=start_date,
            periods=200,
            mode="trend",
            seed=42,
        )

        result = demo_backtest(request)

        # 起始日可能被顺延到下一个工作日，但不会提前
        assert result.start_date >= start_date

        assert len(result.equity_curve) > 0

    def test_start_date_on_weekend_is_normalized(self):
        """
        落在周末的起始日会被顺延到下一个工作日。

        这是 pd.bdate_range 的既有行为，
        本用例把它固定下来，避免将来被误判为 bug。
        """

        import pandas as pd

        # 2000-01-01 是周六
        assert pd.Timestamp("2000-01-01").dayofweek == 5

        result = demo_backtest(
            DemoBacktestRequest(
                start_date="2000-01-01",
                periods=200,
                mode="trend",
                seed=42,
            )
        )

        assert result.start_date == "2000-01-03"

    def test_no_unhandled_exception_for_extreme_date(self):
        """
        极端日期不得抛出未捕获异常。

        修复前这里会抛 DateParseError / OverflowError，
        穿透全部 try/except，最终变成 500。

        本用例直接断言「只会抛这两种受控异常」，
        若将来校验被移除，测试会立即失败。
        """

        from fastapi import HTTPException

        for start_date in (
            "9999-12-01",
            "9999-06-01",
            "9998-01-01",
        ):
            try:
                demo_backtest(
                    DemoBacktestRequest(
                        periods=300, start_date=start_date
                    )
                )
            except HTTPException:
                # 400，受控
                pass
            except ValidationError:
                # 422，受控
                pass
            except Exception as exc:  # pragma: no cover
                pytest.fail(
                    f"start_date={start_date} 抛出未受控异常："
                    f"{type(exc).__name__}: {exc}"
                )

    def test_benchmark_alignment_never_crashes(self):
        """
        基准对齐在任何合法请求下都不得抛未捕获异常。

        build_buy_and_hold 的调用位于回测之后，
        修复前这一段没有异常保护。
        """

        from fastapi import HTTPException

        cases = [
            ("trend", 600, "2020-01-01", "kdj"),
            ("high_vol", 400, "0001-01-01", "kdj"),
            ("range", 1000, "1990-06-15", "ma_cross"),
        ]

        for mode, periods, start, strategy in cases:
            try:
                result = demo_backtest(
                    DemoBacktestRequest(
                        mode=mode,
                        periods=periods,
                        start_date=start,
                        strategy_type=strategy,
                        seed=42,
                    )
                )

                assert len(result.benchmark) == len(
                    result.equity_curve
                )

            except HTTPException:
                # 400，受控（例如数据不足）
                pass
            except Exception as exc:  # pragma: no cover
                pytest.fail(
                    f"case={mode}/{start} 抛出未受控异常："
                    f"{type(exc).__name__}: {exc}"
                )

    def test_compact_date_is_accepted(self):
        """
        紧凑日期格式被正确解析，不产生错误数据。

        实测：start_date="20200101" 被 date.fromisoformat
        正确解析为 2020-01-01，并作为工作日起点使用。
        """

        request = DemoBacktestRequest(
            start_date="20200101", periods=100
        )

        assert request.start_date == "20200101"

        result = demo_backtest(request)

        assert result.start_date == "2020-01-01"

    @pytest.mark.parametrize(
        "capital",
        [0, -1000, 0.5, 0.999999],
        ids=["zero", "negative", "below_min", "just_below_min"],
    )
    def test_invalid_initial_capital_rejected(self, capital):
        """
        初始资金必须落在合法区间内。

        区间与真实行情链路一致：[1.0, 1e12]。
        """

        with pytest.raises(ValidationError) as excinfo:
            DemoBacktestRequest(initial_capital=capital)

        assert excinfo.value.errors()[0]["loc"] == (
            "initial_capital",
        )

    @pytest.mark.parametrize(
        "capital",
        [1.0, 100000.0, 1e12],
        ids=["at_min", "default", "at_max"],
    )
    def test_initial_capital_boundaries_accepted(
        self, capital
    ):
        """
        边界值必须被接受且不引发异常。

        包含浮点与序列化的往返验证：
            1e12 是上界，必须能通过请求模型并完成回测。
        """

        request = DemoBacktestRequest(
            initial_capital=capital,
            periods=200,
            mode="trend",
            seed=42,
        )

        assert request.initial_capital == capital

        result = demo_backtest(request)

        assert result.initial_capital == capital
        assert result.final_capital > 0

        # 必须是可严格序列化的有限数值
        import json

        json.dumps(
            result.model_dump(),
            allow_nan=False,
            ensure_ascii=False,
        )

    @pytest.mark.parametrize(
        "capital",
        [1e12 + 1, 1e13, 1e18, 1e300],
        ids=[
            "just_above_max",
            "1e13",
            "1e18",
            "1e300",
        ],
    )
    def test_initial_capital_above_max_rejected(
        self, capital
    ):
        """
        超过上界的资金必须被拒绝。

        与真实链路口径统一，
        避免同一概念在两条链路有不同的接受规则。
        """

        with pytest.raises(ValidationError) as excinfo:
            DemoBacktestRequest(initial_capital=capital)

        error = excinfo.value.errors()[0]

        assert error["loc"] == ("initial_capital",)
        assert error["type"] == "less_than_equal"

    @pytest.mark.skip(reason='发布副本仅含 Demo，不含生产链路模块（api.strategy / engine.data_loader）；本用例是主仓内的跨模块对照测试，合并回主仓后应恢复。')
    def test_initial_capital_matches_real_chain_limits(self):
        """
        Demo 的资金约束必须与真实链路完全一致。

        直接比对两边的常量，
        防止将来单边修改导致口径漂移。
        """

        from api.demo import (
            MAX_INITIAL_CAPITAL as DEMO_MAX,
        )
        from api.demo import (
            MIN_INITIAL_CAPITAL as DEMO_MIN,
        )
        from api.strategy import (
            MAX_INITIAL_CAPITAL as REAL_MAX,
        )
        from api.strategy import (
            MIN_INITIAL_CAPITAL as REAL_MIN,
        )

        assert DEMO_MIN == REAL_MIN
        assert DEMO_MAX == REAL_MAX

    @pytest.mark.parametrize(
        "field",
        ["unknown_field", "symbol", "start_data", "adjust"],
        ids=["unknown", "symbol", "typo", "adjust"],
    )
    def test_unknown_fields_rejected(self, field):
        """
        未知字段必须被拒绝。

        特别重要：symbol 是真实行情链路的必需字段，
        Demo 请求**不允许**携带它——
        这在模型层保证了 Demo 无法指向真实行情。
        """

        with pytest.raises(ValidationError):
            DemoBacktestRequest(**{field: "000001.SZ"})

    def test_unsupported_strategy_rejected(self):
        """不支持的策略类型走既有白名单，返回 400。"""

        request = DemoBacktestRequest(
            strategy_type="not_a_strategy",
            periods=200,
        )

        with pytest.raises(HTTPException) as excinfo:
            demo_backtest(request)

        assert excinfo.value.status_code == 400

    def test_invalid_strategy_parameter_rejected(self):
        """非法策略参数走既有白名单，返回 400。"""

        request = DemoBacktestRequest(
            strategy_type="kdj",
            parameters={"rsv_peroid": 9},
            periods=200,
        )

        with pytest.raises(HTTPException) as excinfo:
            demo_backtest(request)

        assert excinfo.value.status_code == 400

    def test_empty_frame_rejected_by_benchmark(self):
        """空数据无法构造基准。"""

        with pytest.raises(ValueError, match="为空"):
            build_buy_and_hold(pd.DataFrame())

        with pytest.raises(ValueError, match="为空"):
            validate_ohlcv(pd.DataFrame())


# ==========================================================
# 4. 引擎兼容性：三模式 x 两策略
# ==========================================================

class TestEngineCompatibility:
    """
    两种策略在三种模式上均须完成回测。

    这是阶段 B 的核心验收项：
    证明合成数据能真正跑通现有引擎，
    而不只是生成出一个看起来合理的数据表。
    """

    @pytest.mark.parametrize(
        "mode",
        MARKET_MODES,
        ids=list(MARKET_MODES),
    )
    @pytest.mark.parametrize(
        "strategy_type",
        ["kdj", "ma_cross"],
        ids=["kdj", "ma_cross"],
    )
    def test_strategy_runs_on_all_modes(
        self, mode, strategy_type
    ):
        """KDJ 与 MA 在三种模式上均能完成回测。"""

        frame = make_frame(periods=600, mode=mode)

        parameters = normalize_strategy_parameters(
            strategy_type=strategy_type,
            parameters={},
        )

        strategy = create_strategy(
            strategy_type=strategy_type,
            parameters=parameters,
        )

        result = run_backtest(
            df=frame,
            strategy=strategy,
            initial_capital=100000.0,
        )

        assert result["final_capital"] > 0
        assert len(result["equity_curve"]) > 0
        assert isinstance(result["trades"], list)

    @pytest.mark.parametrize(
        "mode",
        MARKET_MODES,
        ids=list(MARKET_MODES),
    )
    def test_api_returns_valid_response(self, mode):
        """API 在三种模式上均返回完整响应。"""

        result = demo_backtest(
            DemoBacktestRequest(
                mode=mode,
                seed=42,
                periods=600,
                strategy_type="kdj",
            )
        )

        assert result.data_source == "synthetic"
        assert result.is_demo is True
        assert result.disclaimer == DISCLAIMER
        assert result.mode == mode
        assert len(result.equity_curve) > 0
        assert len(result.benchmark) > 0

    def test_insufficient_periods_returns_insufficient_data(self):
        """
        样本数不足 MA60 预热时返回 INSUFFICIENT_DATA。

        这是既有引擎的既有行为，
        Demo 链路沿用同一套判定，不修改引擎。
        """

        request = DemoBacktestRequest(
            mode="trend",
            seed=1,
            periods=30,
            strategy_type="ma_cross",
        )

        with pytest.raises(HTTPException) as excinfo:
            demo_backtest(request)

        assert excinfo.value.status_code == 400

        detail = excinfo.value.detail

        assert detail["code"] == "INSUFFICIENT_DATA"
        assert detail["field"] == "periods"

    def test_ma_strategy_trade_count_note(self):
        """
        记录 ma_cross 的已知局限。

        ma_strategy.py 的注释已声明：
            当前 BUY/SELL 是状态判断而非金叉事件。

        因此在同一持有区间内，
        ma_cross 的成交笔数显著少于 kdj。
        本用例把这个事实固定下来，
        防止后续误以为是合成数据的问题。
        """

        frame = make_frame(periods=600, mode="trend")

        counts = {}

        for strategy_type in ("kdj", "ma_cross"):
            parameters = normalize_strategy_parameters(
                strategy_type, {}
            )

            strategy = create_strategy(
                strategy_type, parameters
            )

            result = run_backtest(
                df=frame,
                strategy=strategy,
                initial_capital=100000.0,
            )

            counts[strategy_type] = result["total_trades"]

        assert counts["kdj"] > counts["ma_cross"]

    def test_equity_identity_holds(self):
        """
        权益恒等式 equity == cash + position_value。

        与真实行情链路共用同一个引擎，
        因此该恒等式必然成立。
        """

        result = demo_backtest(
            DemoBacktestRequest(
                mode="high_vol",
                seed=42,
                periods=400,
                strategy_type="kdj",
            )
        )

        for point in result.equity_curve:
            assert point.equity == pytest.approx(
                point.cash + point.position_value,
                rel=1e-9,
                abs=1e-6,
            )


# ==========================================================
# 5. 合成数据标记
# ==========================================================

class TestSyntheticMarkers:
    """
    响应必须携带全部合成数据标记。
    """

    def test_markers_present(self):
        """data_source / is_demo / disclaimer 三者齐备。"""

        result = demo_backtest(
            DemoBacktestRequest(
                mode="trend", periods=300, seed=42
            )
        )

        assert result.data_source == "synthetic"
        assert result.is_demo is True
        assert result.disclaimer == DISCLAIMER

    def test_disclaimer_wording(self):
        """免责声明文案必须包含关键提示。"""

        assert "模拟数据" in DISCLAIMER
        assert "非真实行情" in DISCLAIMER

    def test_symbol_is_synthetic_marker(self):
        """symbol 固定为 DEMO.SYNTHETIC，不冒充真实代码。"""

        result = demo_backtest(
            DemoBacktestRequest(
                mode="trend", periods=300, seed=42
            )
        )

        assert result.symbol == "DEMO.SYNTHETIC"

    def test_modes_endpoint_markers(self):
        """模式列表接口同样携带标记。"""

        response = demo_modes()

        assert response.data_source == "synthetic"
        assert response.is_demo is True
        assert response.disclaimer == DISCLAIMER

        assert len(response.modes) == 3

        for item in response.modes:
            assert item["data_source"] == "synthetic"
            assert item["is_demo"] is True
            assert item["disclaimer"] == DISCLAIMER

    def test_benchmark_and_excess_return(self):
        """
        基准收益、超额收益口径正确。

        期望值独立构造：
            用相同 mode / seed / periods / base_price
            重新生成行情，取首末收盘价计算。
        不依赖被测结果自身的任何字段。
        """

        mode = "trend"
        periods = 400
        seed = 42

        # 独立重建同一条行情（同seed 必然完全一致）
        frame = generate_demo_ohlcv(
            periods=periods,
            mode=mode,
            seed=seed,
            start_date="2020-01-01",
            base_price=10.0,
        )

        expected_benchmark_return = (
            frame["close"].iloc[-1] / frame["close"].iloc[0] - 1
        )

        result = demo_backtest(
            DemoBacktestRequest(
                mode=mode,
                periods=periods,
                seed=seed,
                start_date="2020-01-01",
                base_price=10.0,
            )
        )

        assert result.benchmark_return == pytest.approx(
            expected_benchmark_return
        )

        # 独立重建同区间基准收益：
        #   起点 = 权益曲线首日对应的收盘价，
        #   终点 = 完整行情末日收盘价
        #   （引擎 dropna 只丢弃开头，故末日未被丢弃）
        equity_first_date = str(
            result.equity_curve[0].date
        )[:10]

        same_range_first_close = float(
            frame.loc[
                frame["trade_date"]
                .astype(str)
                .str[:10]
                == equity_first_date,
                "close",
            ].iloc[0]
        )

        expected_curve_return = (
            frame["close"].iloc[-1] / same_range_first_close - 1
        )

        assert result.benchmark_curve_return == pytest.approx(
            expected_curve_return
        )

        expected_excess = (
            result.total_return - expected_curve_return
        )

        assert result.excess_return == pytest.approx(expected_excess)

    def test_excess_return_uses_same_range_benchmark(self):
        """
        超额收益必须使用同区间基准。

        这是阶段 D2.3 的核心回归点。

        本用例不依赖期望值推导，而用反证法
        确保实现没有走错分支：
            若 excess_return 误用了完整区间的
            benchmark_return，则它必然等于
            total_return - benchmark_return；
            而只要预热期真的丢弃了数据，
            这两个值就不相等。
        """

        mode = "trend"
        periods = 400
        seed = 42

        result = demo_backtest(
            DemoBacktestRequest(
                mode=mode,
                periods=periods,
                seed=seed,
                start_date="2020-01-01",
                base_price=10.0,
            )
        )

        # 前置条件：权益曲线确实短于完整行情，
        # 即预热期确实丢弃了数据。
        # 若该前提不成立，本用例需重新评估。
        assert len(result.equity_curve) < periods
        assert result.equity_start_date != result.start_date

        # 同区间基准与完整区间基准必须不同。
        assert result.benchmark_curve_return != pytest.approx(
            result.benchmark_return
        )

        # 超额收益 = total_return - 同区间基准
        assert result.excess_return == pytest.approx(
            result.total_return - result.benchmark_curve_return
        )

        # 反证：误用完整区间基准时上式不可能成立。
        assert result.excess_return != pytest.approx(
            result.total_return - result.benchmark_return
        )

    def test_benchmark_return_uses_full_range(
        self,
    ):
        """
        基准收益必须按完整模拟区间计算。

        关键回归点：
            回测引擎会丢弃策略预热期（KDJ 前 8 行），
            若基准收益也用裁剪后的首末价，
            会把「预热期内涨跌幅」误当作「全区间涨跌幅」。

        本用例把两者分开断言，
        确保 benchmark_return 始终等于完整区间口径。
        """

        mode = "trend"
        periods = 400
        seed = 42

        frame = generate_demo_ohlcv(
            periods=periods,
            mode=mode,
            seed=seed,
            start_date="2020-01-01",
            base_price=10.0,
        )

        full_range_return = (
            frame["close"].iloc[-1] / frame["close"].iloc[0] - 1
        )

        result = demo_backtest(
            DemoBacktestRequest(
                mode=mode,
                periods=periods,
                seed=seed,
                start_date="2020-01-01",
                base_price=10.0,
            )
        )

        assert result.benchmark_return == pytest.approx(
            full_range_return
        )

        # 权益曲线因预热期而变短，
        # 裁剪区间的收益必然与完整区间不同。
        # 若两者恰好相等，说明预热期没有丢弃数据，
        # 该前提变更时本用例需要重新评估。
        if len(result.equity_curve) < periods:
            assert result.benchmark_curve_return != pytest.approx(
                result.benchmark_return
            )

    def test_benchmark_curve_aligned_with_equity(self):
        """
        基准曲线与权益曲线逐日对齐。

        这是 P1 修复的核心契约：
            两条曲线必须有完全相同的日期序列，
            否则前端按日期画对比图会错位。
        """

        mode = "trend"
        periods = 400
        seed = 42

        result = demo_backtest(
            DemoBacktestRequest(
                mode=mode,
                periods=periods,
                seed=seed,
                start_date="2020-01-01",
                base_price=10.0,
            )
        )

        # equity_curve 的元素是 EquityPoint 模型实例，
        # 必须用属性访问；benchmark 是 list[dict]，用下标访问。
        equity_dates = [
            str(point.date)[:10]
            for point in result.equity_curve
        ]

        benchmark_dates = [
            str(point["date"])[:10]
            for point in result.benchmark
        ]

        # 逐日完全一致：长度相同且每个日期都相同
        assert len(benchmark_dates) == len(equity_dates)
        assert benchmark_dates == equity_dates

        # 首末日期必须与响应中的区间一致
        assert result.benchmark[0]["date"] == (
            result.equity_curve[0].date
        )

        assert result.benchmark[-1]["date"] == (
            result.equity_curve[-1].date
        )

    def test_benchmark_curve_dates_are_existing_trading_days(
        self,
    ):
        """
        基准曲线的每个日期都必须来自真实生成的交易日。

        防止裁剪逻辑凭空造出不属于行情的日期。
        """

        frame = generate_demo_ohlcv(
            periods=300,
            mode="trend",
            seed=42,
            start_date="2020-01-01",
            base_price=10.0,
        )

        valid_dates = {
            str(d)[:10] for d in frame["trade_date"]
        }

        result = demo_backtest(
            DemoBacktestRequest(
                mode="trend", periods=300, seed=42
            )
        )

        for point in result.benchmark:
            assert str(point["date"])[:10] in valid_dates

    def test_benchmark_curve_trimmed_for_ma_strategy(self):
        """
        MA 策略预热更长，基准也必须相应裁剪。

        MA60 会丢弃更多数据，
        若基准不跟随裁剪，两条曲线长度差距会更大。
        """

        result = demo_backtest(
            DemoBacktestRequest(
                mode="trend",
                periods=600,
                seed=42,
                strategy_type="ma_cross",
            )
        )

        assert (
            len(result.benchmark) == len(result.equity_curve)
        )

        assert result.benchmark[0]["date"] == (
            result.equity_curve[0].date
        )


# ==========================================================
# 5b. 区间语义：请求区间 vs 权益曲线实际区间
# ==========================================================

class TestEquityRangeFields:
    """
    equity_start_date / equity_end_date 的语义。

    背景：
        start_date / end_date 表示【请求的模拟区间】，
        equity_start_date / equity_end_date 表示
        【权益曲线的实际区间】。

        两者不同是因为策略指标预热期内无法产生交易，
        引擎会丢弃预热期数据（KDJ 前8 行、MA 前 59 行）。

        新字段必须从实际权益曲线推导，
        不能硬编码，也不能复制原始行情首末日期。
    """

    @pytest.mark.parametrize(
        "strategy_type,expected_warmup",
        [("kdj", True), ("ma_cross", True)],
        ids=["kdj", "ma_cross"],
    )
    def test_equity_range_equals_curve_bounds(
        self, strategy_type, expected_warmup
    ):
        """
        新字段必须等于权益曲线首末点的日期。

        两种策略都要覆盖：
            KDJ 预热 8 行，MA 预热 59 行。
        """

        result = demo_backtest(
            DemoBacktestRequest(
                mode="trend",
                periods=600,
                seed=42,
                strategy_type=strategy_type,
            )
        )

        # EquityPoint 是 Pydantic 模型，用属性访问
        assert result.equity_start_date == (
            result.equity_curve[0].date
        )

        assert result.equity_end_date == (
            result.equity_curve[-1].date
        )

        # 预热确实发生了
        assert expected_warmup

        assert len(result.equity_curve) < result.periods

    def test_kdj_equity_start_differs_from_start_date(self):
        """
        KDJ：预热导致曲线起点晚于请求区间起点。

        这是新增字段存在的意义 ——
        若实现退化为复制 start_date，本断言会失败。
        """

        result = demo_backtest(
            DemoBacktestRequest(
                mode="trend",
                periods=600,
                seed=42,
                strategy_type="kdj",
            )
        )

        assert result.equity_start_date != result.start_date

        # 起点必须落在请求区间之内
        assert result.start_date < result.equity_start_date

        # 且不晚于末日
        assert (
            result.equity_start_date
            <= result.equity_end_date
        )

    def test_ma_equity_start_differs_from_start_date(self):
        """
        MA：预热更长，偏移应明显大于 KDJ。

        MA60 预热 59 行，KDJ 预热 8 行，
        两者的起点偏移量必须不同。
        """

        kdj_result = demo_backtest(
            DemoBacktestRequest(
                mode="trend",
                periods=600,
                seed=42,
                strategy_type="kdj",
            )
        )

        ma_result = demo_backtest(
            DemoBacktestRequest(
                mode="trend",
                periods=600,
                seed=42,
                strategy_type="ma_cross",
            )
        )

        assert ma_result.equity_start_date != (
            ma_result.start_date
        )

        # MA 的起点必须晚于 KDJ 的起点
        assert (
            ma_result.equity_start_date
            > kdj_result.equity_start_date
        )

    def test_equity_end_equals_request_end(self):
        """
        预热只丢开头不丢结尾，因此末日通常与请求一致。

        该断言固定这一事实，
        若将来引擎改为尾部裁剪，本用例会提示。
        """

        result = demo_backtest(
            DemoBacktestRequest(
                mode="trend", periods=600, seed=42
            )
        )

        assert result.equity_end_date == result.end_date

    @pytest.mark.parametrize(
        "mode",
        ["trend", "range", "high_vol"],
        ids=["trend", "range", "high_vol"],
    )
    def test_equity_range_holds_for_all_modes(
        self, mode
    ):
        """三种模式下新字段都必须等于曲线首末日期。"""

        result = demo_backtest(
            DemoBacktestRequest(
                mode=mode, periods=500, seed=42
            )
        )

        assert result.equity_start_date == (
            result.equity_curve[0].date
        )

        assert result.equity_end_date == (
            result.equity_curve[-1].date
        )

    @pytest.mark.parametrize(
        "seed",
        [1, 999, 20240101],
        ids=["seed_1", "seed_999", "default_seed"],
    )
    def test_equity_range_holds_for_seeds(self, seed):
        """不同 seed 下新字段同样成立。"""

        result = demo_backtest(
            DemoBacktestRequest(
                mode="trend", periods=400, seed=seed
            )
        )

        assert result.equity_start_date == (
            result.equity_curve[0].date
        )

        assert result.equity_end_date == (
            result.equity_curve[-1].date
        )

    def test_original_range_fields_unchanged(self):
        """
        既有字段语义未被改动。

        start_date / end_date 仍表示完整生成区间，
        不受新字段影响。
        """

        frame = generate_demo_ohlcv(
            periods=400,
            mode="trend",
            seed=42,
            start_date="2020-01-01",
            base_price=10.0,
        )

        expected_start = str(frame["trade_date"].iloc[0])[:10]
        expected_end = str(frame["trade_date"].iloc[-1])[:10]

        result = demo_backtest(
            DemoBacktestRequest(
                mode="trend",
                periods=400,
                seed=42,
                start_date="2020-01-01",
                base_price=10.0,
            )
        )

        assert result.start_date == expected_start
        assert result.end_date == expected_end

    def test_benchmark_alignment_still_holds(
        self,
    ):
        """
        原有基准对齐契约不受本次改动影响。

        基准曲线与权益曲线仍逐日对齐。
        """

        result = demo_backtest(
            DemoBacktestRequest(
                mode="trend", periods=400, seed=42
            )
        )

        assert (
            len(result.benchmark)
            == len(result.equity_curve)
        )

        # benchmark 是 list[dict]，用下标访问
        assert result.benchmark[0]["date"] == (
            result.equity_start_date
        )

        assert result.benchmark[-1]["date"] == (
            result.equity_end_date
        )

    def test_response_is_strict_json(self):
        """响应可被严格 JSON 序列化（无 Infinity / NaN）。"""

        import json

        result = demo_backtest(
            DemoBacktestRequest(
                mode="range", periods=300, seed=42
            )
        )

        payload = result.model_dump()

        text = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
        )

        assert "Infinity" not in text
        assert "NaN" not in text

    def test_list_modes_cover_all_profiles(self):
        """模式清单与常量一致。"""

        modes = list_modes()

        assert [item["mode"] for item in modes] == list(
            MARKET_MODES
        )


# ==========================================================
# 6. 隔离性：Demo 路径绝不触碰真实行情
# ==========================================================

class TestDemoPathIsolation:
    """
    证明 Demo 路径不调用真实行情加载器。

    这是阶段 B 最关键的约束。
    验证方式是【源码静态分析】+【运行时 monkeypatch】双重确认。
    """

    def test_demo_data_does_not_import_data_loader(self):
        """demo_data.py 不得导入 engine.data_loader。"""

        tree = ast.parse(
            inspect.getsource(demo_data_module)
        )

        imported: set[str] = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                imported.add(module)
                for alias in node.names:
                    imported.add(f"{module}.{alias.name}")

        forbidden = {
            "engine.data_loader",
            "engine.data_loader.load_stock_data",
            "data_loader",
            "database",
            "redis_client",
            "main",
        }

        assert not (imported & forbidden), (
            f"demo_data.py 不得导入 {imported & forbidden}"
        )

    def test_demo_api_does_not_import_data_loader(self):
        """api/demo.py 不得导入 data_loader / database / redis。"""

        tree = ast.parse(inspect.getsource(demo_api))

        imported: set[str] = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                imported.add(module)
                for alias in node.names:
                    imported.add(f"{module}.{alias.name}")

        forbidden = {
            "engine.data_loader",
            "engine.data_loader.load_stock_data",
            "api.strategy",
            "database",
            "redis_client",
            "main",
        }

        assert not (imported & forbidden), (
            f"api/demo.py 不得导入 {imported & forbidden}"
        )

    @pytest.mark.skip(reason='发布副本仅含 Demo，不含生产链路模块（api.strategy / engine.data_loader）；本用例是主仓内的跨模块对照测试，合并回主仓后应恢复。')
    def test_load_stock_data_not_called_at_runtime(
        self, monkeypatch
    ):
        """
        运行时验证：把 load_stock_data 换成炸弹函数。

        若 Demo 路径真的调用了它，测试立即失败。
        """

        import engine.data_loader as loader

        def bomb(*args, **kwargs):
            raise AssertionError(
                "Demo 路径不得调用 load_stock_data"
            )

        monkeypatch.setattr(
            loader, "load_stock_data", bomb
        )

        result = demo_backtest(
            DemoBacktestRequest(
                mode="trend", periods=400, seed=42
            )
        )

        assert result.data_source == "synthetic"

    def test_no_reference_to_real_data_dir(self):
        """
        Demo 模块不得真正引用真实行情目录。

        注意实现方式：
            这里检查的是【AST 导入节点与实际调用】，
            而不是源码文本。

            原因：demo 模块的文档字符串里本就包含
            "load_stock_data" 与 "data/stock" 这些词，
            用于声明「本模块不会碰它们」。
            文本匹配会把这种声明误判为违规。
        """

        for module in (demo_data_module, demo_api):
            tree = ast.parse(inspect.getsource(module))

            # 1. 导入节点检查
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert "data_loader" not in alias.name
                        assert "database" not in alias.name
                        assert "redis" not in alias.name

                elif isinstance(node, ast.ImportFrom):
                    module_name = node.module or ""

                    assert "data_loader" not in module_name
                    assert "database" not in module_name
                    assert "redis" not in module_name
                    assert module_name != "main"

                    for alias in node.names:
                        assert alias.name != "load_stock_data"

            # 2. 实际调用检查：是否存在函数调用形式的引用
            called_names = {
                node.func.id
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
            }

            assert "load_stock_data" not in called_names
            assert "test_database" not in called_names
            assert "test_redis" not in called_names

            # 3. 文件读取检查：不得调用 read_bytes / open / read_csv
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func

                    if isinstance(func, ast.Name):
                        assert func.id != "open"
                        assert func.id != "read_bytes"

                    elif isinstance(func, ast.Attribute):
                        assert func.attr != "read_csv"
                        assert func.attr != "read_bytes"
                        assert func.attr != "read_text"

    def test_demo_docs_declare_isolation(self):
        """
        反向确认：文档中确实写明了隔离承诺。

        这不是安全措施，而是保证承诺可被审查者看到。
        """

        source = inspect.getsource(demo_api)

        assert "load_stock_data" in source
        assert "不读取任何文件" in source

        generator_source = inspect.getsource(demo_data_module)

        assert "load_stock_data" in generator_source

        # docstring 中的路径以字面双斜杠书写（data\\stock），
        # 因为该模块使用了 raw docstring 之外的普通字符串约定。
        normalized = generator_source.replace(
            "\\\\", "\\"
        )

        assert "data/stock" in normalized or (
            "data\\stock" in normalized
        )

    def test_demo_api_does_not_import_main(self):
        """api/demo.py 不得导入 main。"""

        source = inspect.getsource(demo_api)

        assert "import main" not in source
        assert "from main" not in source

    @pytest.mark.skip(reason='发布副本仅含 Demo，不含生产链路模块（api.strategy / engine.data_loader）；本用例是主仓内的跨模块对照测试，合并回主仓后应恢复。')
    def test_backtest_still_imports_data_loader(self):
        """
        对照验证：真实链路的引擎确实依赖 data_loader。

        这说明上面的隔离不是因为 data_loader 失效，
        而是因为 Demo 确实绕开了它。
        属于反向验证，防止「假阳性」。
        """

        import engine.backtest as engine_module

        source = inspect.getsource(engine_module)

        assert "data_loader" in source or "backtest" in source

        from engine.data_loader import load_stock_data

        assert callable(load_stock_data)


# ==========================================================
# 7. 基准构造
# ==========================================================

class TestBuyAndHoldBenchmark:
    """
    买入持有基准的构造与口径。
    """

    def test_benchmark_shape(self):
        """
        不传日期时基准与行情等长。

        默认不裁剪，保持既有行为不变。
        """

        frame = make_frame(periods=300)

        benchmark = build_buy_and_hold(frame)

        assert len(benchmark) == len(frame)
        assert "trade_date" in benchmark.columns
        assert "equity" in benchmark.columns

    def test_benchmark_trimmed_by_date_range(self):
        """
        传入日期区间时基准按闭区间裁剪。

        裁剪后首末值必须等于该区间内真实的收盘价。
        """

        frame = make_frame(periods=300)

        all_dates = [
            str(d)[:10] for d in frame["trade_date"]
        ]

        start = all_dates[10]
        end = all_dates[50]

        trimmed = build_buy_and_hold(
            frame, start_date=start, end_date=end
        )

        # 闭区间：10 到 50 共 41 个交易日
        assert len(trimmed) == 41

        assert str(trimmed["trade_date"].iloc[0])[:10] == start
        assert str(trimmed["trade_date"].iloc[-1])[:10] == end

        # 首值 = 区间内第 10 行收盘价
        assert trimmed["equity"].iloc[0] == pytest.approx(
            frame["close"].iloc[10]
        )

        assert trimmed["equity"].iloc[-1] == pytest.approx(
            frame["close"].iloc[50]
        )

    def test_benchmark_trim_out_of_range_raises(self):
        """裁剪后为空时必须抛错，不能返回空基准。"""

        frame = make_frame(periods=100)

        with pytest.raises(ValueError, match="裁剪后"):
            build_buy_and_hold(
                frame,
                start_date="2099-01-01",
                end_date="2099-12-31",
            )

    def test_benchmark_trim_keeps_values_unchanged(self):
        """
        裁剪只改长度，不改保留点的数值。

        验证裁剪后的值与原始行情逐点一致。
        """

        frame = make_frame(periods=200)

        dates = [str(d)[:10] for d in frame["trade_date"]]

        start = dates[20]
        end = dates[100]

        trimmed = build_buy_and_hold(
            frame, start_date=start, end_date=end
        )

        full = build_buy_and_hold(frame)

        # 在完整基准中定位同一日期区间的切片
        full_dates = [
            str(d)[:10] for d in full["trade_date"]
        ]

        begin = full_dates.index(start)
        finish = full_dates.index(end) + 1

        sliced = full.iloc[begin:finish]

        assert trimmed["equity"].to_numpy() == pytest.approx(
            sliced["equity"].to_numpy()
        )

    def test_benchmark_first_point_equals_first_close(self):
        """基准首点等于首日收盘价（零成本满仓）。"""

        frame = make_frame(periods=100)

        benchmark = build_buy_and_hold(frame)

        assert benchmark["equity"].iloc[0] == pytest.approx(
            frame["close"].iloc[0]
        )

    def test_benchmark_last_point_equals_last_close(self):
        """基准末点等于末日收盘价。"""

        frame = make_frame(periods=100)

        benchmark = build_buy_and_hold(frame)

        assert benchmark["equity"].iloc[-1] == pytest.approx(
            frame["close"].iloc[-1]
        )

    def test_benchmark_return_matches_price_change(self):
        """基准收益等于价格涨幅。"""

        frame = make_frame(periods=300)

        expected = (
            frame["close"].iloc[-1] / frame["close"].iloc[0] - 1
        )

        result = demo_backtest(
            DemoBacktestRequest(
                mode="trend", periods=300, seed=42
            )
        )

        assert result.benchmark_return == pytest.approx(
            expected
        )

    def test_benchmark_has_no_infinity(self):
        """基准序列必须全部有限。"""

        frame = make_frame(periods=300, mode="high_vol")

        benchmark = build_buy_and_hold(frame)

        values = benchmark["equity"].to_numpy(dtype=np.float64)

        assert np.isfinite(values).all()
