import logging
from datetime import datetime, timezone, timedelta
from typing import List
from bson import ObjectId
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from backend.app.core.config import settings
from backend.app.core.database import get_database

logger = logging.getLogger("estateflow.auth")
security_bearer = HTTPBearer(auto_error=True)


async def get_current_user_and_session(
    credentials: HTTPAuthorizationCredentials = Depends(security_bearer),
) -> dict:
    token = credentials.credentials
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials or token expired",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id: str = payload.get("sub")
        session_id: str = payload.get("session_id")
        token_type: str = payload.get("type")
        if user_id is None or session_id is None or token_type != "access":
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    db = get_database()

    # Verify user exists and is active
    try:
        user_obj_id = ObjectId(user_id)
        session_obj_id = ObjectId(session_id)
    except Exception:
        raise credentials_exception

    user = await db.users.find_one({"_id": user_obj_id})
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account no longer exists",
        )

    if user.get("account_status") != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Account is {user.get('account_status')}. Access revoked.",
        )

    # Verify session exists and is active
    session = await db.sessions.find_one({"_id": session_obj_id})
    if not session or session.get("status") != "active":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has been revoked or expired. Please sign in again.",
        )

    # Authoritative 7-Day Inactivity Check (PRD Section 3.5 & 12.1)
    last_activity = session.get("last_activity_at")
    if last_activity:
        if last_activity.tzinfo is None:
            last_activity = last_activity.replace(tzinfo=timezone.utc)
        time_since_activity = datetime.now(timezone.utc) - last_activity
        if time_since_activity > timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS):
            # Expire session on backend
            await db.sessions.update_one(
                {"_id": session_obj_id},
                {"$set": {"status": "expired", "expired_reason": "7_day_inactivity"}}
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session expired due to 7 days of inactivity. Please sign in again.",
            )

    # Touch last_activity_at
    await db.sessions.update_one(
        {"_id": session_obj_id},
        {"$set": {"last_activity_at": datetime.now(timezone.utc)}}
    )

    return {"user": user, "session": session}


async def get_current_user(data: dict = Depends(get_current_user_and_session)) -> dict:
    return data["user"]


def require_roles(allowed_roles: List[str]):
    """Role-based authorization guard enforced at FastAPI route level."""
    async def role_checker(user: dict = Depends(get_current_user)) -> dict:
        user_role = user.get("role")
        if user_role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Operation not permitted. Required role: {allowed_roles}. Current role: {user_role}",
            )
        return user
    return role_checker


require_admin = require_roles(["admin", "super_admin"])
require_super_admin = require_roles(["super_admin"])


async def log_audit_event(
    actor_id: str | None,
    actor_role: str,
    event_type: str,
    entity_type: str,
    entity_id: str,
    diff: dict | None = None,
    request: Request | None = None,
    status_code: str = "success",
):
    """Writes an immutable, append-only record to activity_events."""
    db = get_database()
    event_doc = {
        "actor_id": ObjectId(actor_id) if actor_id else None,
        "actor_role": actor_role,
        "event_type": event_type,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "diff": diff,
        "ip_address": request.client.host if request and request.client else None,
        "status": status_code,
        "created_at": datetime.now(timezone.utc),
    }
    await db.activity_events.insert_one(event_doc)
