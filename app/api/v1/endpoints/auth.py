import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status, Request
from backend.app.core.config import settings
from backend.app.core.database import get_database
from backend.app.core.security import (
    verify_password,
    get_password_hash,
    hash_token,
    create_access_token,
    create_refresh_token,
    generate_secure_token,
)
from backend.app.core.deps import (
    get_current_user,
    get_current_user_and_session,
    log_audit_event,
)
from backend.app.schemas.auth import (
    LoginRequest,
    TokenResponse,
    RefreshTokenRequest,
    UserResponse,
    ActivateAccountRequest,
    ValidateTokenResponse,
    PasswordResetRequest,
    PasswordResetConfirm,
    AdminPasswordResetRequestBody,
    PasswordResetQueueItem,
    PasswordResetQueueResponse,
    AuthorizeResetRequest,
    RejectResetRequest,
)
from backend.app.services.email import send_password_reset_email
from backend.app.services.notification_service import (
    broadcast_notification,
    create_in_app_notification,
)

logger = logging.getLogger("estateflow.auth.routes")
router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login", response_model=TokenResponse, summary="Authenticate user & issue device session")
async def login(req: LoginRequest, request: Request):
    db = get_database()
    email_normalized = req.email.strip().lower()

    user = await db.users.find_one({"email_normalized": email_normalized})
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if user.get("account_status") != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Account is {user.get('account_status')}. Please check your invitation email or contact Admin.",
        )

    if not user.get("password_hash") or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # Create tracked device session (PRD Section 3.5)
    refresh_token = create_refresh_token()
    refresh_token_hash = hash_token(refresh_token)

    session_doc = {
        "user_id": user["_id"],
        "device_id": req.device_id,
        "platform": req.platform,
        "device_name": req.device_name,
        "refresh_token_hash": refresh_token_hash,
        "push_token": None,
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
        "status": "active",
        "created_at": datetime.now(timezone.utc),
        "last_activity_at": datetime.now(timezone.utc),
        "expires_at": datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    }

    session_res = await db.sessions.insert_one(session_doc)
    session_id = str(session_res.inserted_id)

    # Update user last login
    await db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {"last_login_at": datetime.now(timezone.utc)}}
    )

    access_token = create_access_token(
        user_id=str(user["_id"]),
        role=user["role"],
        session_id=session_id
    )

    await log_audit_event(
        actor_id=str(user["_id"]),
        actor_role=user["role"],
        event_type="auth.login",
        entity_type="session",
        entity_id=session_id,
        request=request,
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in_minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES,
        user=UserResponse(
            id=str(user["_id"]),
            full_name=user["full_name"],
            email=user["email"],
            mobile=user.get("mobile"),
            role=user["role"],
            account_status=user["account_status"],
            last_login_at=datetime.now(timezone.utc),
            created_at=user.get("created_at") or datetime.now(timezone.utc),
        ),
    )


@router.post("/refresh", response_model=TokenResponse, summary="Rotate refresh token with 7-day inactivity enforcement")
async def refresh_token(req: RefreshTokenRequest, request: Request):
    db = get_database()
    token_hash = hash_token(req.refresh_token)

    session = await db.sessions.find_one({"refresh_token_hash": token_hash, "device_id": req.device_id})
    if not session or session.get("status") != "active":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked refresh session. Please login again.",
        )

    # 7-day inactivity check
    last_act = session.get("last_activity_at")
    if last_act:
        if last_act.tzinfo is None:
            last_act = last_act.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - last_act > timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS):
            await db.sessions.update_one(
                {"_id": session["_id"]},
                {"$set": {"status": "expired", "expired_reason": "7_day_inactivity"}}
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session has expired due to 7 days of inactivity. Please log in again.",
            )

    user = await db.users.find_one({"_id": session["user_id"]})
    if not user or user.get("account_status") != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated or unavailable.",
        )

    # Rotate refresh token
    new_refresh_token = create_refresh_token()
    new_refresh_hash = hash_token(new_refresh_token)

    await db.sessions.update_one(
        {"_id": session["_id"]},
        {
            "$set": {
                "refresh_token_hash": new_refresh_hash,
                "last_activity_at": datetime.now(timezone.utc),
            }
        }
    )

    new_access_token = create_access_token(
        user_id=str(user["_id"]),
        role=user["role"],
        session_id=str(session["_id"])
    )

    return TokenResponse(
        access_token=new_access_token,
        refresh_token=new_refresh_token,
        expires_in_minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES,
        user=UserResponse(
            id=str(user["_id"]),
            full_name=user["full_name"],
            email=user["email"],
            mobile=user.get("mobile"),
            role=user["role"],
            account_status=user["account_status"],
            last_login_at=user.get("last_login_at"),
            created_at=user.get("created_at") or datetime.now(timezone.utc),
        ),
    )


@router.post("/logout", summary="End current device session")
async def logout(
    auth_data: dict = Depends(get_current_user_and_session),
    request: Request = None,
):
    db = get_database()
    session = auth_data["session"]
    user = auth_data["user"]

    await db.sessions.update_one(
        {"_id": session["_id"]},
        {"$set": {"status": "revoked", "revoked_at": datetime.now(timezone.utc)}}
    )

    await log_audit_event(
        actor_id=str(user["_id"]),
        actor_role=user["role"],
        event_type="auth.logout",
        entity_type="session",
        entity_id=str(session["_id"]),
        request=request,
    )

    return {"message": "Session ended successfully"}


@router.get("/invitation/{token}", response_model=ValidateTokenResponse, summary="Validate single-use invitation token")
async def validate_invitation(token: str):
    db = get_database()
    t_hash = hash_token(token)

    invitation = await db.invitations.find_one({"token_hash": t_hash})
    if not invitation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation link is invalid.")

    if invitation.get("is_used"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This invitation has already been accepted.")

    expiry = invitation.get("expires_at")
    if expiry:
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expiry:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This invitation link has expired.")

    user = await db.users.find_one({"_id": invitation["user_id"]})
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invited account not found.")

    return ValidateTokenResponse(
        valid=True,
        email=user["email"],
        full_name=user["full_name"],
        role=user["role"],
    )


@router.post("/activate", summary="Set password & activate account from single-use invitation")
async def activate_account(req: ActivateAccountRequest, request: Request):
    db = get_database()
    t_hash = hash_token(req.token)

    invitation = await db.invitations.find_one({"token_hash": t_hash})
    if not invitation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid activation link.")

    if invitation.get("is_used"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Activation token has already been used.")

    expiry = invitation.get("expires_at")
    if expiry and datetime.now(timezone.utc) > expiry.replace(tzinfo=timezone.utc):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Activation link has expired.")

    user = await db.users.find_one({"_id": invitation["user_id"]})
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User account not found.")

    # Hash password with Argon2id and activate user
    hashed_password = get_password_hash(req.password)
    now = datetime.now(timezone.utc)

    await db.users.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "password_hash": hashed_password,
                "account_status": "active",
                "activated_at": now,
                "updated_at": now,
            }
        }
    )

    # Invalidate token atomically (Single-use)
    await db.invitations.update_one(
        {"_id": invitation["_id"]},
        {"$set": {"is_used": True, "used_at": now}}
    )

    await log_audit_event(
        actor_id=str(user["_id"]),
        actor_role=user["role"],
        event_type="user.activated",
        entity_type="user",
        entity_id=str(user["_id"]),
        request=request,
    )

    return {"message": "Account successfully activated. You may now sign in."}


@router.post("/password-reset/request", summary="Request password reset link via Resend email")
async def request_password_reset(req: PasswordResetRequest, request: Request):
    db = get_database()
    email_norm = req.email.strip().lower()
    user = await db.users.find_one({"email_normalized": email_norm, "account_status": "active"})

    # Timing-safe neutral response if user not found
    if not user:
        return {"message": "If the account exists and is active, a password reset link has been dispatched."}

    reset_token = generate_secure_token()
    token_h = hash_token(reset_token)

    # Store reset request
    reset_doc = {
        "user_id": user["_id"],
        "token_hash": token_h,
        "is_used": False,
        "created_at": datetime.now(timezone.utc),
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=24),
    }
    await db.password_resets.insert_one(reset_doc)

    # Send reset link via Resend
    try:
        await send_password_reset_email(
            to_email=user["email"],
            recipient_name=user["full_name"],
            reset_token=reset_token,
        )
    except Exception as e:
        logger.error(f"Failed to deliver reset email: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send password reset email. Please contact support.",
        )

    await log_audit_event(
        actor_id=str(user["_id"]),
        actor_role=user["role"],
        event_type="auth.password_reset_requested",
        entity_type="user",
        entity_id=str(user["_id"]),
        request=request,
    )

    return {"message": "If the account exists and is active, a password reset link has been dispatched."}


@router.post("/password-reset/confirm", summary="Confirm reset token and set new password")
async def confirm_password_reset(req: PasswordResetConfirm, request: Request):
    db = get_database()
    t_hash = hash_token(req.token)

    reset_req = await db.password_resets.find_one({"token_hash": t_hash, "is_used": False})
    if not reset_req:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or already used password reset link.")

    expiry = reset_req.get("expires_at")
    if expiry and datetime.now(timezone.utc) > expiry.replace(tzinfo=timezone.utc):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password reset link has expired.")

    user = await db.users.find_one({"_id": reset_req["user_id"]})
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User account not found.")

    new_hash = get_password_hash(req.new_password)
    now = datetime.now(timezone.utc)

    # Update password
    await db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {"password_hash": new_hash, "updated_at": now}}
    )

    # Mark reset token used
    await db.password_resets.update_one(
        {"_id": reset_req["_id"]},
        {"$set": {"is_used": True, "used_at": now}}
    )

    # Revoke all other active device sessions for this user (PRD Section 3.4)
    await db.sessions.update_many(
        {"user_id": user["_id"], "status": "active"},
        {"$set": {"status": "revoked", "revoked_reason": "password_reset", "revoked_at": now}}
    )

    await log_audit_event(
        actor_id=str(user["_id"]),
        actor_role=user["role"],
        event_type="auth.password_reset_completed",
        entity_type="user",
        entity_id=str(user["_id"]),
        request=request,
    )

    return {"message": "Password updated successfully. All other active sessions have been signed out."}


@router.post("/request-admin-password-reset", summary="Executive requests password reset from Super Admin")
async def request_admin_password_reset(req: AdminPasswordResetRequestBody, request: Request):
    """
    Called from mobile app when executive forgot password.
    Directly creates an urgent notification and alert in the database for Super Admin.
    """
    db = get_database()
    email_norm = req.email.strip().lower()
    user = await db.users.find_one({"email_normalized": email_norm})
    now = datetime.now(timezone.utc)

    if user:
        client_ip = request.client.host if request.client else "127.0.0.1"
        device_sig = f"{req.device_model or 'Mobile App'} • {req.os_version or 'Mobile OS'} (App {req.app_version or 'v3.12.0'})"
        req_uuid = f"REQ-{uuid.uuid4().hex[:8].upper()}"
        reason_text = req.reason or "Passcode forgotten after 7-day field token expiration during client showing."

        await db.users.update_one(
            {"_id": user["_id"]},
            {
                "$set": {
                    "password_reset_requested": True,
                    "password_reset_requested_at": now,
                    "updated_at": now,
                }
            },
        )

        doc = {
            "req_id": req_uuid,
            "user_id": user["_id"],
            "email": user["email"],
            "full_name": user.get("full_name") or user.get("name") or "Staff Member",
            "mobile": user.get("mobile") or user.get("phone"),
            "role": user.get("role", "executive"),
            "cluster": user.get("cluster"),
            "reporting_manager": user.get("reporting_manager"),
            "reason": reason_text,
            "device_signature": device_sig,
            "client_ip": client_ip,
            "status": "pending",
            "priority": "High SLA Priority" if user.get("role") in ["admin", "branch_admin"] else "Standard SLA",
            "created_at": now,
            "resolved_at": None,
            "resolved_by": None,
            "resolution_action": None,
        }
        await db.password_reset_requests.insert_one(doc)

        await broadcast_notification(
            db=db,
            target_roles=["super_admin", "admin"],
            title=f"Password Reset Request: {user.get('full_name')}",
            body=f"Staff member {user.get('full_name')} ({user['email']}) requested a password reset: {reason_text}",
            notification_type="password_reset_requested",
            entity_type="user",
            entity_id=str(user["_id"]),
        )

        await log_audit_event(
            actor_id=str(user["_id"]),
            actor_role=user.get("role", "executive"),
            event_type="auth.executive_password_reset_requested",
            entity_type="user",
            entity_id=str(user["_id"]),
            request=request,
        )

    return {
        "detail": f"Password reset request submitted for {req.email}. Your Super Admin has been alerted in the Central Password Reset & Credential Recovery Queue."
    }


@router.get("/password-reset-queue", response_model=PasswordResetQueueResponse, summary="Central Password Reset Queue")
async def get_password_reset_queue(
    status: Optional[str] = None,
    role: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    if current_user.get("role") not in ["super_admin", "admin"]:
        raise HTTPException(status_code=403, detail="Forbidden. Super Admin or Admin access required.")

    db = get_database()
    query = {}
    if status and status != "all":
        query["status"] = status
    if role and role != "all":
        query["role"] = role

    cursor = db.password_reset_requests.find(query).sort("created_at", -1)
    docs = await cursor.to_list(100)

    pending_count = await db.password_reset_requests.count_documents({"status": "pending"})
    approved_count = await db.password_reset_requests.count_documents({"status": "approved"})
    rejected_count = await db.password_reset_requests.count_documents({"status": "rejected"})

    items = []
    for d in docs:
        items.append(
            PasswordResetQueueItem(
                id=str(d["_id"]),
                req_id=d.get("req_id") or f"REQ-{str(d['_id'])[:8].upper()}",
                user_id=str(d.get("user_id")),
                email=d.get("email"),
                full_name=d.get("full_name") or "Staff Member",
                mobile=d.get("mobile"),
                role=d.get("role", "executive"),
                cluster=d.get("cluster") or "Main Operations",
                reporting_manager=d.get("reporting_manager") or "Operations Admin",
                reason=d.get("reason") or "Password reset requested by user.",
                device_signature=d.get("device_signature") or "Mobile App",
                client_ip=d.get("client_ip") or "Origin IP Logged",
                status=d.get("status", "pending"),
                priority=d.get("priority") or ("High SLA Priority" if d.get("role") in ["admin", "branch_admin"] else "Standard SLA"),
                created_at=d.get("created_at") or datetime.now(timezone.utc),
                resolved_at=d.get("resolved_at"),
                resolved_by=d.get("resolved_by"),
                resolution_action=d.get("resolution_action"),
            )
        )

    # Fetch recent dispatches for the sidebar widget
    resolved_cursor = db.password_reset_requests.find({"status": {"$in": ["approved", "rejected"]}}).sort("resolved_at", -1).limit(6)
    resolved_docs = await resolved_cursor.to_list(6)
    recent_dispatches = []
    for r in resolved_docs:
        recent_dispatches.append({
            "name": r.get("full_name") or "Staff Member",
            "role": r.get("role", "executive").capitalize(),
            "action": r.get("resolution_action") or ("Approved" if r.get("status") == "approved" else "Rejected"),
            "status": "Verified" if r.get("status") == "approved" else "Declined",
            "resolved_at": (r.get("resolved_at") or r.get("created_at") or datetime.now(timezone.utc)).isoformat(),
        })

    return PasswordResetQueueResponse(
        items=items,
        pending_count=pending_count,
        approved_count=approved_count,
        rejected_count=rejected_count,
        recent_dispatches=recent_dispatches,
    )


@router.post("/password-reset-queue/{req_id}/authorize", summary="Authorize & issue temp/new password for reset request")
async def authorize_password_reset(
    req_id: str,
    body: AuthorizeResetRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    if current_user.get("role") not in ["super_admin", "admin"]:
        raise HTTPException(status_code=403, detail="Forbidden. Super Admin access required.")

    db = get_database()
    now = datetime.now(timezone.utc)

    reset_req = await db.password_reset_requests.find_one({"$or": [{"req_id": req_id}, {"_id": req_id}]})
    if not reset_req:
        try:
            reset_req = await db.password_reset_requests.find_one({"_id": ObjectId(req_id)})
        except Exception:
            pass

    if not reset_req:
        raise HTTPException(status_code=404, detail="Password reset request not found.")

    user = await db.users.find_one({"_id": reset_req["user_id"]})
    if not user:
        raise HTTPException(status_code=404, detail="User associated with request not found.")

    hashed = get_password_hash(body.new_password)
    await db.users.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "password_hash": hashed,
                "password_reset_requested": False,
                "updated_at": now,
            }
        },
    )

    await db.sessions.update_many(
        {"user_id": user["_id"], "status": "active"},
        {"$set": {"status": "revoked", "revoked_at": now, "revocation_reason": "password_reset_authorized"}},
    )

    await db.password_reset_requests.update_one(
        {"_id": reset_req["_id"]},
        {
            "$set": {
                "status": "approved",
                "resolved_at": now,
                "resolved_by": current_user.get("full_name") or current_user.get("email"),
                "resolution_action": "Temp Passcode Regenerated & Dispatched",
            }
        },
    )

    # Trigger push and in-app notification to the user's mobile device
    await create_in_app_notification(
        db=db,
        user_id=str(user["_id"]),
        title="Password Reset Approved",
        body=f"Your password reset request has been approved by {current_user.get('full_name') or 'Admin'}. Your temporary password has been set. Please login with your new credentials.",
        notification_type="password_reset_approved",
        entity_type="user",
        entity_id=str(user["_id"]),
    )

    await log_audit_event(
        actor_id=str(current_user["_id"]),
        actor_role=current_user.get("role", "super_admin"),
        event_type="auth.password_reset_authorized",
        entity_type="user",
        entity_id=str(user["_id"]),
        request=request,
    )

    return {"message": f"Credentials successfully authorized and set for {user.get('full_name')} ({user['email']})."}


@router.post("/password-reset-queue/{req_id}/reject", summary="Reject password reset request")
async def reject_password_reset(
    req_id: str,
    body: RejectResetRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    if current_user.get("role") not in ["super_admin", "admin"]:
        raise HTTPException(status_code=403, detail="Forbidden. Super Admin access required.")

    db = get_database()
    now = datetime.now(timezone.utc)

    reset_req = await db.password_reset_requests.find_one({"$or": [{"req_id": req_id}, {"_id": req_id}]})
    if not reset_req:
        try:
            reset_req = await db.password_reset_requests.find_one({"_id": ObjectId(req_id)})
        except Exception:
            pass

    if not reset_req:
        raise HTTPException(status_code=404, detail="Password reset request not found.")

    user_id = reset_req.get("user_id")
    if user_id:
        await db.users.update_one(
            {"_id": user_id},
            {"$set": {"password_reset_requested": False, "updated_at": now}},
        )

    await db.password_reset_requests.update_one(
        {"_id": reset_req["_id"]},
        {
            "$set": {
                "status": "rejected",
                "resolved_at": now,
                "resolved_by": current_user.get("full_name") or current_user.get("email"),
                "resolution_action": f"Rejected: {body.reason}",
            }
        },
    )

    # Trigger push and in-app notification to the user's mobile device
    if user_id:
        await create_in_app_notification(
            db=db,
            user_id=str(user_id),
            title="Password Reset Request Declined",
            body=f"Your password reset request was declined by {current_user.get('full_name') or 'Admin'}. Reason: {body.reason}",
            notification_type="password_reset_rejected",
            entity_type="user",
            entity_id=str(user_id),
        )

    await log_audit_event(
        actor_id=str(current_user["_id"]),
        actor_role=current_user.get("role", "super_admin"),
        event_type="auth.password_reset_rejected",
        entity_type="user",
        entity_id=str(user_id) if user_id else req_id,
        request=request,
    )

    return {"message": "Password reset request rejected."}


@router.get("/me", response_model=UserResponse, summary="Get current authenticated user profile")
async def get_me(user: dict = Depends(get_current_user)):
    return UserResponse(
        id=str(user["_id"]),
        full_name=user["full_name"],
        email=user["email"],
        mobile=user.get("mobile"),
        role=user["role"],
        account_status=user["account_status"],
        last_login_at=user.get("last_login_at"),
        created_at=user.get("created_at") or datetime.now(timezone.utc),
    )
