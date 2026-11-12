"""Who is calling, and what role do they have."""
import jwt
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.errors import forbidden, unauthorized
from app.models.integration import ApiKey
from app.models.user import User, utcnow
from app.security import decode_access_token
from app.services.integration_security import hash_key


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise unauthorized("Missing bearer token.")
    try:
        claims = decode_access_token(token)
    except jwt.PyJWTError:
        raise unauthorized("Invalid or expired token.")
    user = db.get(User, claims.get("sub"))
    if user is None:
        raise unauthorized("Unknown user.")
    return user  # role is always read from the database, never trusted from the token


def require_roles(*roles: str):
    def dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise forbidden(f"This action requires one of these roles: {', '.join(roles)}.")
        return user

    return dep


def get_api_key(request: Request, db: Session = Depends(get_db)) -> ApiKey:
    """Service-to-service callers (a CMS, a publishing pipeline) send X-API-Key instead of a user token."""
    plain = request.headers.get("x-api-key", "").strip()
    if not plain:
        raise unauthorized("Missing X-API-Key header.")
    key = db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_key(plain)))
    if key is None or key.revoked_at is not None:
        raise unauthorized("Invalid or revoked API key.")
    key.last_used_at = utcnow()
    db.commit()
    return key


def require_scope(scope: str):
    def dep(key: ApiKey = Depends(get_api_key)) -> ApiKey:
        if scope not in (key.scopes or []):
            raise forbidden(f"This API key does not have the {scope} scope.")
        return key

    return dep
