import logging
from datetime import datetime, timezone, timedelta
from typing import List
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status, Request
from backend.app.core.database import get_database
from backend.app.core.security import generate_secure_token, hash_token, get_password_hash
from backend.app.core.deps import get_current_user, require_roles, log_audit_event
from backend.app.schemas.auth import (
    InviteUserRequest,
    InviteUserResponse,
    DirectUserCreateRequest,
    AdminResetPasswordRequest,
    UserResponse,
    SessionResponse,
)
from backend.app.services.email import send_invitation_email
from backend.app.services.notification_service import create_in_app_notification

logger = logging.getLogger("estateflow.users.routes")
router = APIRouter(prefix="/users", tags=["User Management"])


@router.post("/admin", response_model=InviteUserResponse, summary="Super Admin invites new Admin account via Resend")
async def invite_admin(
    req: InviteUserRequest,
    request: Request,
    current_user: dict = Depends(require_roles(["super_admin"])),
):
    if req.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This endpoint only provisions Admin role accounts",
        )
    return await _process_user_invitation(req, "admin", current_user, request)


@router.post("/executive", response_model=InviteUserResponse, summary="Admin invites new Executive account via Resend")
async def invite_executive(
    req: InviteUserRequest,
    request: Request,
    current_user: dict = Depends(require_roles(["super_admin", "admin"])),
):
    if req.role != "executive":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This endpoint only provisions Executive role accounts",
        )
    return await _process_user_invitation(req, "executive", current_user, request)


@router.post("/create", response_model=UserResponse, status_code=status.HTTP_201_CREATED, summary="Direct User Creation with credentials (no invite link needed)")
async def create_user_direct(
    req: DirectUserCreateRequest,
    request: Request,
    current_user: dict = Depends(require_roles(["super_admin", "admin"])),
):
    """
    Direct account creation:
    - Super Admin can create Admin and Executive accounts with initial credentials.
    - Admin can ONLY create Executive accounts with initial credentials.
    - Super Admin cannot be created via this endpoint.
    - Sets account_status='active' immediately so the employee can login directly in the mobile app.
    """
    db = get_database()
    email_norm = req.email.strip().lower()

    if req.role == "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super Admin accounts cannot be provisioned via user creation.",
        )

    if current_user.get("role") != "super_admin":
        if req.role != "executive":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admins are strictly restricted to provisioning Executive accounts. Only Super Admin can create Admin accounts.",
            )
    else:
        if req.role not in ["admin", "executive"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Role must be either 'admin' or 'executive'.",
            )

    existing = await db.users.find_one({"email_normalized": email_norm})
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An account with email '{req.email}' already exists.",
        )

    now = datetime.now(timezone.utc)
    hashed_pwd = get_password_hash(req.password)

    user_doc = {
        "full_name": req.full_name,
        "email": req.email,
        "email_normalized": email_norm,
        "mobile": req.mobile,
        "role": req.role,
        "account_status": "active",
        "password_hash": hashed_pwd,
        "created_by": current_user["_id"],
        "created_at": now,
        "updated_at": now,
        "last_login_at": None,
    }

    res = await db.users.insert_one(user_doc)
    user_doc["_id"] = res.inserted_id

    await log_audit_event(
        actor_id=str(current_user["_id"]),
        actor_role=current_user["role"],
        event_type="user.created_direct",
        entity_type="user",
        entity_id=str(user_doc["_id"]),
        diff={"email": req.email, "role": req.role, "status": "active"},
        request=request,
    )

    return UserResponse(
        id=str(user_doc["_id"]),
        full_name=user_doc["full_name"],
        email=user_doc["email"],
        mobile=user_doc.get("mobile"),
        role=user_doc["role"],
        account_status="active",
        created_at=now,
    )


async def _process_user_invitation(
    req: InviteUserRequest,
    assigned_role: str,
    creator_user: dict,
    request: Request,
) -> InviteUserResponse:
    db = get_database()
    email_norm = req.email.strip().lower()

    # Check for existing email collision
    existing = await db.users.find_one({"email_normalized": email_norm})
    if existing:
        if existing.get("account_status") == "active":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"An active user with email {req.email} already exists.",
            )
        elif existing.get("account_status") == "deactivated":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"User {req.email} was deactivated. Super Admin must re-activate existing account.",
            )

    activation_token = generate_secure_token()
    token_h = hash_token(activation_token)
    now = datetime.now(timezone.utc)
    expiry = now + timedelta(hours=48)

    user_doc = {
        "full_name": req.full_name,
        "email": req.email,
        "email_normalized": email_norm,
        "mobile": req.mobile,
        "role": assigned_role,
        "account_status": "invited",
        "password_hash": None,
        "created_by": creator_user["_id"],
        "invited_at": now,
        "activated_at": None,
        "last_login_at": None,
        "created_at": now,
        "updated_at": now,
    }

    user_res = await db.users.insert_one(user_doc)
    user_id = user_res.inserted_id

    # Store invitation record
    invitation_doc = {
        "user_id": user_id,
        "created_by": creator_user["_id"],
        "token_hash": token_h,
        "expires_at": expiry,
        "is_used": False,
        "used_at": None,
        "created_at": now,
    }
    await db.invitations.insert_one(invitation_doc)

    # Deliver invitation email via Resend
    provider_id = None
    delivery_status = "sent"
    try:
        email_result = await send_invitation_email(
            to_email=req.email,
            recipient_name=req.full_name,
            role=assigned_role,
            activation_token=activation_token,
        )
        provider_id = email_result.get("id")
    except Exception as e:
        logger.error(f"Resend email dispatch error: {e}")
        delivery_status = "failed_to_deliver"

    await log_audit_event(
        actor_id=str(creator_user["_id"]),
        actor_role=creator_user["role"],
        event_type="user.invited",
        entity_type="user",
        entity_id=str(user_id),
        diff={"email": req.email, "role": assigned_role, "delivery_status": delivery_status},
        request=request,
    )

    return InviteUserResponse(
        message=f"Invitation successfully dispatched to {req.email}.",
        user_id=str(user_id),
        email=req.email,
        role=assigned_role,
        delivery_status=delivery_status,
        provider_message_id=provider_id,
    )


@router.get("", response_model=List[UserResponse], summary="List users (role-filtered)")
async def list_users(
    role: str = None,
    current_user: dict = Depends(require_roles(["super_admin", "admin"])),
):
    db = get_database()
    query = {}

    # Admins can only see Executive accounts and other Admins
    if current_user["role"] == "admin":
        query["role"] = {"$in": ["executive", "admin"]}
    elif role:
        query["role"] = role

    cursor = db.users.find(query).sort("created_at", -1)
    users = await cursor.to_list(length=200)

    return [
        UserResponse(
            id=str(u["_id"]),
            full_name=u.get("full_name") or u.get("email", "User"),
            email=u.get("email", ""),
            mobile=u.get("mobile"),
            role=u.get("role", "executive"),
            account_status=u.get("account_status", "active"),
            password_reset_requested=u.get("password_reset_requested", False),
            last_login_at=u.get("last_login_at"),
            created_at=u.get("created_at") or u.get("invited_at") or u.get("activated_at") or datetime.now(timezone.utc),
        )
        for u in users
    ]


@router.post("/{user_id}/deactivate", summary="Super Admin only: Deactivate user account")
async def deactivate_user(
    user_id: str,
    request: Request,
    current_user: dict = Depends(require_roles(["super_admin"])),
):
    db = get_database()
    try:
        target_obj_id = ObjectId(user_id)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user ID format")

    target = await db.users.find_one({"_id": target_obj_id})
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User account not found")

    if target.get("role") == "super_admin":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot deactivate the Super Admin account")

    now = datetime.now(timezone.utc)

    # Update account status
    await db.users.update_one(
        {"_id": target_obj_id},
        {"$set": {"account_status": "deactivated", "updated_at": now}}
    )

    # Immediately revoke all active sessions for this user
    await db.sessions.update_many(
        {"user_id": target_obj_id, "status": "active"},
        {"$set": {"status": "revoked", "revoked_reason": "account_deactivated", "revoked_at": now}}
    )

    await log_audit_event(
        actor_id=str(current_user["_id"]),
        actor_role=current_user["role"],
        event_type="user.deactivated",
        entity_type="user",
        entity_id=user_id,
        diff={"email": target["email"], "previous_status": target.get("account_status")},
        request=request,
    )

    return {"message": f"User account {target['email']} has been deactivated and all active sessions revoked."}


@router.post("/{user_id}/reactivate", summary="Super Admin only: Reactivate user account")
async def reactivate_user(
    user_id: str,
    request: Request,
    current_user: dict = Depends(require_roles(["super_admin"])),
):
    db = get_database()
    try:
        target_obj_id = ObjectId(user_id)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user ID format")

    target = await db.users.find_one({"_id": target_obj_id})
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User account not found")

    now = datetime.now(timezone.utc)
    await db.users.update_one(
        {"_id": target_obj_id},
        {"$set": {"account_status": "active", "updated_at": now}}
    )

    await log_audit_event(
        actor_id=str(current_user["_id"]),
        actor_role=current_user["role"],
        event_type="user.reactivated",
        entity_type="user",
        entity_id=user_id,
        diff={"email": target["email"], "previous_status": target.get("account_status")},
        request=request,
    )

    return {"message": f"User account {target['email']} has been reactivated."}


@router.delete("/{user_id}", summary="Super Admin only: Permanently delete user account")
async def delete_user(
    user_id: str,
    request: Request,
    current_user: dict = Depends(require_roles(["super_admin"])),
):
    db = get_database()
    try:
        target_obj_id = ObjectId(user_id)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user ID format")

    target = await db.users.find_one({"_id": target_obj_id})
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User account not found")

    if target.get("role") == "super_admin":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete the Super Admin account")

    # Clean up associated user records
    await db.sessions.delete_many({"user_id": target_obj_id})
    await db.push_tokens.delete_many({"user_id": str(target_obj_id)})
    await db.password_reset_requests.delete_many({"user_id": target_obj_id})
    await db.users.delete_one({"_id": target_obj_id})

    await log_audit_event(
        actor_id=str(current_user["_id"]),
        actor_role=current_user["role"],
        event_type="user.deleted",
        entity_type="user",
        entity_id=user_id,
        diff={"email": target["email"], "role": target.get("role")},
        request=request,
    )

    return {"message": f"User account {target['email']} has been permanently deleted."}


@router.post("/{user_id}/reset-password", summary="Super Admin only: Reset user password directly")
async def super_admin_reset_password(
    user_id: str,
    req: AdminResetPasswordRequest,
    request: Request,
    current_user: dict = Depends(require_roles(["super_admin"])),
):
    """
    Super Admin directly overrides and sets a new password for any user account.
    Immediately revokes all active device sessions so the user must authenticate with the new password.
    """
    db = get_database()
    try:
        target_obj_id = ObjectId(user_id)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user ID format")

    target = await db.users.find_one({"_id": target_obj_id})
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User account not found")

    now = datetime.now(timezone.utc)
    hashed_pwd = get_password_hash(req.new_password)

    # Update password and ensure active status
    await db.users.update_one(
        {"_id": target_obj_id},
        {
            "$set": {
                "password_hash": hashed_pwd,
                "account_status": "active",
                "password_reset_requested": False,
                "updated_at": now,
            }
        },
    )

    # Resolve pending reset requests if any
    await db.password_reset_requests.update_many(
        {"user_id": target_obj_id, "status": "pending"},
        {"$set": {"status": "resolved", "resolved_at": now, "resolved_by": str(current_user["_id"])}},
    )

    # Revoke all active sessions for this user
    await db.sessions.update_many(
        {"user_id": target_obj_id, "status": "active"},
        {"$set": {"status": "revoked", "revoked_reason": "password_reset_by_admin", "revoked_at": now}},
    )

    # Trigger push and in-app notification to the user's mobile device
    await create_in_app_notification(
        db=db,
        user_id=str(target_obj_id),
        title="Password Updated by Super Admin",
        body="Your account password was updated by the Super Admin. Please use your new credentials to sign in.",
        notification_type="password_reset_by_admin",
        entity_type="user",
        entity_id=str(target_obj_id),
    )

    await log_audit_event(
        actor_id=str(current_user["_id"]),
        actor_role=current_user["role"],
        event_type="user.password_reset_by_admin",
        entity_type="user",
        entity_id=str(target_obj_id),
        diff={"email": target["email"]},
        request=request,
    )

    return {"message": f"Password for {target['email']} successfully updated and all active sessions revoked."}


@router.get("/{user_id}/sessions", response_model=List[SessionResponse], summary="List active device sessions for user")
async def list_user_sessions(
    user_id: str,
    current_user: dict = Depends(require_roles(["super_admin", "admin"])),
):
    db = get_database()
    try:
        target_obj_id = ObjectId(user_id)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user ID")

    # If Admin, verify target is not another Admin or Super Admin
    if current_user["role"] == "admin":
        target = await db.users.find_one({"_id": target_obj_id})
        if target and target.get("role") != "executive":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admins can only inspect Executive sessions")

    cursor = db.sessions.find({"user_id": target_obj_id}).sort("last_activity_at", -1)
    sessions = await cursor.to_list(length=50)

    return [
        SessionResponse(
            id=str(s["_id"]),
            device_id=s["device_id"],
            platform=s.get("platform", "unknown"),
            device_name=s.get("device_name"),
            ip_address=s.get("ip_address"),
            created_at=s.get("created_at") or datetime.now(timezone.utc),
            last_activity_at=s.get("last_activity_at") or datetime.now(timezone.utc),
            status=s.get("status", "active"),
        )
        for s in sessions
    ]


@router.delete("/{user_id}/sessions/{session_id}", summary="Revoke a specific device session")
async def revoke_user_session(
    user_id: str,
    session_id: str,
    request: Request,
    current_user: dict = Depends(require_roles(["super_admin", "admin"])),
):
    db = get_database()
    try:
        s_obj_id = ObjectId(session_id)
        u_obj_id = ObjectId(user_id)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid ID format")

    if current_user["role"] == "admin":
        target = await db.users.find_one({"_id": u_obj_id})
        if target and target.get("role") != "executive":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admins can only revoke Executive sessions")

    now = datetime.now(timezone.utc)
    res = await db.sessions.update_one(
        {"_id": s_obj_id, "user_id": u_obj_id},
        {"$set": {"status": "revoked", "revoked_reason": "admin_revocation", "revoked_at": now}}
    )

    if res.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    await log_audit_event(
        actor_id=str(current_user["_id"]),
        actor_role=current_user["role"],
        event_type="session.revoked",
        entity_type="session",
        entity_id=session_id,
        request=request,
    )

    return {"message": "Session revoked successfully"}
