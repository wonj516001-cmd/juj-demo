"""
Demo 请求模型参数边界测试。

============================================================
被测对象
============================================================
api/demo.py 的 DemoBacktestRequest.parameters 字段：

    - 键数量上限（MAX_PARAMETERS_ITEMS）
    - 嵌套深度上限（MAX_PARAMETERS_NESTING_DEPTH）

============================================================
隔离性保证
============================================================
本文件只导入：
    api.demo         —— Demo 独立路径，不触真实行情
    engine.*         —— 纯计算，不触数据库

**绝不导入 backend/main.py**，
因此不会触发 init_database() 与 Redis 连接。

运行方式（需先 cd 到 backend 目录）：

    .venv/Scripts/python.exe -m pytest tests/test_demo_parameters.py -q

============================================================
测试设计原则
============================================================
1. 断言具体的错误信息与错误类型，
   不断言「失败了」这类弱条件。
2. 边界值两侧都要覆盖：
   上限值本身必须被接受，上限 +1 必须被拒绝。
3. 明确验证「拒绝发生在回测执行之前」：
   非法参数不会产生任何回测结果字段。
4. 合法策略参数必须不受影响（回归保护）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError


BACKEND_ROOT = Path(__file__).resolve().parent.parent

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


# 注意：以下 import 均不触及 main / database / redis
from api.demo import (  # noqa: E402
    MAX_PARAMETERS_ITEMS,
    MAX_PARAMETERS_NESTING_DEPTH,
    DemoBacktestRequest,
    _count_nesting_depth,
)
import demo_main  # noqa: E402


client = TestClient(demo_main.app, raise_server_exceptions=False)


# ==========================================================
# 辅助
# ==========================================================

def make_request(**overrides) -> DemoBacktestRequest:
    """构造一个最小可用的 Demo 请求。"""

    payload = {
        "strategy_type": "kdj",
        "mode": "trend",
        "seed": 42,
        "periods": 300,
        "start_date": "2020-01-01",
    }
    payload.update(overrides)

    return DemoBacktestRequest(**payload)


def nested(depth: int) -> dict:
    """
    构造指定嵌套深度的参数字典。

    层级约定（与 _count_nesting_depth 一致）：
        顶层 parameters 记为第 0 层，
        它的直接键值记为第 1 层。

    因此 depth=1 -> {"a": 1}
         depth=2 -> {"a": {"b": 1}}
         depth=3 -> {"a": {"b": {"c": 1}}}
    """

    value = "leaf"

    for _ in range(depth - 1):
        value = {"k": value}

    return {"a": value}


# ==========================================================
# 1. 常量
# ==========================================================

class TestParameterLimits:
    """上限常量本身的固定值。"""

    def test_item_limit_is_twenty(self):
        assert MAX_PARAMETERS_ITEMS == 20

    def test_depth_limit_is_three(self):
        assert MAX_PARAMETERS_NESTING_DEPTH == 3


# ==========================================================
# 2. 键数量边界
# ==========================================================

class TestParametersItemCount:
    """键数量上限。"""

    def test_empty_parameters_accepted(self):
        request = make_request(parameters={})
        assert request.parameters == {}

    def test_omitted_parameters_default_empty(self):
        request = make_request()
        assert request.parameters == {}

    def test_single_key_accepted(self):
        request = make_request(parameters={"rsv_period": 9})
        assert request.parameters == {"rsv_period": 9}

    def test_exactly_at_limit_accepted(self):
        """恰好 20 个键必须被接受。"""

        params = {f"k{i}": i for i in range(MAX_PARAMETERS_ITEMS)}

        request = make_request(parameters=params)

        assert len(request.parameters) == MAX_PARAMETERS_ITEMS

    def test_beyond_limit_rejected(self):
        """21 个键必须被拒绝。"""

        params = {
            f"k{i}": i for i in range(MAX_PARAMETERS_ITEMS + 1)
        }

        with pytest.raises(ValidationError) as exc_info:
            make_request(parameters=params)

        error = exc_info.value.errors()[0]
        assert error["type"] == "too_long"
        assert "20" in error["msg"]

    def test_limit_enforced_before_validator(self):
        """
        键数上限由 Field(max_length=...) 声明式实现，
        必然早于 field_validator 执行。

        因此非法结构不会进入我们的 _check_parameters，
        错误类型是 pydantic 原生的 too_long，
        而非我们自己抛出的 ValueError。
        """

        params = {
            f"k{i}": i for i in range(MAX_PARAMETERS_ITEMS + 1)
        }

        with pytest.raises(ValidationError) as exc_info:
            make_request(parameters=params)

        error = exc_info.value.errors()[0]

        assert error["type"] == "too_long"
        assert error["ctx"]["max_length"] == MAX_PARAMETERS_ITEMS
        assert (
            error["ctx"]["actual_length"]
            == MAX_PARAMETERS_ITEMS + 1
        )


# ==========================================================
# 3. 嵌套深度边界
# ==========================================================

class TestParametersNestingDepth:
    """嵌套深度上限。"""

    def test_depth_one_accepted(self):
        request = make_request(parameters={"a": 1})
        assert request.parameters == {"a": 1}

    def test_depth_two_accepted(self):
        request = make_request(parameters=nested(2))
        assert request.parameters == nested(2)

    def test_depth_at_limit_accepted(self):
        """恰好 3 层必须被接受。"""

        request = make_request(parameters=nested(3))

        assert request.parameters == nested(3)

    def test_depth_beyond_limit_rejected(self):
        """4 层必须被拒绝。"""

        with pytest.raises(ValidationError) as exc_info:
            make_request(parameters=nested(4))

        error = exc_info.value.errors()[0]
        assert "嵌套层级过深" in error["msg"]
        assert "3" in error["msg"]

    def test_deeply_nested_rejected(self):
        """极深嵌套必须被拒绝，且不消耗过多资源。"""

        with pytest.raises(ValidationError):
            make_request(parameters=nested(20))

    def test_list_nesting_also_counted(self):
        """
        数组嵌套同样计入深度。

        否则可用 [[{...}]] 绕过深度检查。
        """

        params = {"a": [{"b": [{"c": 1}]}]}

        with pytest.raises(ValidationError) as exc_info:
            make_request(parameters=params)

        assert "嵌套层级过深" in exc_info.value.errors()[0]["msg"]

    def test_dict_keys_are_traversed(self):
        """
        键本身也计入深度。

        超长的键名同样是攻击面。
        """

        params = {"a": {"b": {"c": {"d": 1}}}}

        with pytest.raises(ValidationError):
            make_request(parameters=params)

    def test_count_nesting_depth_values(self):
        """_count_nesting_depth 的返回值与层级约定一致。"""

        assert _count_nesting_depth(nested(1)) == 1
        assert _count_nesting_depth(nested(2)) == 2
        assert _count_nesting_depth(nested(3)) == 3
        assert _count_nesting_depth(nested(4)) == 4

    def test_count_nesting_depth_stops_beyond_limit(self):
        """
        超过上限后立即返回，不再继续递归。

        这保证对无限深的数据不会栈溢出。
        """

        deep = nested(50)

        assert (
            _count_nesting_depth(deep)
            == MAX_PARAMETERS_NESTING_DEPTH + 1
        )


# ==========================================================
# 4. 参数类型
# ==========================================================

class TestParametersType:
    """parameters 的类型约束。"""

    def test_none_rejected_by_type_check(self):
        """
        parameters=None 在类型层即被拒绝。

        实际行为记录：
            parameters 声明为裸 dict，
            而 field_validator 默认 mode="after"，
            也就是说 pydantic 的类型校验先于 validator 执行。
            因此 None 在类型层就被拒绝，错误类型为 dict_type。

        这与生产链路 api/strategy.py 的行为一致。
        """

        with pytest.raises(ValidationError) as exc_info:
            make_request(parameters=None)

        assert exc_info.value.errors()[0]["type"] == "dict_type"

    def test_string_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            make_request(parameters="not-a-dict")

        assert exc_info.value.errors()[0]["type"] == "dict_type"

    def test_list_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            make_request(parameters=[1, 2, 3])

        assert exc_info.value.errors()[0]["type"] == "dict_type"

    def test_number_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            make_request(parameters=42)

        assert exc_info.value.errors()[0]["type"] == "dict_type"


# ==========================================================
# 5. 拒绝发生在回测执行之前
# ==========================================================

class TestRejectionHappensBeforeBacktest:
    """
    非法参数必须在回测执行前被拒绝。

    验证方式：
        检查响应中不含任何回测结果字段。
        若非法参数已跑到回测阶段，
        即使最终报错，响应里也会出现部分结果或错误来自引擎。
    """

    def test_deep_parameters_rejected_with_422(self):
        response = client.post(
            "/api/demo/backtest",
            json={
                "strategy_type": "kdj",
                "mode": "trend",
                "parameters": nested(4),
            },
        )

        assert response.status_code == 422

        body = response.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert body["error"]["field"] == "parameters"

        # 未进入回测阶段
        assert "equity_curve" not in body
        assert "trades" not in body
        assert "total_return" not in body

    def test_too_many_keys_rejected_with_422(self):
        response = client.post(
            "/api/demo/backtest",
            json={
                "strategy_type": "kdj",
                "mode": "trend",
                "parameters": {
                    f"k{i}": i
                    for i in range(MAX_PARAMETERS_ITEMS + 1)
                },
            },
        )

        assert response.status_code == 422

        body = response.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert body["error"]["field"] == "parameters"

        assert "equity_curve" not in body
        assert "trades" not in body

    def test_error_response_does_not_leak_values(self):
        """
        错误响应不得回显用户提交的参数值。

        与 api/demo.py 的既有约定一致：
            异常处理器只提取安全的字段名，
            不回显原始值。
        """

        response = client.post(
            "/api/demo/backtest",
            json={
                "strategy_type": "kdj",
                "mode": "trend",
                "parameters": {"secret_marker_value": "SENSITIVE"},
            },
        )

        body = response.json()

        assert "SENSITIVE" not in str(body)


# ==========================================================
# 6. 合法参数回归保护
# ==========================================================

class TestValidParametersUnaffected:
    """新增约束不得影响合法用法。"""

    def test_valid_kdj_parameters_accepted(self):
        request = make_request(
            strategy_type="kdj",
            parameters={"rsv_period": 9, "k_period": 3, "d_period": 3},
        )

        assert request.parameters["rsv_period"] == 9

    def test_valid_parameters_still_run_backtest(self):
        """合法参数的回测请求必须成功。"""

        response = client.post(
            "/api/demo/backtest",
            json={
                "strategy_type": "kdj",
                "mode": "trend",
                "seed": 42,
                "periods": 300,
                "start_date": "2020-01-01",
                "parameters": {
                    "rsv_period": 9,
                    "k_period": 3,
                    "d_period": 3,
                },
            },
        )

        assert response.status_code == 200

        body = response.json()
        assert body["data_source"] == "synthetic"
        assert body["is_demo"] is True

    def test_extra_is_forbid(self):
        """Demo 请求模型仍然禁止未知字段。"""

        with pytest.raises(ValidationError) as exc_info:
            make_request(unknown_field=1)

        assert exc_info.value.errors()[0]["type"] == "extra_forbidden"

    def test_response_structure_unchanged(self):
        """
        回测响应结构不变。

        这是回归保护：新增的请求层约束
        不应改变任何响应字段。
        """

        response = client.post(
            "/api/demo/backtest",
            json={
                "strategy_type": "kdj",
                "mode": "trend",
                "seed": 20240101,
                "periods": 300,
                "start_date": "2020-01-01",
            },
        )

        assert response.status_code == 200

        body = response.json()

        for field in (
            "data_source",
            "is_demo",
            "disclaimer",
            "mode",
            "mode_label",
            "seed",
            "periods",
            "strategy_type",
            "total_return",
            "annual_return",
            "max_drawdown",
            "sharpe_ratio",
            "total_trades",
            "winning_trades",
            "losing_trades",
            "win_rate",
            "profit_factor",
            "benchmark",
            "benchmark_return",
            "benchmark_curve_return",
            "excess_return",
            "equity_curve",
            "trades",
        ):
            assert field in body, f"响应缺少字段 {field}"
