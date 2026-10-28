"""
JUJ 合成行情生成器（Demo 专用）。

=============================================================
用途
=============================================================
为「验证版本」提供完全合成的 OHLCV 日线数据，
用于在没有真实行情依赖的情况下演示与验证回测链路。

=============================================================
数据性质声明（重要）
=============================================================
本模块生成的所有数据均为：

    **模拟数据，非真实行情，不代表实际交易表现。**

它由数学模型（几何布朗运动 / OU 均值回复）生成，
不是任何真实证券的历史价格，
不来自 Tushare、不来自任何行情 API、不来自数据库。

生成结果仅在内存中返回，**从不落盘**。

=============================================================
隔离性保证（关键设计约束）
=============================================================
1. 本模块**不导入** engine.data_loader，
   因此**不可能**调用 load_stock_data()。
2. 本模块**不导入** database / redis / main，
   因此导入它不会触发任何数据库初始化或 DDL。
3. 本模块**不读取任何文件**，全部数据在内存中生成。
    4. 生成结果**不写入** data\\stock 或任何目录，
   真实行情目录与合成数据在物理上完全隔离。

=============================================================
可复现性
=============================================================
使用 numpy.random.default_rng(seed)，
不依赖全局随机状态。

因此：
    相同的 (mode, seed, periods, start_date, base_price)
    必然生成**逐字节相同**的数据。

不使用 Python 内置 random，也不使用 np.random 的全局函数，
因为它们的随机序列会受调用顺序影响，无法保证可复现。

=============================================================
OHLCV 约束（生成后强制校验）
=============================================================
- high  >= max(open, close)
- low   <= min(open, close)
- low   >  0
- 所有价格为正
- vol   >= 0
- trade_date 严格递增且无重复

违反任一条即抛ValueError，不返回不合规数据。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


# ==========================================================
# 常量
# ==========================================================

#: 数据来源标识。所有 Demo 响应都必须携带它。
DATA_SOURCE = "synthetic"

#: 免责声明文案。
DISCLAIMER = "模拟数据，非真实行情，不代表实际交易表现"

#: 合法的行情模式。
MARKET_MODES: tuple[str, ...] = (
    "trend",
    "range",
    "high_vol",
)

#: 模式中文标签（供接口返回，前端可直接展示）。
MODE_LABELS: dict[str, str] = {
    "trend": "趋势",
    "range": "震荡",
    "high_vol": "高波动",
}

#: 生成器可接受的最小样本数。
MIN_PERIODS = 2

#: 生成器可接受的最大样本数。
#: 4000 个交易日约等于 16 年，足够覆盖任何策略周期，
#: 同时避免单次请求占用过多 CPU。
MAX_PERIODS = 4000

#: 默认随机种子。
#: 固定默认值保证「不指定 seed 也能得到可复现结果」。
DEFAULT_SEED = 20240101

#: 默认起始价格。
DEFAULT_BASE_PRICE = 10.0

#: seed 的合法上界。
#: np.random.default_rng 的种子为非负整数，
#: 上界取 2**32-1 以确保在所有平台一致。
MAX_SEED = 2**32 - 1


# ==========================================================
# 行情模式参数
# ==========================================================
#
# 每个模式由 (漂移 mu, 波动 sigma, 均值回复强度 kappa) 描述。
#
# mu     —— 对数价格的漂移（趋势强度）
# sigma  —— 对数收益的标准差（波动率）
# kappa  —— 均值回复强度，0 表示纯随机游走，
#           正值表示价格会被拉回锚点（震荡）
#
# 三组参数刻意拉开差异，使不同模式下
# 策略表现产生可区分的差异，便于演示。

@dataclass(frozen=True)
class MarketProfile:
    """单个行情模式的参数。"""

    label: str
    mu: float
    sigma: float
    kappa: float
    anchor: float


MARKET_PROFILES: dict[str, MarketProfile] = {
    # 单边上行趋势：漂移显著为正，波动中等，弱均值回复
    "trend": MarketProfile(
        label="趋势",
        mu=0.0008,
        sigma=0.018,
        kappa=0.0,
        anchor=0.0,
    ),
    # 横盘震荡：漂移为 0，波动较低，强均值回复
    #
    # anchor 必须为 0：
    #   anchor 是对数价格空间的偏移量，
    #   取0 表示锚点就是起始价本身，
    #   价格围绕起始价上下摆动。
    #   若取非 0（例如 1.0），锚点会远高于起始价，
    #   价格会一路向上收敛 —— 那是趋势，不是震荡。
    "range": MarketProfile(
        label="震荡",
        mu=0.0,
        sigma=0.012,
        kappa=0.05,
        anchor=0.0,
    ),
    # 高波动：漂移接近 0，波动显著放大，无均值回复
    "high_vol": MarketProfile(
        label="高波动",
        mu=0.0,
        sigma=0.045,
        kappa=0.0,
        anchor=0.0,
    ),
}


# ==========================================================
# 参数校验
# ==========================================================

def validate_mode(mode: str) -> str:
    """
    校验行情模式。

    失败抛 ValueError，由 API 层转为 400。
    """

    if not isinstance(mode, str):
        raise ValueError("mode 必须是字符串")

    normalized = mode.strip().lower()

    if normalized not in MARKET_PROFILES:
        raise ValueError(
            f"不支持的行情模式：{mode}。"
            f"当前支持：{'、'.join(MARKET_MODES)}"
        )

    return normalized


def validate_seed(seed: int) -> int:
    """
    校验随机种子。

    bool 是 int 的子类，必须单独排除，
    否则 True 会被当成种子 1 静默通过。
    """

    if isinstance(seed, bool):
        raise ValueError("seed 不接受布尔值")

    if not isinstance(seed, int):
        raise ValueError("seed 必须是整数")

    if seed < 0 or seed > MAX_SEED:
        raise ValueError(
            f"seed 必须在 0 到 {MAX_SEED} 之间"
        )

    return seed


def validate_periods(periods: int) -> int:
    """
    校验样本数量。
    """

    if isinstance(periods, bool):
        raise ValueError("periods 不接受布尔值")

    if not isinstance(periods, int):
        raise ValueError("periods 必须是整数")

    if periods < MIN_PERIODS:
        raise ValueError(
            f"periods 不能小于 {MIN_PERIODS}"
        )

    if periods > MAX_PERIODS:
        raise ValueError(
            f"periods 不能超过 {MAX_PERIODS}"
        )

    return periods


# ==========================================================
# 价格路径生成
# ==========================================================

def _build_price_path(
    rng: np.random.Generator,
    periods: int,
    profile: MarketProfile,
    base_price: float,
) -> np.ndarray:
    """
    生成对数价格路径。

    趋势 / 高波动使用几何布朗运动：

        logP[i] = logP[i-1] + (mu - sigma^2/2) + sigma * z[i]

    震荡使用 OU（Ornstein-Uhlenbeck）均值回复：

        logP[i] = logP[i-1]
                  + mu
                  - kappa * (logP[i-1] - log(anchor))
                  + sigma * z[i]

    为什么在【对数】空间生成：
        对数价格的一阶差分即对数收益率，
        天然保证价格为正，无需额外截断，
        也不会出现负价格或零价格。

    减sigma^2/2 是为了让算术期望下的漂移恰好等于 mu
    （连续时间的漂移修正项）。
    """

    log_base = float(np.log(base_price))

    # 标准正态随机数一次性抽好，保证调用顺序固定
    shocks = rng.standard_normal(periods)

    log_path = np.empty(periods, dtype=np.float64)

    # 震荡模式的锚点
    log_anchor = log_base + profile.anchor

    log_path[0] = log_base

    # 连续时间漂移修正
    drift = profile.mu - 0.5 * profile.sigma**2

    for index in range(1, periods):

        shock = profile.sigma * shocks[index]

        if profile.kappa > 0:
            deviation = log_path[index - 1] - log_anchor
            pull = -profile.kappa * deviation
            log_path[index] = (
                log_path[index - 1] + drift + pull + shock
            )
        else:
            log_path[index] = log_path[index - 1] + drift + shock

    return np.exp(log_path)


def _build_ohlc(
    rng: np.random.Generator,
    close: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    由收盘价路径构造 open / high / low。

    做法：
    - open[0] 用收盘价；此后 open[i] = close[i-1] * (1 + 跳空)
      跳空幅度来自同一 rng，符合真实市场的隔夜跳空。
    - high[i] = max(open[i], close[i]) * (1 + 上影线幅度)
    - low[i]  = min(open[i], close[i]) * (1 - 下影线幅度)

    这样构造在数学上**必然**满足：

        high >= max(open, close)
        low<= min(open, close)

    不需要事后修正，也就不存在修正失败的风险。
    """

    periods = len(close)

    # 上影线 / 下影线 / 跳空幅度，均为非负比例
    upper_wick = rng.random(periods) * 0.008
    lower_wick = rng.random(periods) * 0.008
    gap = rng.standard_normal(periods) * 0.004

    open_price = np.empty(periods, dtype=np.float64)
    high_price = np.empty(periods, dtype=np.float64)
    low_price = np.empty(periods, dtype=np.float64)

    open_price[0] = close[0]

    for index in range(1, periods):
        open_price[index] = close[index - 1] * (
            1.0 + gap[index]
        )

    # 保证 open 仍为正
    open_price = np.maximum(open_price, 1e-6)

    body_high = np.maximum(open_price, close)
    body_low = np.minimum(open_price, close)

    high_price = body_high * (1.0 + upper_wick)
    low_price = body_low * (1.0 - lower_wick)

    # 数值兜底：low 必须严格为正
    low_price = np.maximum(low_price, 1e-6)

    return open_price, high_price, low_price, close


def _build_volume(
    rng: np.random.Generator,
    close: np.ndarray,
) -> np.ndarray:
    """
    生成成交量。

    做法：基础量 × 对数正态噪声 × 波动放大因子。

    波动放大因子让「大幅波动日」的成交量更高，
    更接近真实市场的量价关系。

    成交量单位为「股」，向下取整保证是非负整数。
    """

    periods = len(close)

    base_volume = 1_000_000.0

    log_noise = rng.standard_normal(periods) * 0.35

    # 相对价格变化的绝对值
    returns = np.abs(np.diff(close, prepend=close[0])) / close

    activity = 1.0 + returns * 20.0

    volume = base_volume * np.exp(log_noise) * activity

    # 向下取整并保证非负
    return np.maximum(volume, 0.0).astype(np.int64)


# ==========================================================
# 主生成函数
# ==========================================================

def generate_demo_ohlcv(
    periods: int,
    mode: str,
    seed: int,
    start_date: str,
    base_price: float,
) -> pd.DataFrame:
    """
    生成合成 OHLCV 日线数据。

    参数：
        periods      交易日数量
        mode         行情模式：trend / range / high_vol
        seed         随机种子（决定可复现性）
        start_date   起始日期，格式 YYYY-MM-DD
        base_price   起始价格

    返回：
        DataFrame，列为：

            trade_date  交易日期（datetime64）
            open        开盘价
            high        最高价
            low         最低价
            close       收盘价
            vol         成交量（股，非负整数）
            pct_chg     日涨跌幅（百分比，非小数）
            data_source 恒为 "synthetic"

    重要：
        返回的是**全新对象**，修改它不会影响任何全局状态。
        本函数**不读取任何文件**。
    """

    normalized_mode = validate_mode(mode)
    validated_seed = validate_seed(seed)
    validated_periods = validate_periods(periods)

    if not isinstance(base_price, (int, float)) or isinstance(
        base_price, bool
    ):
        raise ValueError("base_price 必须是数字")

    if not np.isfinite(base_price) or base_price <= 0:
        raise ValueError("base_price 必须是大于 0 的有限数值")

    # bdate_range 产出工作日序列，
    # 天然严格递增且无重复，无需额外去重。
    # 频率推断失败时退回 W-FRI，避免运行期异常。
    try:
        dates = pd.bdate_range(
            start=start_date,
            periods=validated_periods,
        )
    except ValueError as exc:
        raise ValueError(
            f"start_date 无法解析：{start_date}"
        ) from exc

    profile = MARKET_PROFILES[normalized_mode]

    # 唯一随机源：seed 完全决定后续所有随机数
    rng = np.random.default_rng(validated_seed)

    close = _build_price_path(
        rng=rng,
        periods=validated_periods,
        profile=profile,
        base_price=float(base_price),
    )

    open_price, high_price, low_price, close = _build_ohlc(
        rng=rng,
        close=close,
    )

    volume = _build_volume(rng=rng, close=close)

    frame = pd.DataFrame({
        "trade_date": dates,
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "close": close,
        "vol": volume,
    })

    # 日涨跌幅（百分比口径，与真实行情的 pct_chg 一致）
    frame["pct_chg"] = (
        frame["close"].pct_change() * 100.0
    ).round(4)

    # 首个交易日没有前收盘，涨跌幅无定义，置 0
    frame.loc[0, "pct_chg"] = 0.0

    # 数据来源标记：逐行写入，
    # 即使调用方把 DataFrame 传遍系统也不会丢失来源信息。
    frame["data_source"] = DATA_SOURCE

    # 价格统一保留 4 位小数，与真实行情精度对齐
    for column in ("open", "high", "low", "close"):
        frame[column] = frame[column].round(4)

    return validate_ohlcv(frame)


# ==========================================================
# 自校验
# ==========================================================

def validate_ohlcv(frame: pd.DataFrame) -> pd.DataFrame:
    """
    校验合成数据是否满足全部 OHLCV 约束。

    任何一条不满足都抛 ValueError。
    这是最后一道防线 —— 即使生成逻辑将来被改动，
    也不可能把不合规数据交给回测引擎。
    """

    if frame is None or frame.empty:
        raise ValueError("合成数据为空")

    required = (
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "vol",
    )

    missing = [
        column
        for column in required
        if column not in frame.columns
    ]

    if missing:
        raise ValueError(
            f"合成数据缺少字段：{missing}"
        )

    # ------------------------------------------------------
    # 价格关系
    # ------------------------------------------------------

    open_price = frame["open"]
    high_price = frame["high"]
    low_price = frame["low"]
    close_price = frame["close"]

    body_high = np.maximum(open_price, close_price)
    body_low = np.minimum(open_price, close_price)

    if (high_price < body_high - 1e-9).any():
        raise ValueError(
            "合成数据违反约束：high < max(open, close)"
        )

    if (low_price > body_low + 1e-9).any():
        raise ValueError(
            "合成数据违反约束：low > min(open, close)"
        )

    # ------------------------------------------------------
    # 正数与有限性
    # ------------------------------------------------------

    for column, series in (
        ("open", open_price),
        ("high", high_price),
        ("low", low_price),
        ("close", close_price),
    ):
        if not np.isfinite(series.to_numpy(dtype=np.float64)).all():
            raise ValueError(
                f"合成数据的 {column} 含非有限值"
            )

        if (series <= 0).any():
            raise ValueError(
                f"合成数据的 {column} 必须为正数"
            )

    if (frame["vol"] < 0).any():
        raise ValueError(
            "合成数据的 vol 必须为非负数"
        )

    # ------------------------------------------------------
    # 日期有序且无重复
    # ------------------------------------------------------

    dates = frame["trade_date"]

    if dates.duplicated().any():
        raise ValueError(
            "合成数据的 trade_date 存在重复"
        )

    if not dates.is_monotonic_increasing:
        raise ValueError(
            "合成数据的 trade_date 未严格递增"
        )

    return frame


# ==========================================================
# 买入持有基准
# ==========================================================

def build_buy_and_hold(
    frame: pd.DataFrame,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    """
    构造买入持有基准序列。

    ==========================================================
    定义
    ==========================================================
    首日以收盘价全额买入并持有，之后不做任何操作。

    ==========================================================
    与回测引擎的差异（必须在展示时说明）
    ==========================================================
    引擎计入佣金、印花税与滑点；
    本基准为**零成本**理想曲线，
    因此基准收益会略高于同等条件下的真实成交结果。
    这是刻意的简化，用于直观对比，
    不应被解读为「无成本交易是可行的」。

    ==========================================================
    start_date / end_date：与权益曲线对齐
    ==========================================================
    回测引擎在回测前会丢弃策略预热期不可用数据
    （KDJ 丢前 8 行，MA 丢前 59 行），
    因此 equity_curve 的起点晚于原始行情起点。

    若基准不裁剪，两条曲线的日期范围不同，
    前端按日期对齐画图会错位。

    因此这里允许按 start_date / end_date 裁剪基准，
    使其与 equity_curve 覆盖完全相同的日期范围。

    ==========================================================
    重要：裁剪不等于改变收益定义
    ==========================================================
    裁剪只影响【序列本身的长度与起点】，
    不影响【基准收益的计算口径】。

    调用方应：
        用裁剪后的基准做「曲线对比展示」
        用未裁剪的完整区间算「基准收益」

    若把裁剪后的首末价直接相除当作基准收益，
    得到的是「预热期内涨跌幅」而非「全区间涨跌幅」，
    两者在本次实测中相差 5.1 个百分点，
    会显著误导超额收益的解读。

    返回：
        DataFrame，列为 trade_date / equity / pct_chg
    """

    if frame is None or frame.empty:
        raise ValueError("合成数据为空，无法构造基准")

    close_price = frame["close"].astype(np.float64)

    first_price = float(close_price.iloc[0])

    if first_price <= 0:
        raise ValueError("首日收盘价必须为正")

    dates = pd.to_datetime(frame["trade_date"])

    # ------------------------------------------------------
    # 可选日期裁剪（闭区间，与 data_loader 的约定一致）
    # ------------------------------------------------------

    mask = pd.Series(True, index=frame.index)

    if start_date is not None:
        mask &= dates >= pd.to_datetime(start_date)

    if end_date is not None:
        mask &= dates <= pd.to_datetime(end_date)

    trimmed = frame[mask.to_numpy()]

    if trimmed.empty:
        raise ValueError(
            "按给定日期区间裁剪后，基准数据为空"
        )

    benchmark = pd.DataFrame({
        "trade_date": trimmed["trade_date"].to_numpy(),
        "equity": (
            trimmed["close"].astype(np.float64).to_numpy()
        ),
    })

    benchmark["pct_chg"] = (
        benchmark["equity"].pct_change() * 100.0
    ).round(4)

    benchmark.loc[0, "pct_chg"] = 0.0

    return benchmark


# ==========================================================
# 元信息
# ==========================================================

def describe_mode(mode: str) -> dict:
    """
    返回单个模式的描述信息。

    供 GET /api/demo/modes 使用，
    让前端不必硬编码模式清单。
    """

    normalized = validate_mode(mode)

    profile = MARKET_PROFILES[normalized]

    return {
        "mode": normalized,
        "label": profile.label,
        "mu": profile.mu,
        "sigma": profile.sigma,
        "kappa": profile.kappa,
        "data_source": DATA_SOURCE,
        "is_demo": True,
        "disclaimer": DISCLAIMER,
    }


def list_modes() -> list[dict]:
    """
    返回全部行情模式。
    """

    return [
        describe_mode(mode)
        for mode in MARKET_MODES
    ]
