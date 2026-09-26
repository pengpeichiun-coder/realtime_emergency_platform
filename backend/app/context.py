"""Trusted server identity, never selected by request body, query or tenant headers."""
from dataclasses import dataclass
from typing import Literal
from app.observability import identity_var
from fastapi import HTTPException
from starlette.requests import HTTPConnection

Role = Literal['ADMIN', 'DISPATCHER', 'RESPONDER']

@dataclass(frozen=True)
class RequestContext:
    user_id: str
    organization_id: str
    role: Role

async def request_context(connection: HTTPConnection) -> RequestContext:
    authorization = connection.headers.get('authorization', '')
    if authorization:
        scheme, _, token = authorization.partition(' ')
        if scheme.lower() != 'bearer' or not token or connection.app.state.auth is None:
            raise HTTPException(401, 'Invalid authentication.')
        from app.auth.service import context_for
        user, _ = await connection.app.state.auth.identity(token)
        connection.state.user = user
        context = context_for(user)
        identity_var.set({"organization_id":context.organization_id,"user_id":context.user_id})
        return context
    context = connection.app.state.trusted_context
    if context is None:
        raise HTTPException(401, 'A trusted identity is required.')
    return context
