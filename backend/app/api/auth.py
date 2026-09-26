from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from app.auth.schemas import (
    Credentials,
    Register,
    TokenResponse,
    UserResponse,
    UserCreate,
    RoleUpdate,
    ActiveUpdate,
)
from app.auth.policies import Context, admin
from app.context import RequestContext
from app.observability import AUTH_LOGIN_ATTEMPTS, AUTH_REFRESH_FAILURES
from app.security.rate_limit import enforce_auth_rate_limit

router = APIRouter(prefix="/api", tags=["identity"])
COOKIE = "emergency_refresh"
COOKIE_PATH = "/api/auth"


async def browser_origin(request: Request):
    origin = request.headers.get("origin")
    if origin and origin not in request.app.state.allowed_origins:
        raise HTTPException(403, "Untrusted browser origin.")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Cross-site session requests are not permitted.")


async def auth_service(request: Request):
    service = request.app.state.auth
    if service is None:
        raise HTTPException(503, "Authentication requires PostgreSQL.")
    return service


Auth = Annotated[object, Depends(auth_service)]
Admin = Annotated[RequestContext, Depends(admin)]


def session_response(request, response, result, raw):
    settings = request.app.state.auth_settings
    response.set_cookie(
        COOKIE,
        raw,
        max_age=settings.refresh_days * 86400,
        httponly=True,
        secure=settings.environment == "production",
        samesite="strict",
        path=COOKIE_PATH,
    )
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post(
    "/auth/register",
    response_model=TokenResponse,
    status_code=201,
    dependencies=[Depends(browser_origin)],
)
async def register(body: Register, request: Request, response: Response, service: Auth):
    await enforce_auth_rate_limit(request, "register", str(body.email))
    return session_response(request, response, *(await service.register(body)))


@router.post(
    "/auth/login", response_model=TokenResponse, dependencies=[Depends(browser_origin)]
)
async def login(body: Credentials, request: Request, response: Response, service: Auth):
    await enforce_auth_rate_limit(request, "login", str(body.email))
    try:
        result = await service.login(body)
    except HTTPException as exc:
        if exc.status_code == 401:
            AUTH_LOGIN_ATTEMPTS.labels("failure").inc()
        raise
    AUTH_LOGIN_ATTEMPTS.labels("success").inc()
    return session_response(request, response, *result)


@router.post(
    "/auth/refresh",
    response_model=TokenResponse,
    dependencies=[Depends(browser_origin)],
)
async def refresh(request: Request, response: Response, service: Auth):
    raw = request.cookies.get(COOKIE)
    await enforce_auth_rate_limit(request, "refresh", raw)
    try:
        result = await service.refresh(raw)
    except HTTPException as exc:
        if exc.status_code == 401:
            AUTH_REFRESH_FAILURES.inc()
        raise
    return session_response(request, response, *result)


@router.post("/auth/logout", status_code=204, dependencies=[Depends(browser_origin)])
async def logout(request: Request, response: Response, service: Auth):
    await service.logout(request.cookies.get(COOKIE))
    response.delete_cookie(
        COOKIE,
        path=COOKIE_PATH,
        httponly=True,
        samesite="strict",
        secure=request.app.state.auth_settings.environment == "production",
    )
    response.headers["Cache-Control"] = "no-store"


@router.get("/auth/me", response_model=UserResponse)
async def me(request: Request, context: Context, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return UserResponse.model_validate(request.state.user).model_copy(
        update={
            "can_reset_demo": context.role == "ADMIN"
            and request.app.state.auth_settings.environment in {"development", "test"}
        }
    )


@router.get("/users", response_model=list[UserResponse])
async def users(service: Auth, context: Admin):
    return await service.list_users(context)


@router.post("/users", response_model=UserResponse, status_code=201)
async def create_user(body: UserCreate, service: Auth, context: Admin):
    return await service.create_user(context, body)


async def close_user_sockets(request, user):
    service = request.app.state.tenants.services.get(user.organization_id)
    if service:
        service.connections.close_user(user.id)


@router.patch("/users/{user_id}/role", response_model=UserResponse)
async def role(
    user_id: str, body: RoleUpdate, request: Request, service: Auth, context: Admin
):
    user = await service.change_user(context, user_id, role=body.role)
    await close_user_sockets(request, user)
    return user


@router.patch("/users/{user_id}/active", response_model=UserResponse)
async def active(
    user_id: str, body: ActiveUpdate, request: Request, service: Auth, context: Admin
):
    user = await service.change_user(context, user_id, active=body.is_active)
    await close_user_sockets(request, user)
    return user
