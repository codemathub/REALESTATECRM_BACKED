from datetime import datetime, timezone
from typing import List, Optional
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.app.core.deps import get_current_user
from backend.app.core.database import get_database
from backend.app.schemas.notification import (
    NotificationResponse,
    NotificationsListResponse,
    PushTokenRequest,
)

router = APIRouter()


@router.get("", response_model=NotificationsListResponse)
async def list_notifications(
    limit: int = Query(50, ge=1, le=100),
    unread_only: bool = False,
    current_user: dict = Depends(get_current_user),
):
    """List in-app notifications for the logged in user."""
    db = get_database()
    user_id_str = str(current_user["_id"])

    query: dict = {"user_id": user_id_str}
    if unread_only:
        query["is_read"] = False

    total = await db.notifications.count_documents({"user_id": user_id_str})
    unread = await db.notifications.count_documents({"user_id": user_id_str, "is_read": False})

    docs = await db.notifications.find(query).sort("created_at", -1).limit(limit).to_list(length=limit)

    items = [
        NotificationResponse(
            id=str(d["_id"]),
            user_id=d["user_id"],
            title=d["title"],
            body=d["body"],
            notification_type=d.get("notification_type", "system"),
            entity_type=d.get("entity_type"),
            entity_id=d.get("entity_id"),
            is_read=d.get("is_read", False),
            created_at=d.get("created_at") or datetime.now(timezone.utc),
        )
        for d in docs
    ]

    return NotificationsListResponse(
        items=items,
        total=total,
        unread_count=unread,
    )


@router.post("/{notification_id}/read", status_code=status.HTTP_200_OK)
async def mark_notification_as_read(
    notification_id: str,
    current_user: dict = Depends(get_current_user),
):
    db = get_database()
    user_id_str = str(current_user["_id"])

    query = {"_id": ObjectId(notification_id) if ObjectId.is_valid(notification_id) else None, "user_id": user_id_str}
    res = await db.notifications.update_one(query, {"$set": {"is_read": True}})
    if res.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found.")
    return {"message": "Notification marked as read."}


@router.post("/read-all", status_code=status.HTTP_200_OK)
async def mark_all_notifications_as_read(
    current_user: dict = Depends(get_current_user),
):
    db = get_database()
    user_id_str = str(current_user["_id"])
    await db.notifications.update_many({"user_id": user_id_str, "is_read": False}, {"$set": {"is_read": True}})
    return {"message": "All notifications marked as read.", "detail": "All notifications marked as read."}


@router.post("/push-token", status_code=status.HTTP_200_OK)
async def register_push_token(
    payload: PushTokenRequest,
    current_user: dict = Depends(get_current_user),
):
    """Register Expo push token for user device."""
    db = get_database()
    user_id_str = str(current_user["_id"])
    now = datetime.now(timezone.utc)

    token_val = payload.get_token()
    await db.push_tokens.update_one(
        {"user_id": user_id_str, "device_id": payload.device_id or "default-device"},
        {
            "$set": {
                "token": token_val,
                "platform": payload.platform,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )

    return {"message": "Push token registered successfully.", "detail": "Push token registered successfully"}
