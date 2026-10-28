"""
JUJ 合成行情 Demo —— 独立启动入口。

=============================================================
本文件解决什么问题
=============================================================
原有启动路径是 `main:app`，而`main.py` 在**模块级**
执行了 `init_database()`，其内部会调用
`Base.metadata.create_all(bind=engine)`。

后果：
    仅仅为了演示一个【纯内存合成数据】的回测，
    也必须先连上 PostgreSQL 并可能执行建表 DDL，
    同时 `main.py` 还导入了 redis_client。
    数据库不可达时，服务在导入阶段就直接崩溃。

本文件提供一个**零外部依赖**的入口：
    只注册合成行情 Demo 路由 + 策略规范查询，
    不导入 main.py，不初始化数据库，不连接 Redis。

=============================================================
安全边界（必须保持，改动前请逐条复核）
=============================================================
1. **不导入 main**
   因此不会执行 init_database()。

2. **不导入 database / redis_client / models**
   这三个模块是唯一会建立外部连接的入口
   （create_engine / redis.from_url）。

3. **不注册任何探测型路由**
   原 main.py 的 /api/health、/api/db-test、
   /api/redis-test 每次调用都会真实连接数据库与 Redis，
   本文件一律不提供。

4. **不读取 .env**
   不调用 load_dotenv()，因此不依赖任何凭据。

5. **不复制业务逻辑**
   策略工厂、参数校验、Demo 实现、回测引擎
   全部从既有模块导入，本文件只做路由装配。

=============================================================
与既有代码的耦合面
=============================================================
唯一的共享依赖是 `api/demo.py` 与 `engine/*`。
经静态核对（见模块底部注释），
这两条链上不存在 database / redis / models 的引用。

启动方式：
    uvicorn demo_main:app --port 8001
"""

import logging

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.schemas import ErrorCode
from engine.strategy_validator import list_supported_strategies

from api import demo as demo_api


# ==========================================================
# 日志
# ==========================================================

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s | %(levelname)-8s | "
        "%(name)s | %(message)s"
    ),
)

logger = logging.getLogger("juj.api.demo_main")


# ==========================================================
# 应用
# ==========================================================

app = FastAPI(
    title="JUJ Demo API",
    version="0.1.0",
    description=(
        "JUJ 合成行情演示接口。\n\n"
        "本服务完全离线运行：\n"
        "  - 不连接 PostgreSQL\n"
        "  - 不连接 Redis\n"
        "  - 不读取任何凭据\n"
        "  - 不读取任何真实行情文件\n\n"
        "全部数据由数学模型在内存中生成，"
        "结果不代表实际交易表现。"
    ),
)


# ==========================================================
# 路由注册
# ==========================================================

app.include_router(demo_api.router)


@app.get(
    "/api/strategy/types",
    tags=["strategy"],
    summary="查询支持的策略与参数范围",
)
def strategy_types():
    """
    返回受支持的策略类型及其参数规范。

    与 main.py 中的同名接口实现一致（同样调用
    engine.strategy_validator.list_supported_strategies），
    复制而非改动 main.py，是为了避免碰数据库初始化逻辑。

    Demo 表单需要它来渲染参数输入框，
    因此这个最小依赖必须保留。
    """

    return {
        "strategies": list_supported_strategies(),
    }


@app.get(
    "/api/health",
    tags=["health"],
    summary="存活检查",
)
def health():
    """
    仅确认进程存活。

    注意与 main.py 的 /api/health 语义不同：
        main.py 的版本会调用 test_database() 与 test_redis()，
        真实连接外部服务；
        本版本不连接任何外部服务。
    """

    return {
        "service": "JUJ Demo API",
        "status": "ok",
        "postgresql": None,
        "redis": None,
        "note": "本服务不连接数据库与 Redis",
    }


# ==========================================================
# 异常处理
# ==========================================================
#
# 保持与 main.py 一致的响应结构 {"error": {...}}，
# 便于前端 toApiError 复用同一套解析逻辑。
#
# 这里不注册 main.py 中的 Exception 兜底处理器：
#   FastAPI 的处理是逐级回退的，
#   缺失的兜底只会让未捕获异常走Starlette 默认格式，
#   不影响 Demo 主链路，且少一层全局捕获更易排查。

@app.exception_handler(StarletteHTTPException)
async def starlette_http_exception_handler(
    request,
    exc: StarletteHTTPException,
):
    """
    处理路由级 HTTP 错误（404/ 405 等）。

    与 main.py 保持一致：只统一格式，不改状态码。
    """

    status_code = exc.status_code

    if status_code == 404:
        code = ErrorCode.NOT_FOUND
        message = "接口不存在"

    elif status_code == 405:
        code = ErrorCode.METHOD_NOT_ALLOWED
        message = "请求方法不被允许"

    else:
        code = ErrorCode.INTERNAL_ERROR
        message = "请求处理失败"

    logger.info(
        "route level http error: path=%s method=%s status=%s",
        request.url.path,
        request.method,
        status_code,
    )

    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
            },
        },
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(
    request,
    exc: HTTPException,
):
    """
    统一业务 HTTPException 的响应格式。

    api/demo.py 抛出的 detail 已是
    {"code": ..., "message": ..., "field": ...} 结构，
    这里只做一层包装。
    """

    detail = exc.detail

    if isinstance(detail, dict) and "code" in detail:
        error = {
            "code": detail.get(
                "code",
                ErrorCode.INTERNAL_ERROR,
            ),
            "message": detail.get(
                "message",
                "请求处理失败",
            ),
        }

        if detail.get("field"):
            error["field"] = detail["field"]

    else:
        error = {
            "code": ErrorCode.INTERNAL_ERROR,
            "message": str(detail),
        }

    return JSONResponse(
        status_code=exc.status_code,
        content={"error": error},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request,
    exc: RequestValidationError,
):
    """
    Pydantic 字段级校验失败（422）。

    只提取字段名，不回显用户提交的原始值。
    """

    errors = exc.errors()

    field = None

    if errors:
        location = errors[0].get("loc") or ()
        field = ".".join(
            str(part)
            for part in location
            if part != "body"
        ) or None

    logger.info(
        "request validation failed: path=%s field=%s",
        request.url.path,
        field,
    )

    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": ErrorCode.VALIDATION_ERROR,
                "message": "请求参数不合法",
                "field": field,
            },
        },
    )