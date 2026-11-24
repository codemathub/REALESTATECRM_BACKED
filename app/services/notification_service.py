import logging
from datetime import datetime, timezone
from typing import List, Optional
from bson import ObjectId
import httpx
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger("estateflow.notifications")
EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


async def create_in_app_notification(
    db: AsyncIOMotorDatabase,
    user_id: str,
    title: str,
    body: str,
    notification_type: str,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
) -> dict:
    """Insert notification into MongoDB and trigger push dispatch if tokens exist."""
    now = datetime.now(timezone.utc)
    doc = {
        "user_id": str(user_id),
        "title": title,
        "body": body,
        "notification_type": notification_type,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "is_read": False,
        "created_at": now,
    }
    result = await db.notifications.insert_one(doc)
    doc["_id"] = result.inserted_id

    # Lookup registered push tokens for user (support both string and ObjectId)
    tokens_cursor = db.push_tokens.find({
        "$or": [
            {"user_id": str(user_id)},
            {"user_id": ObjectId(user_id) if ObjectId.is_valid(user_id) else None},
        ]
    })
    tokens_docs = await tokens_cursor.to_list(length=10)
    tokens = [t["token"] for t in tokens_docs if t.get("token")]

    if tokens:
        await send_expo_push_notifications(
            tokens=tokens,
            title=title,
            body=body,
            data={"notification_type": notification_type, "entity_type": entity_type, "entity_id": entity_id},
        )

    return doc


async def broadcast_notification(
    db: AsyncIOMotorDatabase,
    target_roles: List[str],
    title: str,
    body: str,
    notification_type: str,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
):
    """Broadcast notification to all active users with specified roles."""
    query = {"account_status": "active"}
    if "all" not in target_roles:
        query["role"] = {"$in": target_roles}

    users_cursor = db.users.find(query, {"_id": 1})
    users = await users_cursor.to_list(length=500)

    for u in users:
        await create_in_app_notification(
            db=db,
            user_id=str(u["_id"]),
            title=title,
            body=body,
            notification_type=notification_type,
            entity_type=entity_type,
            entity_id=entity_id,
        )


async def send_expo_push_notifications(
    tokens: List[str],
    title: str,
    body: str,
    data: Optional[dict] = None,
):
    """
    Deliver real push notifications via Expo Push API.
    Configured with high priority, default sound, and 'default' channel for Android Heads-Up display.
    """
    messages = [
        {
            "to": t,
            "sound": "default",
            "title": title,
            "body": body,
            "channelId": "default",
            "priority": "high",
            "_displayInForeground": True,
            "data": data or {},
        }
        for t in tokens
        if t and (t.startswith("ExponentPushToken") or t.startswith("ExpoPushToken"))
    ]

    if not messages:
        return

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(EXPO_PUSH_URL, json=messages)
            if resp.status_code != 200:
                logger.warning(f"Expo push API returned status {resp.status_code}: {resp.text}")
            else:
                logger.info(f"Delivered push notifications to {len(messages)} device(s): {resp.json()}")
    except Exception as e:
        logger.warning(f"Push notification delivery skipped: {e}")
