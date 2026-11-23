from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorDatabase


async def recalculate_enquiry_followup_timestamps(db: AsyncIOMotorDatabase, enquiry_id: str):
    """
    PRD Section 5.12: Authoritative derivation of Last and Next follow-up timestamps.
    Prevents conflicting manual summary fields.
    """
    now = datetime.now(timezone.utc)

    # 1. Most recent completed follow-up
    last_completed_cursor = db.followups.find({
        "enquiry_id": enquiry_id,
        "status": "completed",
    }).sort("completed_at", -1).limit(1)
    last_completed = await last_completed_cursor.to_list(length=1)
    last_followup_at = last_completed[0]["completed_at"] if last_completed else None

    # 2. Earliest scheduled upcoming follow-up
    next_scheduled_cursor = db.followups.find({
        "enquiry_id": enquiry_id,
        "status": {"$in": ["scheduled", "due"]},
    }).sort("scheduled_at", 1).limit(1)
    next_scheduled = await next_scheduled_cursor.to_list(length=1)
    next_followup_at = next_scheduled[0]["scheduled_at"] if next_scheduled else None

    # Update enquiry
    await db.enquiries.update_one(
        {"enquiry_id": enquiry_id},
        {
            "$set": {
                "last_followup_at": last_followup_at,
                "next_followup_at": next_followup_at,
                "updated_at": now,
            }
        },
    )
