"""
JUJ 策略参数校验。

职责：
1. 校验 strategy_type 是否为受支持的策略
2. 校验 parameters 的键是否合法（拒绝未知参数，避免拼写错误被静默忽略）
3. 校验每个参数的类型与取值范围
4. 返回补全默认值后的参数（供策略工厂使用）

设计原则：
- 校验失败统一抛出 ValueError，由 API 层转换为 400
- 错误信息只描述参数本身，绝不包含本地路径或内部细节
- 本模块不参与任何信号计算，不影响回测结果
"""

from __future__ import annotations

from typing import Any


# ==========================================================
# 策略参数规范表
#
# 这里集中定义每个策略：
#   - 支持哪些参数
#   - 参数类型
#   - 默认值
#   - 合理取值范围
#
# 新增策略时只需在这里登记一份规范。
# ==========================================================

STRATEGY_SPECS: dict[str, dict[str, Any]] = {
    "ma_cross": {
        "label": "均线交叉",
        "parameters": {
            "fast_period": {
                "type": int,
                "default": 20,
                "min": 1,
                "max": 250,
            },
            "slow_period": {
                "type": int,
                "default": 60,
                "min": 2,
                "max": 500,
            },
        },
    },
    "kdj": {
        "label": "KDJ 金叉死叉",
        "parameters": {
            "rsv_period": {
                "type": int,
                "default": 9,
                "min": 2,
                "max": 250,
            },
            "k_period": {
                "type": int,
                "default": 3,
                "min": 1,
                "max": 100,
            },
            "d_period": {
                "type": int,
                "default": 3,
                "min": 1,
                "max": 100,
            },
        },
    },
    # ----------------------------------------------------------
    # 新增策略（第一批扩展）
    #
    # 注意：
    #   本表由 Demo API 与真实回测 API **共用**
    #   （api/demo.py 与 api/strategy.py 都调用
    #   normalize_strategy_parameters）。
    #   因此在这里登记的策略，对两条链路同时生效，
    #   这不是 Demo 专属白名单。
    # ----------------------------------------------------------
    "bollinger": {
        "label": "布林带",
        "parameters": {
            "period": {
                "type": int,
                "default": 20,
                "min": 2,
                "max": 250,
            },
            # 标准差倍数必须为正数，
            # 因此下限取 0.1 而不是 0，
            # 避免 std_dev=0 退化为「中轨单线」策略。
            "std_dev": {
                "type": float,
                "default": 2.0,
                "min": 0.1,
                "max": 10.0,
            },
        },
    },
    "rsi": {
        "label": "RSI 超卖超买",
        "parameters": {
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
        },
    },
    "macd": {
        "label": "MACD 金叉死叉",
        "parameters": {
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
        },
    },
}


SUPPORTED_STRATEGY_TYPES = tuple(STRATEGY_SPECS.keys())


def list_supported_strategies() -> list[dict[str, Any]]:
    """
    返回受支持策略及其参数规范（只读副本）。
    """

    return [
        {
            "strategy_type": strategy_type,
            "label": spec["label"],
            "parameters": {
                name: {
                    "type": rule["type"].__name__,
                    "default": rule["default"],
                    "min": rule["min"],
                    "max": rule["max"],
                }
                for name, rule in spec["parameters"].items()
            },
        }
        for strategy_type, spec in STRATEGY_SPECS.items()
    ]


# ==========================================================
# 内部工具
# ==========================================================

def _describe_supported() -> str:
    return "、".join(SUPPORTED_STRATEGY_TYPES)


def _validate_single_parameter(
    name: str,
    value: Any,
    rule: dict[str, Any],
) -> Any:
    """
    校验单个参数。

    返回通过校验的原始值（不做类型转换）。
    """

    expected_type = rule["type"]
    expected_name = expected_type.__name__

    # ------------------------------------------------------
    # bool 是 int 的子类，必须单独排除，
    # 否则 True 会被当成 1 混入周期参数。
    # ------------------------------------------------------

    if isinstance(value, bool):
        raise ValueError(
            f"{name} 必须是 {expected_name}，"
            f"不接受布尔值"
        )

    if not isinstance(value, expected_type):
        # ------------------------------------------------------
        # 整数可以安全地用在浮点参数上
        #
        # 背景：
        #     前端的 el-input-number 在 step=1 时产出的是整数，
        #     例如用户把 oversold 设为 30，传过来是 int 30，
        #     而规范声明的是 float。
        #     若严格要求 float，用户无法把阈值设成整数，
        #     这是一条现实中会被触发的路径，不是理论问题。
        #
        #     float(x) 对任何 int 都是无损的，
        #     因此这里放行并转换是安全的；
        #     反向（int 参数接受 float）则不行，
        #     因为 3.7 转成int 3 会静默改变语义。
        #
        # 关键：转换后必须【继续走范围校验】，
        #      不能在此直接 return，
        #      否则 oversold=-1 这类越界值会被静默放行。
        # ------------------------------------------------------

        if expected_type is float and isinstance(value, int):
            value = float(value)
        else:
            raise ValueError(
                f"{name} 必须是 {expected_name}，"
                f"当前类型为 {type(value).__name__}"
            )

    if value < rule["min"] or value > rule["max"]:
        raise ValueError(
            f"{name} 必须在 "
            f"{rule['min']} 到 {rule['max']} 之间，"
            f"当前值为 {value}"
        )

    return value


# ==========================================================
# 对外接口
# ==========================================================

def validate_strategy_parameters(
    strategy_type: str,
    parameters: dict,
) -> None:
    """
    校验策略类型和策略参数。

    校验失败抛出 ValueError。
    （保留原有函数签名，供既有调用方使用）
    """

    normalize_strategy_parameters(
        strategy_type=strategy_type,
        parameters=parameters,
    )


def normalize_strategy_parameters(
    strategy_type: str,
    parameters: dict,
) -> dict:
    """
    校验参数并返回补全默认值后的参数字典。

    返回的字典可以直接交给策略工厂，
    保证工厂永远拿不到未校验过的参数。
    """

    # ------------------------------------------------------
    # 1. strategy_type
    # ------------------------------------------------------

    if not isinstance(strategy_type, str):
        raise ValueError(
            "strategy_type 必须是字符串"
        )

    if strategy_type not in STRATEGY_SPECS:
        raise ValueError(
            f"不支持的策略类型：{strategy_type}。"
            f"当前支持的策略：{_describe_supported()}"
        )

    # ------------------------------------------------------
    # 2. parameters 容器
    # ------------------------------------------------------

    if parameters is None:
        parameters = {}

    if not isinstance(parameters, dict):
        raise ValueError(
            "parameters 必须是对象（JSON object）"
        )

    spec = STRATEGY_SPECS[strategy_type]
    param_rules = spec["parameters"]

    # ------------------------------------------------------
    # 3. 拒绝未知参数
    #
    # 少了这一步，fast_peroid 这类拼写错误会被
    # 静默替换成默认值，非常难排查。
    # ------------------------------------------------------

    unknown_keys = sorted(
        set(parameters.keys()) - set(param_rules.keys())
    )

    if unknown_keys:
        allowed = "、".join(sorted(param_rules.keys()))

        raise ValueError(
            f"{strategy_type} 不支持参数："
            f"{'、'.join(unknown_keys)}。"
            f"可用参数：{allowed}"
        )

    # ------------------------------------------------------
    # 4. 逐个校验 + 补默认值
    # ------------------------------------------------------

    normalized: dict[str, Any] = {}

    for name, rule in param_rules.items():
        if name in parameters:
            normalized[name] = (
                _validate_single_parameter(
                    name=name,
                    value=parameters[name],
                    rule=rule,
                )
            )
        else:
            normalized[name] = rule["default"]

    # ------------------------------------------------------
    # 5. 策略专属的组合约束
    # ------------------------------------------------------

    if strategy_type == "ma_cross":
        _validate_ma_cross(normalized)

    elif strategy_type == "macd":
        _validate_macd(normalized)

    elif strategy_type == "rsi":
        _validate_rsi(normalized)

    return normalized


def _validate_ma_cross(parameters: dict) -> None:
    """
    均线策略的组合约束。
    """

    fast_period = parameters["fast_period"]
    slow_period = parameters["slow_period"]

    if fast_period >= slow_period:
        raise ValueError(
            f"fast_period（{fast_period}）必须小于 "
            f"slow_period（{slow_period}）"
        )


def _validate_macd(parameters: dict) -> None:
    """
    MACD 的组合约束。

    与均线策略同构：快线周期必须小于慢线周期。
    """

    fast_period = parameters["fast_period"]
    slow_period = parameters["slow_period"]

    if fast_period >= slow_period:
        raise ValueError(
            f"fast_period（{fast_period}）必须小于 "
            f"slow_period（{slow_period}）"
        )


def _validate_rsi(parameters: dict) -> None:
    """
    RSI 的组合约束。

    要求 0 <= oversold < overbought <= 100。

    为什么必须严格小于：
        oversold == overbought 时，
        「上穿超卖」与「上穿超买」会在同一天同时成立，
        信号互相矛盾，行为不可预测。
        因此相等也不允许。
    """

    oversold = parameters["oversold"]
    overbought = parameters["overbought"]

    if oversold >= overbought:
        raise ValueError(
            f"oversold（{oversold}）必须小于 "
            f"overbought（{overbought}）"
        )
