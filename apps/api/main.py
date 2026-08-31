from __future__ import annotations

import hmac
import os
import time
from collections import defaultdict
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from apps.api.routes.conversations import router as conversations_router
from apps.api.routes.feedback import router as feedback_router
from apps.api.routes.health import router as health_router
from apps.api.routes.knowledge import router as knowledge_router
from apps.api.routes.router import router as dispatch_router
from apps.api.routes.tasks import router as v1_router
from packages.config.settings import Environment, get_settings
from packages.core.bootstrap import get_container
from packages.core.errors import (
    AuthenticationError,
    BusinessOpsError,
    RateLimitError,
    ValidationError,
)
from packages.database.repositories.api_keys import ApiKeyRepository
from packages.database.session import dispose_engine, get_session_factory
from packages.observability.context import (
    RequestContext,
    new_request_id,
    reset_context,
    set_context,
)
from packages.observability.logging import configure_logging, get_logger
from packages.observability.metrics import prometheus_enabled

logger = get_logger("api")


class SlidingWindowRateLimiter:
    """In-memory sliding window rate limiter per API key.

    Simple implementation suitable for pilot scale. For production at scale,
    replace with Redis-backed distributed limiter.
    """

    def __init__(self, max_requests: int, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        # key -> list of request timestamps
        self._requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str) -> tuple[bool, int]:
        """Check if request is allowed. Returns (allowed, remaining)."""
        if self.max_requests <= 0:
            return True, self.max_requests

        now = time.time()
        window_start = now - self.window_seconds

        # Clean old entries
        timestamps = self._requests[key]
        # Keep only timestamps within the window
        while timestamps and timestamps[0] < window_start:
            timestamps.pop(0)

        if len(timestamps) >= self.max_requests:
            return False, 0

        timestamps.append(now)
        return True, self.max_requests - len(timestamps)


# Rate limiter stored in app.state (initialized at startup)
# No global to avoid test cross-contamination

def get_rate_limiter(app: FastAPI) -> SlidingWindowRateLimiter | None:
    return getattr(app.state, "rate_limiter", None)

def init_rate_limiter(app: FastAPI, max_requests: int, window_seconds: int = 60) -> None:
    app.state.rate_limiter = SlidingWindowRateLimiter(max_requests, window_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    # Initialize rate limiter
    init_rate_limiter(app, settings.rate_limit_per_minute, 60)
    # Fail-closed auth: never start without any authn boundary outside local.
    env_value = settings.environment.value if hasattr(settings.environment, 'value') else settings.environment
    if (
        env_value != Environment.LOCAL.value
        and not settings.api_key
        and not settings.tenant_api_keys
    ):
        raise RuntimeError(
            "Refusing to start: no api_key and no tenant_api_keys configured "
            f"in environment={env_value!r}"
        )
    logger.info("startup", extra={"environment": env_value})
    yield
    await dispose_engine()
    logger.info("shutdown")


async def _verify_api_key(api_key: str) -> str | None:
    """Verify API key against DB. Returns organization_id (string) or None."""
    if not api_key:
        return None
    
    factory = get_session_factory()
    async with factory() as session:
        repo = ApiKeyRepository(session)
        org_id = await repo.verify(api_key)
        if org_id:
            return str(org_id)
    return None


def _require_metrics_token(request: Request) -> None:
    """Gate /metrics when METRICS_TOKEN is configured.

    When METRICS_TOKEN is unset (default) /metrics stays open so local
    scraping and the hermetic test client work unchanged (D3). When set,
    the endpoint requires it as a Bearer token or via ``?token=``.
    """
    token = os.environ.get("METRICS_TOKEN")
    if not token:
        return
    auth = request.headers.get("Authorization", "")
    supplied = (
        auth[7:] if auth.lower().startswith("bearer ") else request.query_params.get("token", "")
    )
    if not supplied or not hmac.compare_digest(supplied, token):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _instrument_prometheus(app: FastAPI) -> None:
    """Attach prometheus-fastapi-instrumentator and expose GET /metrics.

    The import is lazy so the API keeps booting when the optional
    observability extras are absent; /metrics is then simply not mounted and
    a structured warning is logged. Serving the endpoint needs no external
    service: it renders the in-process registry (default HTTP metrics plus
    the ``boas_*`` business counters) as plain text.
    """
    try:
        from prometheus_fastapi_instrumentator import Instrumentator
    except ImportError:  # pragma: no cover - optional dependency
        logger.warning(
            "prometheus_instrumentation_skipped",
            extra={"reason": "prometheus_fastapi_instrumentator_not_installed"},
        )
        return

    (
        Instrumentator(
            should_group_status_codes=False,
            should_ignore_untemplated=True,
            excluded_handlers=["/metrics"],
        )
        .instrument(app)
        .expose(
            app,
            endpoint="/metrics",
            include_in_schema=False,
            tags=["observability"],
            dependencies=[Depends(_require_metrics_token)],
        )
    )
    logger.info(
        "prometheus_instrumentation_enabled",
        extra={"endpoint": "/metrics", "business_metrics": prometheus_enabled()},
    )


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
    app.include_router(feedback_router)

    # Prometheus scrape endpoint + default HTTP metrics (Feature 3).
    # Business counters live in packages.observability.metrics.
    _instrument_prometheus(app)

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
    async def auth_middleware(request: Request, call_next):
        """Verify API key for /v1/* routes. Returns org_id in request.state."""
        settings = get_settings()
        if request.url.path.startswith("/v1"):
            supplied = request.headers.get("X-API-Key")
            org_id = None
            
            if supplied:
                # First try DB-backed API keys
                org_id = await _verify_api_key(supplied)
                
                # Fallback to tenant_api_keys only in local environment
                if org_id is None and settings.environment == Environment.LOCAL:
                    if supplied in settings.tenant_api_keys:
                        org_id = settings.tenant_api_keys[supplied]
            
            if org_id is None:
                exc = AuthenticationError("Missing or invalid API key")
                return JSONResponse(
                    status_code=exc.http_status, content={"error": exc.to_payload()}
                )
            
            # Bind org_id to request state for downstream use
            request.state.organization_id = org_id
        
        return await call_next(request)

    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next):
        """Sliding window rate limiting per API key (X-API-Key header)."""
        
        limiter = get_rate_limiter(request.app)
        if limiter is None:
            return await call_next(request)

        # Only rate limit /v1/* routes
        if not request.url.path.startswith("/v1"):
            return await call_next(request)

        api_key = request.headers.get("X-API-Key")
        if not api_key:
            # No API key - let the auth middleware handle it
            return await call_next(request)

        
        allowed, remaining = limiter.is_allowed(api_key)
        
        
        if not allowed:
            exc = RateLimitError("Rate limit exceeded")
            response = JSONResponse(
                status_code=exc.http_status,
                content={"error": {"code": "RATE_LIMITED", "message": exc.message}},
            )
            response.headers["X-RateLimit-Remaining"] = "0"
            response.headers["X-RateLimit-Limit"] = str(limiter.max_requests)
            response.headers["X-RateLimit-Reset"] = str(int(time.time() + limiter.window_seconds))
            return response

        response = await call_next(request)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Limit"] = str(limiter.max_requests)
        response.headers["X-RateLimit-Reset"] = str(int(time.time() + limiter.window_seconds))
        return response

    @app.exception_handler(BusinessOpsError)
    async def business_ops_error_handler(request: Request, exc: BusinessOpsError):
        logger.warning("request_failed", extra={"error_code": exc.code.value})
        return JSONResponse(status_code=exc.http_status, content={"error": exc.to_payload()})

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        # Sanitize errors: ctx may hold non-serializable objects (e.g. ValueError)
        # Sanitize errors: ctx may hold non-serializable objects (e.g. ValueError)
        safe_errors = []
        for e in exc.errors()[:10]:
            clean = {k: v for k, v in e.items() if k != "ctx"}
            ctx = e.get("ctx")
            if isinstance(ctx, dict):
                clean["ctx"] = {ck: str(cv) for ck, cv in ctx.items()}
            safe_errors.append(clean)
        err = ValidationError(
            "Request validation failed",
            details={"errors": safe_errors},
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