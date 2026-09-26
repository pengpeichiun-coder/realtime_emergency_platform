from typing import Annotated
from fastapi import Depends, HTTPException, Request
from app.context import RequestContext, request_context

Context = Annotated[RequestContext, Depends(request_context)]


def require_roles(*roles):
    async def policy(context: Context):
        if context.role not in roles:
            raise HTTPException(403, "Your role does not permit this action.")
        return context

    return policy


operator = require_roles("ADMIN", "DISPATCHER")
admin = require_roles("ADMIN")


async def development_admin(
    request: Request, context: Annotated[RequestContext, Depends(admin)]
):
    settings = request.app.state.auth_settings
    if settings and settings.environment not in {"development", "test"}:
        raise HTTPException(403, "Demo reset is disabled in this environment.")
    return context
