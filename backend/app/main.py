"""Application factory: each app owns its repository, engine, and realtime stream."""

import logging
import os
import secrets
from pathlib import Path
from dotenv import load_dotenv
import asyncio
from sqlalchemy import text
from redis.asyncio import Redis
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse, Response
from app.observability import configure_logging, RequestObservability, metrics
from starlette.exceptions import HTTPException

from app.auth.config import AuthSettings
from app.auth.service import AuthService
from app.api.auth import router as auth_router
from app.api.webhooks import router as webhooks_router
from app.api.ai import router as ai_router
from app.api.jobs import router as jobs_router
from app.context import RequestContext
from app.services.tenants import TenantServices
from app.api.routes import router
from app.api.realtime import router as realtime_router
from app.domain.transitions import DomainError
from app.repositories.base import Repository
from app.services.simulator import SimulationConfig
from app.security.middleware import (
    RequestBodyLimit,
    SecurityHeaders,
    TrustedHostPolicy,
    allowed_hostnames,
    trusted_proxy_networks,
)
from app.security.rate_limit import AuthRateLimiter

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
logger = logging.getLogger(__name__)


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"error": {"code": code, "message": message}}
    )


def create_app(
    *,
    repository: Repository | None = None,
    database_url: str | None = None,
    trusted_context: RequestContext | None = None,
    auth_settings: AuthSettings | None = None,
    realtime_enabled: bool = False,
    simulation_enabled: bool = True,
    dispatch_delay: float = 3.0,
    travel_duration: float = 16.0,
    tick_interval: float = 0.2,
) -> FastAPI:
    config = (
        SimulationConfig(dispatch_delay, travel_duration, tick_interval)
        if simulation_enabled
        else None
    )
    tenants = TenantServices(database_url, config, realtime_enabled)
    # Explicit repository injection is reserved for isolated tests, never HTTP input.
    service = tenants.build(repository) if repository is not None else None
    if repository is not None and trusted_context is None:
        trusted_context = RequestContext(
            "test-user",
            getattr(repository, "organization_id", "test-organization"),
            "ADMIN",
        )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if realtime_enabled:
            configure_logging()
        logging.basicConfig(
            level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
        )
        try:
            if service:
                await repository.open()
                await service.resume()
            else:
                await tenants.open()
                _app.state.auth_settings = auth_settings or AuthSettings.load()
                _app.state.auth = AuthService(
                    tenants.database, _app.state.auth_settings
                )
                _app.state.auth_rate_limiter = AuthRateLimiter(
                    os.environ.get("REDIS_URL"),
                    pepper=_app.state.auth_settings.secret,
                )
            yield
        finally:
            if service:
                try:
                    await service.shutdown()
                finally:
                    await repository.close()
            else:
                try:
                    await tenants.close()
                finally:
                    if _app.state.auth_rate_limiter is not None:
                        await _app.state.auth_rate_limiter.close()

    app = FastAPI(
        title="Emergency Response Platform API",
        version="1.0.0",
        docs_url=None,
        lifespan=lifespan,
    )
    app.state.auth_settings = auth_settings
    app.state.auth = None
    app.state.tenants = tenants
    app.state.trusted_context = trusted_context
    app.state.service = service
    app.state.connections = service.connections if service else None
    app.state.allowed_origins = os.environ.get(
        "ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    app.state.allowed_origins = [
        origin.strip() for origin in app.state.allowed_origins if origin.strip()
    ]
    if os.environ.get("APP_ENV", "production") == "production" and (
        not app.state.allowed_origins
        or any(not origin.startswith("https://") for origin in app.state.allowed_origins)
    ):
        raise ValueError("Production ALLOWED_ORIGINS must contain HTTPS origins")
    app.state.allowed_hosts = allowed_hostnames()
    app.state.trusted_proxy_networks = trusted_proxy_networks()
    app.state.auth_rate_limiter = None
    app.add_middleware(RequestObservability)
    app.add_middleware(RequestBodyLimit)
    app.add_middleware(TrustedHostPolicy, allowlist=app.state.allowed_hosts)
    app.add_middleware(
        SecurityHeaders,
        hsts=(
            os.environ.get("APP_ENV", "production") == "production"
            and os.environ.get("ENABLE_HSTS", "false").lower() == "true"
        ),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app.state.allowed_origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        allow_credentials=True,
    )

    @app.exception_handler(DomainError)
    async def domain_error(_request: Request, exc: DomainError):
        return error_response(exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in exc.errors()
        )
        return error_response(422, "VALIDATION_ERROR", details)

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        response = error_response(exc.status_code, "HTTP_ERROR", str(exc.detail))
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(Exception)
    async def unexpected_error(_request: Request, exc: Exception):
        logger.error(
            "Unhandled API error", extra={"error_type": type(exc).__name__}
        )
        response = error_response(
            500, "INTERNAL_ERROR", "An unexpected server error occurred."
        )
        response.headers["X-Request-ID"] = getattr(
            _request.state, "request_id", "unavailable"
        )
        return response

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/docs", include_in_schema=False)
    def swagger_docs() -> HTMLResponse:
        """Serve Swagger UI with a per-response nonce for its inline bootstrap."""
        nonce = secrets.token_urlsafe(24)
        docs = get_swagger_ui_html(
            openapi_url=app.openapi_url or "/openapi.json",
            title=f"{app.title} - Swagger UI",
        )
        html = docs.body.decode("utf-8")
        marker = "<script>\n    const ui = SwaggerUIBundle"
        if marker not in html:
            raise RuntimeError("Could not apply a nonce to the Swagger UI bootstrap")
        html = html.replace(
            marker,
            f'<script nonce="{nonce}">\n    const ui = SwaggerUIBundle',
            1,
        )
        response = HTMLResponse(html)
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; "
            f"script-src 'self' https://cdn.jsdelivr.net 'nonce-{nonce}'; "
            "style-src 'self' https://cdn.jsdelivr.net; "
            "style-src-attr 'unsafe-inline'; "
            "img-src 'self' data: https://fastapi.tiangolo.com; "
            "font-src 'self' data: https://cdn.jsdelivr.net; "
            "connect-src 'self'; base-uri 'self'; object-src 'none'; "
            "form-action 'none'; frame-ancestors 'none'"
        )
        return response

    @app.get("/health/live", tags=["health"])
    async def liveness():
        return {"status": "ok"}

    @app.get("/metrics", include_in_schema=False)
    async def prometheus_metrics(request: Request):
        # Global metrics can reveal aggregate business activity; explicit token only.
        import hmac

        secret = os.environ.get("METRICS_TOKEN", "")
        if not secret or not hmac.compare_digest(
            request.headers.get("authorization", ""), "Bearer " + secret
        ):
            return error_response(404, "NOT_FOUND", "Not found.")
        try:
            async with asyncio.timeout(3):
                return Response(
                    await metrics(tenants.database),
                    media_type="text/plain; version=0.0.4",
                )
        except Exception:
            return error_response(503, "NOT_READY", "Metrics unavailable.")

    @app.get("/health/ready", tags=["health"])
    async def readiness():
        if tenants.database is None:
            return error_response(503, "NOT_READY", "Dependencies unavailable.")
        redis = Redis.from_url(
            os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"),
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        try:
            async with asyncio.timeout(3):
                async with tenants.database.sessions() as session:
                    await session.execute(text("SELECT 1"))
                await redis.ping()
                if tenants.event_bus and not tenants.event_bus.connected.is_set():
                    raise RuntimeError("Realtime subscription unavailable")
            return {"status": "ready"}
        except Exception:
            return error_response(503, "NOT_READY", "Dependencies unavailable.")
        finally:
            await redis.aclose()

    app.include_router(webhooks_router)
    app.include_router(ai_router)
    app.include_router(jobs_router)
    app.include_router(auth_router)
    app.include_router(router)
    app.include_router(realtime_router)
    return app


app = create_app(realtime_enabled=True)
