import re
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pymongo import ReturnDocument

from backend.app.core.deps import get_current_user, require_roles
from backend.app.core.database import get_database
from backend.app.schemas.task import (
    TaskCreate,
    TaskAcknowledge,
    TaskComplete,
    TaskCancel,
    TaskResponse,
    TasksListResponse,
    AnnouncementCreate,
    AnnouncementResponse,
)
from backend.app.services.sequence_service import generate_task_id
from backend.app.services.notification_service import create_in_app_notification, broadcast_notification

router = APIRouter()


async def get_task_doc(db, task_id: str):
    """Fetch task by sequential task_id or ObjectId."""
    query = {"task_id": task_id}
    if ObjectId.is_valid(task_id):
        query = {"$or": [{"task_id": task_id}, {"_id": ObjectId(task_id)}]}
    return await db.tasks.find_one(query)


async def task_doc_to_response(db, doc: dict) -> TaskResponse:
    assigned_name = None
    if doc.get("assigned_to"):
        assigned_user = await db.users.find_one({
            "$or": [
                {"_id": ObjectId(doc["assigned_to"]) if ObjectId.is_valid(doc["assigned_to"]) else None},
                {"email": doc["assigned_to"]},
            ]
        })
        if assigned_user:
            assigned_name = assigned_user.get("full_name") or assigned_user.get("email")

    creator_name = None
    if doc.get("assigned_by"):
        creator = await db.users.find_one({
            "$or": [
                {"_id": ObjectId(doc["assigned_by"]) if ObjectId.is_valid(doc["assigned_by"]) else None},
                {"email": doc["assigned_by"]},
            ]
        })
        if creator:
            creator_name = creator.get("full_name") or creator.get("email")

    return TaskResponse(
        id=str(doc["_id"]),
        task_id=doc["task_id"],
        title=doc["title"],
        task_type=doc.get("task_type", "general"),
        assigned_to=str(doc["assigned_to"]),
        assigned_name=assigned_name,
        assigned_by=str(doc["assigned_by"]),
        assigned_by_name=creator_name,
        priority=doc.get("priority", "medium"),
        status=doc.get("status", "pending"),
        target_client_name=doc.get("target_client_name"),
        target_client_phone=doc.get("target_client_phone"),
        monthly_budget=doc.get("monthly_budget"),
        asset_preference=doc.get("asset_preference"),
        property_id=doc.get("property_id"),
        enquiry_id=doc.get("enquiry_id"),
        lockbox_tag=doc.get("lockbox_tag"),
        sla_minutes=doc.get("sla_minutes", 30),
        due_at=doc.get("due_at"),
        instructions=doc.get("instructions"),
        acknowledged_at=doc.get("acknowledged_at"),
        completed_at=doc.get("completed_at"),
        completion_remarks=doc.get("completion_remarks"),
        cancellation_reason=doc.get("cancellation_reason"),
        created_at=doc.get("created_at") or datetime.now(timezone.utc),
        updated_at=doc.get("updated_at") or doc.get("created_at") or datetime.now(timezone.utc),
    )


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: TaskCreate,
    current_user: dict = Depends(require_roles(["super_admin", "admin"])),
):
    """
    PRD Section 5.13: Admin dispatches operational directive or lead assignment to an executive.
    """
    db = get_database()
    now = datetime.now(timezone.utc)
    user_id_str = str(current_user["_id"])

    # Resolve target assignee user
    assignee_query = {
        "$or": [
            {"email": payload.assigned_to.strip().lower()},
            {"_id": ObjectId(payload.assigned_to) if ObjectId.is_valid(payload.assigned_to) else None},
        ]
    }
    target_emp = await db.users.find_one(assignee_query)
    if not target_emp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Target executive '{payload.assigned_to}' not found.",
        )

    task_id = await generate_task_id(db)
    due_datetime = payload.due_at or (now + timedelta(minutes=payload.sla_minutes))

    doc = {
        "task_id": task_id,
        "title": payload.title,
        "task_type": payload.task_type,
        "assigned_to": str(target_emp["_id"]),
        "assigned_by": user_id_str,
        "priority": payload.priority,
        "status": "pending",
        "target_client_name": payload.target_client_name,
        "target_client_phone": payload.target_client_phone,
        "monthly_budget": payload.monthly_budget,
        "asset_preference": payload.asset_preference,
        "property_id": payload.property_id,
        "enquiry_id": payload.enquiry_id,
        "lockbox_tag": payload.lockbox_tag,
        "sla_minutes": payload.sla_minutes,
        "due_at": due_datetime,
        "instructions": payload.instructions,
        "created_at": now,
        "updated_at": now,
    }

    result = await db.tasks.insert_one(doc)
    doc["_id"] = result.inserted_id

    # Create in-app alert & push notification
    await create_in_app_notification(
        db=db,
        user_id=str(target_emp["_id"]),
        title=f"New Task: {payload.title}",
        body=f"Assigned by {current_user.get('full_name', 'Admin')}. Standard {payload.sla_minutes}m SLA response required.",
        notification_type="task_assigned",
        entity_type="task",
        entity_id=task_id,
    )

    # Activity log
    await db.activity_events.insert_one({
        "event_type": "task.dispatched",
        "entity_type": "task",
        "entity_id": task_id,
        "actor_id": user_id_str,
        "actor_role": current_user.get("role", "admin"),
        "details": {"title": payload.title, "assigned_to": str(target_emp["_id"])},
        "created_at": now,
    })

    return await task_doc_to_response(db, doc)


@router.get("", response_model=TasksListResponse)
async def list_tasks(
    status_filter: Optional[str] = Query(None, alias="status"),
    task_type: Optional[str] = None,
    assigned_to: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(50, ge=1, le=100),
    skip: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    """
    List tasks. Executives view their assigned tasks. Admins view all branch tasks.
    """
    db = get_database()
    query: dict = {}

    if current_user["role"] == "executive":
        query["assigned_to"] = str(current_user["_id"])
    elif assigned_to:
        query["assigned_to"] = assigned_to

    if status_filter and status_filter.lower() != "all":
        query["status"] = status_filter.lower()

    if task_type:
        query["task_type"] = task_type

    if search:
        escaped = re.escape(search.strip())
        query["$or"] = [
            {"task_id": {"$regex": escaped, "$options": "i"}},
            {"title": {"$regex": escaped, "$options": "i"}},
            {"target_client_name": {"$regex": escaped, "$options": "i"}},
            {"instructions": {"$regex": escaped, "$options": "i"}},
        ]

    total = await db.tasks.count_documents(query)
    docs = await db.tasks.find(query).sort("created_at", -1).skip(skip).limit(limit).to_list(length=limit)
    items = [await task_doc_to_response(db, d) for d in docs]

    base_scope = {"assigned_to": str(current_user["_id"])} if current_user["role"] == "executive" else {}
    pending_ack = await db.tasks.count_documents({**base_scope, "status": "pending"})
    in_progress = await db.tasks.count_documents({**base_scope, "status": {"$in": ["acknowledged", "in_progress"]}})
    completed = await db.tasks.count_documents({**base_scope, "status": "completed"})

    return TasksListResponse(
        items=items,
        total=total,
        pending_ack_count=pending_ack,
        in_progress_count=in_progress,
        completed_count=completed,
    )


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: str,
    current_user: dict = Depends(get_current_user),
):
    db = get_database()
    doc = await get_task_doc(db, task_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Task '{task_id}' not found.")
    return await task_doc_to_response(db, doc)


@router.post("/{task_id}/acknowledge", response_model=TaskResponse)
async def acknowledge_task(
    task_id: str,
    payload: TaskAcknowledge = None,
    current_user: dict = Depends(get_current_user),
):
    """
    Executive acknowledges task assignment (matching Stitch Image 8 button).
    Updates status to 'acknowledged' and records response timestamp.
    """
    db = get_database()
    now = datetime.now(timezone.utc)
    user_id_str = str(current_user["_id"])

    doc = await get_task_doc(db, task_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Task '{task_id}' not found.")

    if current_user["role"] == "executive" and doc["assigned_to"] != user_id_str:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only acknowledge tasks assigned to you.")

    update_fields = {
        "status": "acknowledged",
        "acknowledged_at": now,
        "updated_at": now,
    }
    if payload and payload.acknowledgement_note:
        update_fields["instructions"] = f"{doc.get('instructions', '')}\n[Ack Note]: {payload.acknowledgement_note}".strip()

    updated = await db.tasks.find_one_and_update(
        {"_id": doc["_id"]},
        {"$set": update_fields},
        return_document=ReturnDocument.AFTER,
    )

    # Notify creator admin
    await create_in_app_notification(
        db=db,
        user_id=doc["assigned_by"],
        title="Directive Acknowledged",
        body=f"{current_user.get('full_name', 'Executive')} acknowledged task #{doc['task_id']}: '{doc['title']}'.",
        notification_type="task_assigned",
        entity_type="task",
        entity_id=doc["task_id"],
    )

    # Activity log
    await db.activity_events.insert_one({
        "event_type": "task.acknowledged",
        "entity_type": "task",
        "entity_id": doc["task_id"],
        "actor_id": user_id_str,
        "actor_role": current_user.get("role", "executive"),
        "created_at": now,
    })

    return await task_doc_to_response(db, updated)


@router.post("/{task_id}/complete", response_model=TaskResponse)
async def complete_task(
    task_id: str,
    payload: TaskComplete,
    current_user: dict = Depends(get_current_user),
):
    """Mark task as completed with remarks."""
    db = get_database()
    now = datetime.now(timezone.utc)
    user_id_str = str(current_user["_id"])

    doc = await get_task_doc(db, task_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Task '{task_id}' not found.")

    updated = await db.tasks.find_one_and_update(
        {"_id": doc["_id"]},
        {
            "$set": {
                "status": "completed",
                "completed_at": now,
                "completion_remarks": payload.get_remarks(),
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )

    await db.activity_events.insert_one({
        "event_type": "task.completed",
        "entity_type": "task",
        "entity_id": doc["task_id"],
        "actor_id": user_id_str,
        "actor_role": current_user.get("role", "executive"),
        "created_at": now,
    })

    return await task_doc_to_response(db, updated)


@router.post("/{task_id}/cancel", response_model=TaskResponse)
async def cancel_task(
    task_id: str,
    payload: TaskCancel,
    current_user: dict = Depends(require_roles(["super_admin", "admin"])),
):
    """Cancel task with mandatory reason."""
    db = get_database()
    now = datetime.now(timezone.utc)

    doc = await get_task_doc(db, task_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Task '{task_id}' not found.")

    updated = await db.tasks.find_one_and_update(
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

    return await task_doc_to_response(db, updated)


# --- ANNOUNCEMENTS & BRANCH NOTICES ---


@router.post("/announcements/create", response_model=AnnouncementResponse, status_code=status.HTTP_201_CREATED)
async def create_announcement(
    payload: AnnouncementCreate,
    current_user: dict = Depends(require_roles(["super_admin", "admin"])),
):
    """Post branch notice or company-wide announcement."""
    db = get_database()
    now = datetime.now(timezone.utc)
    user_id_str = str(current_user["_id"])

    doc = {
        "title": payload.title,
        "body": payload.body,
        "audience": payload.audience,
        "priority": payload.priority,
        "created_by": user_id_str,
        "created_by_name": current_user.get("full_name") or current_user.get("email"),
        "created_at": now,
        "expires_at": payload.expires_at,
    }

    result = await db.announcements.insert_one(doc)
    doc["_id"] = result.inserted_id

    # Broadcast notification to target audience
    target_roles = ["executive", "admin", "super_admin"] if payload.audience == "all" else [payload.audience]
    await broadcast_notification(
        db=db,
        target_roles=target_roles,
        title=f"BRANCH NOTICE: {payload.title}",
        body=payload.body[:150] + ("..." if len(payload.body) > 150 else ""),
        notification_type="announcement",
        entity_type="announcement",
        entity_id=str(doc["_id"]),
    )

    return AnnouncementResponse(
        id=str(doc["_id"]),
        title=doc["title"],
        body=doc["body"],
        audience=doc["audience"],
        priority=doc["priority"],
        created_by=doc["created_by"],
        created_by_name=doc.get("created_by_name"),
        created_at=doc.get("created_at") or datetime.now(timezone.utc),
        expires_at=doc.get("expires_at"),
    )


@router.get("/announcements/active", response_model=List[AnnouncementResponse])
async def list_active_announcements(
    current_user: dict = Depends(get_current_user),
):
    """List active announcements for current user's role."""
    db = get_database()
    now = datetime.now(timezone.utc)

    user_role = current_user.get("role", "executive")
    audience_query = ["all", user_role]
    if user_role == "executive":
        audience_query.append("executives")
    elif user_role in ["admin", "super_admin"]:
        audience_query.extend(["admins", "executives"])

    query = {
        "audience": {"$in": audience_query},
        "$or": [
            {"expires_at": None},
            {"expires_at": {"$gt": now}},
        ],
    }

    docs = await db.announcements.find(query).sort("created_at", -1).to_list(length=50)

    return [
        AnnouncementResponse(
            id=str(d["_id"]),
            title=d["title"],
            body=d["body"],
            audience=d["audience"],
            priority=d.get("priority", "normal"),
            created_by=str(d["created_by"]),
            created_by_name=d.get("created_by_name"),
            created_at=d.get("created_at") or datetime.now(timezone.utc),
            expires_at=d.get("expires_at"),
        )
        for d in docs
    ]
