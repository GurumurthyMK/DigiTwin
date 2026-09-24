"""Structured API error model + handlers. All errors -> {detail:{code,message}}."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)


def error_body(code: str, message: str) -> dict:
    return {"detail": {"code": code, "message": message}}


class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(content=error_body(exc.code, exc.message), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            content=error_body("validation_error", str(exc.errors())),
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        # Fail closed with the SAME schema clients already parse. The full
        # traceback stays server-side (log only) — never in the response.
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            content=error_body("internal_error", "Something went wrong. Retry shortly."),
            status_code=500,
        )
