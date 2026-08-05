from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from app.api.health import router as health_router
from app.api.v1.router import router as v1_router
from app.core.config import get_settings
from app.core.exceptions import AppException


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="医药会议智能分析平台第一阶段后端 API。",
        debug=settings.app_debug,
        lifespan=lifespan,
    )

    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
        request_id = getattr(request.state, "request_id", str(uuid4()))
        content = {
            "code": exc.code,
            "error_code": exc.code,
            "message": exc.message,
            "details": exc.details,
            "request_id": request_id,
        }
        return JSONResponse(status_code=exc.status_code, content=jsonable_encoder(content))

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        request_id = getattr(request.state, "request_id", str(uuid4()))
        return JSONResponse(
            status_code=422,
            content={
                "code": "validation_error",
                "error_code": "validation_error",
                "message": "请求参数校验失败",
                "details": jsonable_encoder(exc.errors()),
                "request_id": request_id,
            },
        )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request.state.request_id = request.headers.get("x-request-id") or str(uuid4())
        response = await call_next(request)
        response.headers["x-request-id"] = request.state.request_id
        return response

    app.include_router(health_router)
    app.include_router(v1_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
