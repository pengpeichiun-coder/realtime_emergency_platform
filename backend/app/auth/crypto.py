from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from uuid import uuid4
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import HTTPException

hasher = PasswordHasher()  # Maintained Argon2id defaults: 64 MiB, time cost 3.
dummy_hash = hasher.hash(token_urlsafe(32))


def verify_password(encoded, password):
    try:
        return hasher.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def token_hash(raw):
    return sha256(raw.encode()).hexdigest()


def access_token(user_id, settings):
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": user_id,
            "type": "access",
            "iat": now,
            "exp": now + timedelta(minutes=settings.access_minutes),
            "jti": uuid4().hex,
            "iss": "emergency-platform",
        },
        settings.secret,
        algorithm="HS256",
    )


def claims(raw, settings):
    try:
        value = jwt.decode(
            raw,
            settings.secret,
            algorithms=["HS256"],
            issuer="emergency-platform",
            options={"require": ["sub", "type", "iat", "exp", "jti", "iss"]},
        )
        if value["type"] != "access" or not value["sub"] or not value["jti"]:
            raise jwt.InvalidTokenError()
        return value
    except jwt.InvalidTokenError:
        raise HTTPException(
            401,
            "Invalid or expired access token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
