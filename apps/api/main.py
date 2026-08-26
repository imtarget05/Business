from __future__ import annotations

import hmac
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from apps.api.routes.conversations import router as conversations_router
from apps.api.routes.health import router as health_router
from apps.api.routes.knowledge import router as knowledge_router
from apps.api.routes.router import router as dispatch_router
from apps.api.routes.tasks import router as v1_router
from packages.config.settings import Environment, get_settings
from packages.core.bootstrap import get_container
from packages.core.errors import (
    AuthenticationError,
    BusinessOpsError,
    ValidationError,
)
from packages.database.session import dispose_engine
from packages.observability.context import (
    RequestContext,
    new_request_id,
    reset_context,
    set_context,
)
from packages.observability.logging import configure_logging, get_logger

logger = get_logger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    # Fail-closed auth: never start without any authn boundary outside local.
    if (
        settings.environment is not Environment.LOCAL
        and not settings.api_key
        and not settings.tenant_api_keys
    ):
        raise RuntimeError(
            "Refusing to start: no api_key and no tenant_api_keys configured "
            f"in environment={settings.environment.value!r}"
        )
    logger.info("startup", extra={"environment": settings.environment.value})
    yield
    await dispose_engine()
    logger.info("shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Business Ops Agent Swarm API",
        version="0.1.0",
        description=(
            "Multi-agent platform for business operations "
            "(Phase 1 - core platform)"
        ),
        lifespan=lifespan,
    )
    get_container()  # eager-wire registry/orchestrator at startup

    app.include_router(health_router)
    app.include_router(v1_router)
    app.include_router(knowledge_router)
    app.include_router(dispatch_router)
    app.include_router(conversations_router)

    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):
        incoming = request.headers.get("X-Request-ID") or new_request_id()
        token = set_context(
            RequestContext(request_id=incoming, trace_id=request.headers.get("X-Trace-ID"))
        )
        try:
            response = await call_next(request)
        finally:
            reset_context(token)
        response.headers["X-Request-ID"] = incoming
        return response

    @app.middleware("http")
    async def api_key_middleware(request: Request, call_next):
        settings = get_settings()
        if request.url.path.startswith("/v1") and (
            settings.api_key or settings.tenant_api_keys
        ):
            supplied = request.headers.get("X-API-Key")
            valid = False
            if supplied:
                if settings.api_key and hmac.compare_digest(supplied, settings.api_key):
                    valid = True
                elif supplied in settings.tenant_api_keys:
                    valid = True
            if not valid:
                exc = AuthenticationError("Missing or invalid API key")
                return JSONResponse(
                    status_code=exc.http_status, content={"error": exc.to_payload()}
                )
        return await call_next(request)

    @app.exception_handler(BusinessOpsError)
    async def business_ops_error_handler(request: Request, exc: BusinessOpsError):
        logger.warning("request_failed", extra={"error_code": exc.code.value})
        return JSONResponse(status_code=exc.http_status, content={"error": exc.to_payload()})

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        err = ValidationError(
            "Request validation failed",
            details={"errors": exc.errors()[:10]},
        )
        return JSONResponse(status_code=err.http_status, content={"error": err.to_payload()})

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        # Never leak stack traces to clients (STEP 0.9).
        logger.error("unhandled_exception", extra={"type": type(exc).__name__})
        fallback = BusinessOpsError("Internal server error")
        return JSONResponse(status_code=500, content={"error": fallback.to_payload()})

    return app


app = create_app()
