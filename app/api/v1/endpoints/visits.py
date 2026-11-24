import re
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pymongo import ReturnDocument

from backend.app.core.deps import get_current_user, require_admin
from backend.app.core.database import get_database
from backend.app.schemas.visit import (
    VisitCreate,
    VisitComplete,
    VisitReschedule,
    VisitCancel,
    VisitReassign,
    VisitResponse,
    VisitsListResponse,
)
from backend.app.schemas.deal import DealResponse
from backend.app.services.sequence_service import generate_visit_id, generate_deal_id
from backend.app.services.notification_service import create_in_app_notification, broadcast_notification
from backend.app.api.v1.endpoints.properties import get_property_doc
from backend.app.api.v1.endpoints.enquiries import get_enquiry_doc


router = APIRouter()


async def get_visit_doc(db, visit_id: str):
    """Fetch visit by sequential visit_id or MongoDB _id."""
    query = {"visit_id": visit_id}
    if ObjectId.is_valid(visit_id):
        query = {"$or": [{"visit_id": visit_id}, {"_id": ObjectId(visit_id)}]}
    return await db.visits.find_one(query)


async def visit_doc_to_response(db, doc: dict) -> VisitResponse:
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

    claimed_by_name = None
    if doc.get("claimed_by"):
        claimed_user = await db.users.find_one({
            "$or": [
                {"_id": ObjectId(doc["claimed_by"]) if ObjectId.is_valid(doc["claimed_by"]) else None},
                {"email": doc["claimed_by"]},
            ]
        })
        if claimed_user:
            claimed_by_name = claimed_user.get("full_name") or claimed_user.get("email")

    return VisitResponse(
        id=str(doc["_id"]),
        visit_id=doc["visit_id"],
        customer_id=doc["customer_id"],
        customer_name=doc.get("customer_name", "Unknown"),
        customer_phone=doc.get("customer_phone", ""),
        enquiry_id=doc["enquiry_id"],
        property_id=doc["property_id"],
        property_society=doc.get("property_society", ""),
        property_bhk=doc.get("property_bhk"),
        property_location=doc.get("property_location", ""),
        property_amount=float(doc.get("property_amount", 0.0)),
        property_transaction_type=doc.get("property_transaction_type", "rent"),
        scheduled_at=doc["scheduled_at"],
        assigned_to=doc.get("assigned_to"),
        assigned_name=assigned_name,
        claim_status=doc.get("claim_status", "direct_assigned"),
        claimed_by=doc.get("claimed_by"),
        claimed_by_name=claimed_by_name,
        claimed_at=doc.get("claimed_at"),
        status=doc.get("status", "scheduled"),
        outcome=doc.get("outcome"),
        remarks=doc.get("remarks"),
        reschedule_reason=doc.get("reschedule_reason"),
        cancellation_reason=doc.get("cancellation_reason"),
        created_by=str(doc.get("created_by", "")),
        created_at=doc.get("created_at", datetime.now(timezone.utc)),
        updated_at=doc.get("updated_at", datetime.now(timezone.utc)),
    )


@router.post("", response_model=VisitResponse, status_code=status.HTTP_201_CREATED)
async def create_visit(
    payload: VisitCreate,
    current_user: dict = Depends(get_current_user),
):
    """
    PRD Section 5.11: Schedule visit for an enquiry and property.
    - Admin can directly assign or post to Open Claim Pool (assigned_to=None).
    - Executive schedules directly assigned to self.
    """
    db = get_database()
    now = datetime.now(timezone.utc)

    # 1. Verify Enquiry
    enquiry = await get_enquiry_doc(db, payload.enquiry_id)
    if not enquiry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Enquiry '{payload.enquiry_id}' not found.",
        )

    # 2. Verify Property
    prop = await get_property_doc(db, payload.property_id)
    if not prop:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Property '{payload.property_id}' not found.",
        )

    user_role = current_user.get("role", "executive")
    user_id_str = str(current_user["_id"])

    # 3. Determine assignment & claim status
    if user_role == "executive":
        assigned_to = user_id_str
        claim_status = "direct_assigned"
    else:
        # Admin / Super Admin
        if payload.assigned_to:
            # Direct assignment
            assigned_to = payload.assigned_to
            claim_status = "direct_assigned"
        else:
            # Open Pool for claiming
            assigned_to = None
            claim_status = "unclaimed"

    visit_id = await generate_visit_id(db)

    visit_doc = {
        "visit_id": visit_id,
        "customer_id": enquiry["customer_id"],
        "customer_name": enquiry.get("customer_name", "Customer"),
        "customer_phone": enquiry.get("customer_phone", ""),
        "enquiry_id": enquiry["enquiry_id"],
        "property_id": prop["property_id"],
        "property_society": prop.get("society_name", ""),
        "property_bhk": prop.get("property_type") or prop.get("bhk"),
        "property_location": prop.get("location", ""),
        "property_amount": float(prop.get("expected_amount", 0.0)),
        "property_transaction_type": prop.get("transaction_type", "rent"),
        "scheduled_at": payload.scheduled_at,
        "assigned_to": assigned_to,
        "claim_status": claim_status,
        "claimed_by": None,
        "claimed_at": None,
        "status": "scheduled",
        "notes": payload.notes,
        "created_by": user_id_str,
        "created_at": now,
        "updated_at": now,
    }

    result = await db.visits.insert_one(visit_doc)
    visit_doc["_id"] = result.inserted_id

    # Notification & Push Alert
    if assigned_to and assigned_to != user_id_str:
        await create_in_app_notification(
            db=db,
            user_id=assigned_to,
            title=f"Site Visit Assigned: {prop.get('society_name', 'Property')}",
            body=f"Visit scheduled for {payload.scheduled_at} with {enquiry.get('customer_name', 'client')}.",
            notification_type="visit_assigned",
            entity_type="visit",
            entity_id=visit_id,
        )
    elif claim_status == "unclaimed":
        await broadcast_notification(
            db=db,
            target_roles=["executive"],
            title="Open Visit Claim Available",
            body=f"New visit at {prop.get('society_name', 'Property')} available in claim pool.",
            notification_type="visit_claim",
            entity_type="visit",
            entity_id=visit_id,
        )

    # Record Audit Event
    await db.activity_events.insert_one({
        "event_type": "visit.created",
        "entity_type": "visit",
        "entity_id": visit_id,
        "actor_id": user_id_str,
        "actor_role": user_role,
        "details": {
            "enquiry_id": enquiry["enquiry_id"],
            "property_id": prop["property_id"],
            "claim_status": claim_status,
            "assigned_to": assigned_to,
        },
        "created_at": now,
    })

    return await visit_doc_to_response(db, visit_doc)


@router.get("", response_model=VisitsListResponse)
async def list_visits(
    status_filter: Optional[str] = Query(None, alias="status"),
    claim_status: Optional[str] = Query(None),
    my_visits: bool = Query(False),
    open_claims_only: bool = Query(False),
    date_filter: Optional[str] = Query(None, alias="date"),
    search: Optional[str] = Query(None),
    property_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    skip: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    """
    List scheduled visits with filtering by status, assignment, and open claiming pool.
    """
    db = get_database()
    query = {}
    user_id_str = str(current_user["_id"])
    user_role = current_user.get("role", "executive")

    if property_id:
        query["property_id"] = property_id

    if open_claims_only:
        query["claim_status"] = "unclaimed"
    elif claim_status:
        query["claim_status"] = claim_status

    if my_visits or (user_role == "executive" and not open_claims_only and not claim_status):
        # Executive default or explicit my_visits
        query["$or"] = [
            {"assigned_to": user_id_str},
            {"claimed_by": user_id_str},
        ]
    elif my_visits:
        query["assigned_to"] = user_id_str

    if status_filter and status_filter.lower() != "all":
        query["status"] = status_filter.lower()

    if date_filter:
        try:
            start_date = datetime.strptime(date_filter, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            end_date = start_date + timedelta(days=1)
            query["scheduled_at"] = {"$gte": start_date, "$lt": end_date}
        except ValueError:
            pass

    if search:
        s_clean = search.strip()
        escaped = re.escape(s_clean)
        query["$or"] = [
            {"visit_id": {"$regex": escaped, "$options": "i"}},
            {"customer_name": {"$regex": escaped, "$options": "i"}},
            {"property_id": {"$regex": escaped, "$options": "i"}},
            {"property_society": {"$regex": escaped, "$options": "i"}},
        ]

    total = await db.visits.count_documents(query)
    docs = await db.visits.find(query).sort("scheduled_at", 1).skip(skip).limit(limit).to_list(length=limit)

    items = [await visit_doc_to_response(db, d) for d in docs]

    # Compute operational KPI metrics
    open_claims_count = await db.visits.count_documents({"claim_status": "unclaimed", "status": "scheduled"})
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    today_visits_count = await db.visits.count_documents({
        "scheduled_at": {"$gte": today_start, "$lt": today_end},
        "status": {"$in": ["scheduled", "in_progress", "completed"]},
    })

    return VisitsListResponse(
        items=items,
        total=total,
        open_claims_count=open_claims_count,
        today_visits_count=today_visits_count,
    )


@router.get("/{visit_id}", response_model=VisitResponse)
async def get_visit(
    visit_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Retrieve single visit record."""
    db = get_database()
    doc = await get_visit_doc(db, visit_id)
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Visit '{visit_id}' not found.",
        )
    return await visit_doc_to_response(db, doc)


@router.post("/{visit_id}/claim", response_model=VisitResponse)
async def claim_visit(
    visit_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    PRD Section 5.11 & REQ-VIS-01:
    Atomic conditional claim operation.
    Only the first eligible claimant wins; race conditions return 409 Conflict.
    """
    db = get_database()
    now = datetime.now(timezone.utc)
    user_id_str = str(current_user["_id"])

    # Atomic find_one_and_update condition
    updated_doc = await db.visits.find_one_and_update(
        {
            "$or": [{"visit_id": visit_id}, {"_id": ObjectId(visit_id) if ObjectId.is_valid(visit_id) else None}],
            "claim_status": "unclaimed",
        },
        {
            "$set": {
                "claim_status": "claimed",
                "claimed_by": user_id_str,
                "assigned_to": user_id_str,
                "claimed_at": now,
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )

    if not updated_doc:
        # Check if visit exists to return accurate error code
        existing = await get_visit_doc(db, visit_id)
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Visit '{visit_id}' does not exist.",
            )
        # Already claimed or direct assigned
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Visit was already claimed by another executive or is not available for claiming.",
        )

    # Record Audit Event
    await db.activity_events.insert_one({
        "event_type": "visit.claimed",
        "entity_type": "visit",
        "entity_id": updated_doc["visit_id"],
        "actor_id": user_id_str,
        "actor_role": current_user.get("role", "executive"),
        "details": {"claimed_at": now.isoformat()},
        "created_at": now,
    })

    return await visit_doc_to_response(db, updated_doc)


@router.post("/{visit_id}/complete", response_model=VisitResponse)
async def complete_visit(
    visit_id: str,
    payload: VisitComplete,
    current_user: dict = Depends(get_current_user),
):
    """Mark a scheduled visit as completed."""
    db = get_database()
    now = datetime.now(timezone.utc)
    user_id_str = str(current_user["_id"])

    doc = await get_visit_doc(db, visit_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Visit '{visit_id}' not found.")

    updated_doc = await db.visits.find_one_and_update(
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

    await db.activity_events.insert_one({
        "event_type": "visit.completed",
        "entity_type": "visit",
        "entity_id": doc["visit_id"],
        "actor_id": user_id_str,
        "actor_role": current_user.get("role", "executive"),
        "details": {"outcome": payload.outcome, "remarks": payload.remarks},
        "created_at": now,
    })

    return await visit_doc_to_response(db, updated_doc)


@router.post("/{visit_id}/reschedule", response_model=VisitResponse)
async def reschedule_visit(
    visit_id: str,
    payload: VisitReschedule,
    current_user: dict = Depends(get_current_user),
):
    """Reschedule visit date/time with mandatory reason."""
    db = get_database()
    now = datetime.now(timezone.utc)
    user_id_str = str(current_user["_id"])

    doc = await get_visit_doc(db, visit_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Visit '{visit_id}' not found.")

    updated_doc = await db.visits.find_one_and_update(
        {"_id": doc["_id"]},
        {
            "$set": {
                "scheduled_at": payload.new_scheduled_at,
                "reschedule_reason": payload.reason,
                "status": "rescheduled",
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )

    await db.activity_events.insert_one({
        "event_type": "visit.rescheduled",
        "entity_type": "visit",
        "entity_id": doc["visit_id"],
        "actor_id": user_id_str,
        "actor_role": current_user.get("role", "executive"),
        "details": {
            "new_scheduled_at": payload.new_scheduled_at.isoformat(),
            "reason": payload.reason,
        },
        "created_at": now,
    })

    return await visit_doc_to_response(db, updated_doc)


@router.post("/{visit_id}/cancel", response_model=VisitResponse)
async def cancel_visit(
    visit_id: str,
    payload: VisitCancel,
    current_user: dict = Depends(get_current_user),
):
    """Cancel visit with mandatory reason."""
    db = get_database()
    now = datetime.now(timezone.utc)
    user_id_str = str(current_user["_id"])

    doc = await get_visit_doc(db, visit_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Visit '{visit_id}' not found.")

    updated_doc = await db.visits.find_one_and_update(
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

    await db.activity_events.insert_one({
        "event_type": "visit.cancelled",
        "entity_type": "visit",
        "entity_id": doc["visit_id"],
        "actor_id": user_id_str,
        "actor_role": current_user.get("role", "executive"),
        "details": {"reason": payload.reason},
        "created_at": now,
    })

    return await visit_doc_to_response(db, updated_doc)


@router.post("/{visit_id}/reassign", response_model=VisitResponse)
async def reassign_visit(
    visit_id: str,
    payload: VisitReassign,
    current_user: dict = Depends(require_admin),
):
    """Admin / Super Admin reassigns visit to another executive."""
    db = get_database()
    now = datetime.now(timezone.utc)
    user_id_str = str(current_user["_id"])

    doc = await get_visit_doc(db, visit_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Visit '{visit_id}' not found.")

    # Verify target executive exists
    target_user = await db.users.find_one({
        "$or": [
            {"_id": ObjectId(payload.new_assigned_to) if ObjectId.is_valid(payload.new_assigned_to) else None},
            {"email": payload.new_assigned_to},
        ]
    })
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Target executive '{payload.new_assigned_to}' not found.",
        )

    updated_doc = await db.visits.find_one_and_update(
        {"_id": doc["_id"]},
        {
            "$set": {
                "assigned_to": str(target_user["_id"]),
                "claim_status": "direct_assigned",
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )

    await db.activity_events.insert_one({
        "event_type": "visit.reassigned",
        "entity_type": "visit",
        "entity_id": doc["visit_id"],
        "actor_id": user_id_str,
        "actor_role": current_user.get("role", "admin"),
        "details": {"reassigned_to": str(target_user["_id"])},
        "created_at": now,
    })

    return await visit_doc_to_response(db, updated_doc)


@router.post("/{visit_id}/close-deal", response_model=DealResponse)
async def close_deal_from_visit(
    visit_id: str,
    payload: dict = {},
    current_user: dict = Depends(get_current_user),
):
    """
    PRD Section 5.15 & Phase 11:
    Initiate Deal Closed workflow directly from an active or completed visit.
    Automatically links visit's enquiry and property.
    """
    db = get_database()
    now = datetime.now(timezone.utc)

    visit = await get_visit_doc(db, visit_id)
    if not visit:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Visit '{visit_id}' not found.")

    enquiry = await get_enquiry_doc(db, visit["enquiry_id"])
    if not enquiry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Linked Enquiry '{visit['enquiry_id']}' not found.")

    prop = await db.properties.find_one({"property_id": visit["property_id"].upper()})
    if not prop:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Linked Property '{visit['property_id']}' not found.")

    trans_type = enquiry.get("transaction_type", "rent").lower()
    target_property_status = "rented" if trans_type == "rent" else "sold"

    # 1. Update property status
    await db.properties.update_one(
        {"property_id": prop["property_id"]},
        {
            "$set": {
                "status": target_property_status,
                "closed_deal_at": now,
                "closed_enquiry_id": enquiry["enquiry_id"],
                "updated_at": now,
                "updated_by": str(current_user["_id"]),
            }
        },
    )

    # 2. Generate Deal ID and store deal record
    deal_id = await generate_deal_id(db)
    deal_amount = float(payload.get("deal_amount") or prop.get("expected_amount", 0.0))
    exec_id = str(visit.get("assigned_to") or current_user["_id"])
    exec_name = current_user.get("full_name", "Staff Member")
    remarks = payload.get("final_remarks") or f"Deal closed directly from field visit #{visit['visit_id']}."

    deal_doc = {
        "deal_id": deal_id,
        "enquiry_id": enquiry["enquiry_id"],
        "customer_id": enquiry["customer_id"],
        "customer_name": enquiry.get("customer_name", "Customer"),
        "customer_phone": enquiry.get("customer_phone", ""),
        "property_id": prop["property_id"],
        "property_society": prop.get("society_name", ""),
        "property_location": prop.get("location", ""),
        "property_bhk": prop.get("bhk", ""),
        "transaction_type": trans_type,
        "deal_amount": deal_amount,
        "deal_date": now,
        "responsible_executive_id": exec_id,
        "responsible_executive_name": exec_name,
        "final_remarks": remarks,
        "created_at": now,
    }
    deal_res = await db.deals.insert_one(deal_doc)
    deal_doc["_id"] = deal_res.inserted_id

    # 3. Update enquiry status
    await db.enquiries.update_one(
        {"enquiry_id": enquiry["enquiry_id"]},
        {
            "$set": {
                "status": "deal_closed",
                "closed_at": now,
                "deal_id": deal_id,
                "closed_property_id": prop["property_id"],
                "final_remarks": remarks,
                "updated_at": now,
                "updated_by": str(current_user["_id"]),
            }
        },
    )

    # 4. Mark visit as completed with outcome
    await db.visits.update_one(
        {"_id": visit["_id"]},
        {
            "$set": {
                "status": "completed",
                "outcome": f"Deal Closed ({deal_id} • {prop['property_id']})",
                "remarks": remarks,
                "completed_at": now,
                "updated_at": now,
            }
        },
    )

    # 5. Broadcast notification & log activity
    await broadcast_notification(
        db=db,
        target_roles=["super_admin", "admin"],
        title="Field Visit Closed Deal! 🏆",
        body=f"Visit #{visit['visit_id']} converted! {enquiry.get('customer_name')} closed on {prop['property_id']} ({target_property_status.capitalize()}).",
        notification_type="deal_closed",
        entity_type="deal",
        entity_id=deal_id,
    )

    await db.activity_events.insert_one({
        "event_type": "deal.closed_from_visit",
        "entity_type": "deal",
        "entity_id": deal_id,
        "actor_id": str(current_user["_id"]),
        "actor_role": current_user.get("role", "executive"),
        "details": {
            "visit_id": visit["visit_id"],
            "property_id": prop["property_id"],
            "deal_amount": deal_amount,
            "property_status": target_property_status,
        },
        "created_at": now,
    })

    return DealResponse(
        id=str(deal_doc["_id"]),
        deal_id=deal_doc["deal_id"],
        enquiry_id=deal_doc["enquiry_id"],
        customer_id=deal_doc["customer_id"],
        customer_name=deal_doc["customer_name"],
        customer_phone=deal_doc.get("customer_phone"),
        property_id=deal_doc["property_id"],
        property_society=deal_doc.get("property_society"),
        property_location=deal_doc.get("property_location"),
        property_bhk=deal_doc.get("property_bhk"),
        transaction_type=deal_doc["transaction_type"],
        deal_amount=deal_doc["deal_amount"],
        deal_date=deal_doc["deal_date"],
        responsible_executive_id=deal_doc["responsible_executive_id"],
        responsible_executive_name=deal_doc["responsible_executive_name"],
        final_remarks=deal_doc.get("final_remarks"),
        created_at=deal_doc["created_at"],
    )


@router.post("/{visit_id}/abort-deal", response_model=VisitResponse)
async def abort_deal_from_visit(
    visit_id: str,
    payload: dict,
    current_user: dict = Depends(get_current_user),
):
    """
    PRD Section 5.15: Abort enquiry directly from visit.
    """
    db = get_database()
    now = datetime.now(timezone.utc)

    visit = await get_visit_doc(db, visit_id)
    if not visit:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Visit '{visit_id}' not found.")

    reason = payload.get("reason", "Client decided not to proceed after site visit.")
    remarks = payload.get("remarks")

    # Update enquiry
    await db.enquiries.update_one(
        {"enquiry_id": visit["enquiry_id"]},
        {
            "$set": {
                "status": "aborted",
                "abort_reason": reason,
                "final_remarks": remarks,
                "aborted_at": now,
                "updated_at": now,
                "updated_by": str(current_user["_id"]),
            }
        },
    )

    # Update visit
    updated_doc = await db.visits.find_one_and_update(
        {"_id": visit["_id"]},
        {
            "$set": {
                "status": "completed",
                "outcome": f"Enquiry Aborted: {reason}",
                "remarks": remarks or reason,
                "completed_at": now,
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )

    await db.activity_events.insert_one({
        "event_type": "enquiry.aborted_from_visit",
        "entity_type": "enquiry",
        "entity_id": visit["enquiry_id"],
        "actor_id": str(current_user["_id"]),
        "actor_role": current_user.get("role", "executive"),
        "details": {"visit_id": visit["visit_id"], "reason": reason},
        "created_at": now,
    })

    return await visit_doc_to_response(db, updated_doc)

