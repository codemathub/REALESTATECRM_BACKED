import re
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from bson import ObjectId
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pymongo import ReturnDocument

from backend.app.core.deps import get_current_user
from backend.app.core.database import get_database
from backend.app.core.datetime_utils import ensure_utc
from backend.app.schemas.followup import (
    FollowUpCreate,
    FollowUpComplete,
    FollowUpReschedule,
    FollowUpCancel,
    FollowUpResponse,
    FollowUpsListResponse,
    CalendarAgendaItem,
)
from backend.app.services.sequence_service import generate_followup_id
from backend.app.services.followup_service import recalculate_enquiry_followup_timestamps
from backend.app.api.v1.endpoints.enquiries import get_enquiry_doc

router = APIRouter()


async def get_followup_doc(db, followup_id: str):
    """Fetch follow-up by sequential followup_id or MongoDB _id."""
    query = {"followup_id": followup_id}
    if ObjectId.is_valid(followup_id):
        query = {"$or": [{"followup_id": followup_id}, {"_id": ObjectId(followup_id)}]}
    return await db.followups.find_one(query)


async def followup_doc_to_response(db, doc: dict) -> FollowUpResponse:
    resp_name = None
    if doc.get("responsible_employee"):
        emp = await db.users.find_one({
            "$or": [
                {"_id": ObjectId(doc["responsible_employee"]) if ObjectId.is_valid(doc["responsible_employee"]) else None},
                {"email": doc["responsible_employee"]},
            ]
        })
        if emp:
            resp_name = emp.get("full_name") or emp.get("email")

    now = datetime.now(timezone.utc)
    curr_status = doc.get("status", "scheduled")
    sched_at = ensure_utc(doc.get("scheduled_at")) or now

    # Dynamic due/overdue indicator if scheduled time is in the past
    if curr_status == "scheduled" and sched_at < now:
        curr_status = "due"

    return FollowUpResponse(
        id=str(doc["_id"]),
        followup_id=doc["followup_id"],
        customer_id=doc["customer_id"],
        customer_name=doc.get("customer_name", "Customer"),
        customer_phone=doc.get("customer_phone", ""),
        enquiry_id=doc["enquiry_id"],
        enquiry_requirement=doc.get("enquiry_requirement"),
        scheduled_at=sched_at,
        responsible_employee=str(doc.get("responsible_employee", "")),
        responsible_name=resp_name,
        notes=doc.get("notes", ""),
        status=curr_status,
        outcome=doc.get("outcome"),
        remarks=doc.get("remarks"),
        completed_at=ensure_utc(doc.get("completed_at")),
        reschedule_reason=doc.get("reschedule_reason"),
        cancellation_reason=doc.get("cancellation_reason"),
        created_by=str(doc.get("created_by", "")),
        created_at=ensure_utc(doc.get("created_at")) or now,
        updated_at=ensure_utc(doc.get("updated_at")) or now,
    )


@router.post("", response_model=FollowUpResponse, status_code=status.HTTP_201_CREATED)
async def create_followup(
    payload: FollowUpCreate,
    current_user: dict = Depends(get_current_user),
):
    """
    PRD Section 5.12: Schedule a follow-up for a customer enquiry.
    Derives authoritative enquiry timestamps.
    """
    db = get_database()
    now = datetime.now(timezone.utc)
    user_id_str = str(current_user["_id"])

    enquiry = await get_enquiry_doc(db, payload.enquiry_id)
    if not enquiry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Enquiry '{payload.enquiry_id}' not found.",
        )

    responsible = payload.responsible_employee or user_id_str
    followup_id = await generate_followup_id(db)

    doc = {
        "followup_id": followup_id,
        "customer_id": enquiry["customer_id"],
        "customer_name": enquiry.get("customer_name", "Customer"),
        "customer_phone": enquiry.get("customer_phone", ""),
        "enquiry_id": enquiry["enquiry_id"],
        "enquiry_requirement": enquiry.get("looking_for"),
        "scheduled_at": payload.scheduled_at,
        "responsible_employee": responsible,
        "notes": payload.notes,
        "status": "scheduled",
        "created_by": user_id_str,
        "created_at": now,
        "updated_at": now,
    }

    result = await db.followups.insert_one(doc)
    doc["_id"] = result.inserted_id

    # Recalculate derived enquiry timestamps
    await recalculate_enquiry_followup_timestamps(db, enquiry["enquiry_id"])

    # Audit event
    await db.activity_events.insert_one({
        "event_type": "followup.created",
        "entity_type": "followup",
        "entity_id": followup_id,
        "actor_id": user_id_str,
        "actor_role": current_user.get("role", "executive"),
        "details": {
            "enquiry_id": enquiry["enquiry_id"],
            "scheduled_at": payload.scheduled_at.isoformat(),
        },
        "created_at": now,
    })

    return await followup_doc_to_response(db, doc)


@router.get("", response_model=FollowUpsListResponse)
async def list_followups(
    range_filter: Optional[str] = Query(None, alias="range", pattern="^(today|upcoming|overdue|completed|all)$"),
    status_filter: Optional[str] = Query(None, alias="status"),
    enquiry_id: Optional[str] = None,
    customer_id: Optional[str] = None,
    employee_id: Optional[str] = None,
    search: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    skip: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    """
    List follow-ups with range filters (today, upcoming, overdue, completed) and employee scopes.
    """
    db = get_database()
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    query: dict = {}

    if enquiry_id:
        query["enquiry_id"] = enquiry_id
    if customer_id:
        query["customer_id"] = customer_id
    if employee_id:
        query["responsible_employee"] = employee_id

    if range_filter == "today":
        query["scheduled_at"] = {"$gte": today_start, "$lt": today_end}
        query["status"] = {"$ne": "cancelled"}
    elif range_filter == "upcoming":
        query["scheduled_at"] = {"$gte": today_end}
        query["status"] = "scheduled"
    elif range_filter == "overdue":
        query["scheduled_at"] = {"$lt": now}
        query["status"] = "scheduled"
    elif range_filter == "completed":
        query["status"] = "completed"

    if status_filter and status_filter.lower() != "all":
        query["status"] = status_filter.lower()

    if search:
        escaped = re.escape(search.strip())
        query["$or"] = [
            {"followup_id": {"$regex": escaped, "$options": "i"}},
            {"customer_name": {"$regex": escaped, "$options": "i"}},
            {"customer_phone": {"$regex": escaped}},
            {"enquiry_id": {"$regex": escaped, "$options": "i"}},
            {"notes": {"$regex": escaped, "$options": "i"}},
        ]

    total = await db.followups.count_documents(query)
    docs = await db.followups.find(query).sort("scheduled_at", 1).skip(skip).limit(limit).to_list(length=limit)

    items = [await followup_doc_to_response(db, d) for d in docs]

    # Compute KPI badges
    overdue_count = await db.followups.count_documents({
        "scheduled_at": {"$lt": now},
        "status": "scheduled",
    })
    today_count = await db.followups.count_documents({
        "scheduled_at": {"$gte": today_start, "$lt": today_end},
        "status": {"$ne": "cancelled"},
    })
    upcoming_count = await db.followups.count_documents({
        "scheduled_at": {"$gte": today_end},
        "status": "scheduled",
    })

    return FollowUpsListResponse(
        items=items,
        total=total,
        overdue_count=overdue_count,
        today_count=today_count,
        upcoming_count=upcoming_count,
    )


@router.get("/calendar/agenda", response_model=List[CalendarAgendaItem])
async def get_calendar_agenda(
    start_date: str = Query(..., description="Start date YYYY-MM-DD"),
    end_date: str = Query(..., description="End date YYYY-MM-DD"),
    current_user: dict = Depends(get_current_user),
):
    """
    PRD Section 5.12: Company calendar agenda grouped by day.
    """
    db = get_database()
    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Expected YYYY-MM-DD.")

    docs = await db.followups.find({
        "scheduled_at": {"$gte": start_dt, "$lt": end_dt},
        "status": {"$ne": "cancelled"},
    }).sort("scheduled_at", 1).to_list(length=500)

    # Group by date string
    grouped: dict = {}
    for d in docs:
        item = await followup_doc_to_response(db, d)
        date_key = item.scheduled_at.strftime("%Y-%m-%d")
        if date_key not in grouped:
            grouped[date_key] = []
        grouped[date_key].append(item)

    agenda = [
        CalendarAgendaItem(date=k, total_count=len(v), followups=v)
        for k, v in sorted(grouped.items())
    ]
    return agenda


@router.get("/{followup_id}", response_model=FollowUpResponse)
async def get_followup(
    followup_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Retrieve single follow-up."""
    db = get_database()
    doc = await get_followup_doc(db, followup_id)
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Follow-up '{followup_id}' not found.",
        )
    return await followup_doc_to_response(db, doc)


@router.post("/{followup_id}/complete", response_model=FollowUpResponse)
async def complete_followup(
    followup_id: str,
    payload: FollowUpComplete,
    current_user: dict = Depends(get_current_user),
):
    """
    Complete follow-up with mandatory outcome, remarks, and optional next scheduled follow-up.
    """
    db = get_database()
    now = datetime.now(timezone.utc)
    user_id_str = str(current_user["_id"])

    doc = await get_followup_doc(db, followup_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Follow-up '{followup_id}' not found.")

    updated_doc = await db.followups.find_one_and_update(
        {"_id": doc["_id"]},
        {
            "$set": {
                "status": "completed",
                "outcome": payload.outcome,
                "remarks": payload.remarks,
                "completed_at": now,
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )

    # Optional auto-creation of next scheduled follow-up
    if payload.next_followup_at:
        next_fid = await generate_followup_id(db)
        await db.followups.insert_one({
            "followup_id": next_fid,
            "customer_id": doc["customer_id"],
            "customer_name": doc["customer_name"],
            "customer_phone": doc["customer_phone"],
            "enquiry_id": doc["enquiry_id"],
            "enquiry_requirement": doc.get("enquiry_requirement"),
            "scheduled_at": payload.next_followup_at,
            "responsible_employee": doc["responsible_employee"],
            "notes": payload.next_followup_notes or f"Follow-up after {doc['followup_id']}",
            "status": "scheduled",
            "created_by": user_id_str,
            "created_at": now,
            "updated_at": now,
        })

    # Recalculate derived enquiry timestamps
    await recalculate_enquiry_followup_timestamps(db, doc["enquiry_id"])

    # Audit event
    await db.activity_events.insert_one({
        "event_type": "followup.completed",
        "entity_type": "followup",
        "entity_id": doc["followup_id"],
        "actor_id": user_id_str,
        "actor_role": current_user.get("role", "executive"),
        "details": {"outcome": payload.outcome, "remarks": payload.remarks},
        "created_at": now,
    })

    return await followup_doc_to_response(db, updated_doc)


@router.post("/{followup_id}/reschedule", response_model=FollowUpResponse)
async def reschedule_followup(
    followup_id: str,
    payload: FollowUpReschedule,
    current_user: dict = Depends(get_current_user),
):
    """Reschedule follow-up with mandatory reason."""
    db = get_database()
    now = datetime.now(timezone.utc)
    user_id_str = str(current_user["_id"])

    doc = await get_followup_doc(db, followup_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Follow-up '{followup_id}' not found.")

    updated_doc = await db.followups.find_one_and_update(
        {"_id": doc["_id"]},
        {
            "$set": {
                "scheduled_at": payload.new_scheduled_at,
                "reschedule_reason": payload.reason,
                "status": "scheduled",
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )

    await recalculate_enquiry_followup_timestamps(db, doc["enquiry_id"])

    await db.activity_events.insert_one({
        "event_type": "followup.rescheduled",
        "entity_type": "followup",
        "entity_id": doc["followup_id"],
        "actor_id": user_id_str,
        "actor_role": current_user.get("role", "executive"),
        "details": {"reason": payload.reason, "new_scheduled_at": payload.new_scheduled_at.isoformat()},
        "created_at": now,
    })

    return await followup_doc_to_response(db, updated_doc)


@router.post("/{followup_id}/cancel", response_model=FollowUpResponse)
async def cancel_followup(
    followup_id: str,
    payload: FollowUpCancel,
    current_user: dict = Depends(get_current_user),
):
    """Cancel follow-up with mandatory reason."""
    db = get_database()
    now = datetime.now(timezone.utc)
    user_id_str = str(current_user["_id"])

    doc = await get_followup_doc(db, followup_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Follow-up '{followup_id}' not found.")

    updated_doc = await db.followups.find_one_and_update(
        {"_id": doc["_id"]},
        {
            "$set": {
                "status": "cancelled",
                "cancellation_reason": payload.reason,
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )

    await recalculate_enquiry_followup_timestamps(db, doc["enquiry_id"])

    await db.activity_events.insert_one({
        "event_type": "followup.cancelled",
        "entity_type": "followup",
        "entity_id": doc["followup_id"],
        "actor_id": user_id_str,
        "actor_role": current_user.get("role", "executive"),
        "details": {"reason": payload.reason},
        "created_at": now,
    })

    return await followup_doc_to_response(db, updated_doc)


class FollowUpAddNote(BaseModel):
    notes: str = Field(..., min_length=1, description="Follow-up note or conversation remarks")
    outcome: Optional[str] = None
    remarks: Optional[str] = None


@router.post("/{followup_id}/note", response_model=FollowUpResponse, summary="Admin and Executive can append conversation notes on follow-up")
async def add_followup_note(
    followup_id: str,
    payload: FollowUpAddNote,
    current_user: dict = Depends(get_current_user),
):
    """
    Allow Admin or Executive to add notes or update discussion remarks on any follow-up.
    Appends with author name and timestamp for complete auditability.
    """
    db = get_database()
    now = datetime.now(timezone.utc)
    user_id_str = str(current_user["_id"])
    author_name = current_user.get("full_name") or current_user.get("email") or "Agent"

    doc = await get_followup_doc(db, followup_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Follow-up '{followup_id}' not found.")

    timestamp_str = now.strftime("%d %b %Y, %I:%M %p")
    new_note_entry = f"[{timestamp_str} - {author_name}]: {payload.notes.strip()}"
    existing_notes = doc.get("notes", "")
    updated_notes = f"{existing_notes}\n{new_note_entry}" if existing_notes else new_note_entry

    update_fields: dict = {
        "notes": updated_notes,
        "updated_at": now,
    }
    if payload.outcome:
        update_fields["outcome"] = payload.outcome
    if payload.remarks:
        update_fields["remarks"] = payload.remarks

    updated_doc = await db.followups.find_one_and_update(
        {"_id": doc["_id"]},
        {"$set": update_fields},
        return_document=ReturnDocument.AFTER,
    )

    await db.activity_events.insert_one({
        "event_type": "followup.note_added",
        "entity_type": "followup",
        "entity_id": doc["followup_id"],
        "actor_id": user_id_str,
        "actor_role": current_user.get("role", "executive"),
        "details": {"new_note": payload.notes.strip()},
        "created_at": now,
    })

    return await followup_doc_to_response(db, updated_doc)
