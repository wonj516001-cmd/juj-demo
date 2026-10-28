"""
JUJ 严格 JSON 序列化工具。

背景：
    Python 标准库 json 默认允许 NaN / Infinity，
    序列化结果是 Infinity、-Infinity、NaN，
    这些都不是合法 JSON（RFC 8259），
    前端 JSON.parse 会直接抛错。

    回测指标里 Profit Factor 在没有亏损交易时
    会被定义为 float("inf")，正好会触发这个问题。

本模块提供：
    1. to_json_safe：递归清洗任意结构，保证可被严格解析
    2. 明确的字段返回规则（见下方文档）

字段返回规则（重要，已文档化）：
    profit_factor
        类型：number | null
        当存在亏损交易时，为「总盈利 / 总亏损」的有限数值。
        当不存在亏损交易时，分母为 0，数学上无定义，
        此时返回 null，而不是 Infinity。
        null 表示「无亏损交易，Profit Factor 无定义」，
        不代表 0，也不代表亏损。

    max_drawdown
        类型：number
        始终为有限数值（无持仓回撤时为 0.0）。

    所有浮点字段
        类型：number | null
        非有限值（inf / -inf / NaN）统一转为 null，
        绝不会出现在响应中。
"""

from __future__ import annotations

import datetime
import math
import sys
from typing import Any


# ==========================================================
# 日期输出格式
#
# 统一为 YYYY-MM-DD 字符串，
# 避免前端处理 ISO 时间戳或带时区的对象。
# ==========================================================

DATE_FORMAT = "%Y-%m-%d"


def _numpy_floating_types() -> tuple:
    """
    返回 numpy.floating 元类，用于区分浮点与整数标量。

    numpy 缺失时返回空元组，isinstance 恒为 False。
    """

    numpy = sys.modules.get("numpy")

    if numpy is None:
        return ()

    return (numpy.floating,)


def _is_numpy_bool(value: Any) -> bool:
    """
    判断是否为 numpy.bool_。

    np.bool_ 的特殊之处：
        它既不是 Python bool 的子类，
        也不是 np.integer，
        因此必须单独判断，否则会漏过转换。
    """

    numpy = sys.modules.get("numpy")

    if numpy is None:
        return False

    return isinstance(value, numpy.bool_)


def _is_numpy_scalar(value: Any) -> bool:
    """
    判断是否为 NumPy 标量（np.int64 / np.float64 / np.longdouble 等）。
    """

    numpy = sys.modules.get("numpy")

    if numpy is None:
        return False

    return isinstance(
        value,
        (numpy.integer, numpy.floating),
    )


def _to_date_string(value: Any) -> str:
    """
    把日期时间对象转为 YYYY-MM-DD 字符串。
    """

    # pandas.Timestamp 继承自 datetime.datetime，
    # 因此 isinstance 判断对两者都成立。
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.strftime(DATE_FORMAT)

    return str(value)


# ==========================================================
# 内部工具
# ==========================================================

def _is_finite_number(value: Any) -> bool:
    """
    判断是否为有限数值。

    注意 bool 是 int 的子类，这里需要排除。
    """

    if isinstance(value, bool):
        return False

    if isinstance(value, int):
        return True

    if isinstance(value, float):
        return math.isfinite(value)

    return False


def to_json_safe(value: Any) -> Any:
    """
    递归清洗数据，保证结果可以被严格 JSON 解析。

    规则：
    - datetime / date（含 pandas.Timestamp）
      -> YYYY-MM-DD 字符串
    - NumPy 整数 -> Python int
    - NumPy 浮点 -> Python float，再走有限性判断
    - float('inf') / float('-inf') / NaN -> None
      （对原生 float 与 NumPy float 同样生效）
    - bool -> 原值（不会被误当作 int）
    - int / float（有限）-> 原值
    - dict -> 逐个清洗 value
    - list / tuple -> 逐个清洗元素
    - 其余类型 -> 原值（交给 pydantic 处理）
    """

    # ------------------------------------------------------
    # 布尔值必须最先判断：
    # bool 是 int 的子类，放到后面会被当成 0 / 1
    # ------------------------------------------------------

    if isinstance(value, bool):
        return value

    # ------------------------------------------------------
    # 日期时间
    #
    # 含 pandas.Timestamp：
    #     它继承自 datetime.datetime，
    #     因此同一个 isinstance 判断即可覆盖。
    # ------------------------------------------------------

    if isinstance(value, (datetime.datetime, datetime.date)):
        return _to_date_string(value)

    if _is_numpy_bool(value):
        # np.bool_ 必须单独判断：
        #     它既不是 Python bool 的子类，
        #     也不是 np.integer，
        #     若不拦截会漏过转换，
        #     导致 json.dumps 抛 TypeError。
        return bool(value)

    # ------------------------------------------------------
    # NumPy 标量
    #
    # 不能直接依赖 json / FastAPI 自动转换：
    #     np.int64 不被 json.dumps 接受
    # 统一转成 Python 原生类型，并复用现有有限性规则。
    #
    # 浮点用 float() 而非 .item()：
    #     np.longdouble.item() 返回的仍是 longdouble，
    #     而 float() 能安全降级为 Python float
    #     （超出 double 范围时会得到 inf，
    #       随即被下面的 isfinite 判断拦下）。
    # ------------------------------------------------------

    if _is_numpy_scalar(value):
        if isinstance(value, _numpy_floating_types()):
            try:
                native = float(value)
            except (TypeError, ValueError, OverflowError):
                # 无法安全降级为 float，按非有限值处理
                return None

            if math.isfinite(native):
                return native

            return None

        try:
            return int(value.item())
        except (TypeError, ValueError, OverflowError):
            return None

    # ------------------------------------------------------
    # 原生数值
    # ------------------------------------------------------

    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return None

    if isinstance(value, int):
        return value

    # ------------------------------------------------------
    # 容器
    # ------------------------------------------------------

    if isinstance(value, dict):
        return {
            key: to_json_safe(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [
            to_json_safe(item)
            for item in value
        ]

    return value


def assert_strict_json_serializable(payload: Any) -> bytes:
    """
    将数据序列化为严格 JSON 字节串。

    使用 allow_nan=False：
        一旦出现 NaN / Infinity 立即抛错，
        绝不放行非法 JSON。
    """

    import json

    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
