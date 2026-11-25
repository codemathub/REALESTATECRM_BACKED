import math
import re
from datetime import datetime, timezone
from typing import Optional
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.app.core.deps import get_current_user, require_roles
from backend.app.core.database import get_database
from backend.app.core.datetime_utils import ensure_utc
from backend.app.schemas.enquiry import (
    EnquiryCreate,
    EnquiryUpdate,
    EnquiryStatusChange,
    EnquiryResponse,
    EnquiryListResponse,
)
from backend.app.schemas.deal import DealCloseRequest, EnquiryAbortRequest, DealResponse
from backend.app.services.property_service import normalize_phone, log_activity
from backend.app.services.sequence_service import generate_enquiry_id, generate_deal_id
from backend.app.services.notification_service import create_in_app_notification, broadcast_notification


router = APIRouter()


async def get_enquiry_doc(db, identifier: str) -> Optional[dict]:
    clean = str(identifier).strip()
    query_list = [
        {"enquiry_id": clean},
        {"enquiry_id": clean.upper()},
        {"enquiry_id": {"$regex": f"^{re.escape(clean)}$", "$options": "i"}},
    ]
    if ObjectId.is_valid(clean):
        query_list.append({"_id": ObjectId(clean)})
    return await db.enquiries.find_one({"$or": query_list})


def enquiry_doc_to_response(doc: dict) -> EnquiryResponse:
    return EnquiryResponse(
        id=str(doc["_id"]),
        enquiry_id=doc["enquiry_id"],
        customer_id=doc["customer_id"],
        customer_name=doc.get("customer_name", "Unknown"),
        customer_phone=doc.get("customer_phone", ""),
        customer_phone_normalized=doc.get("customer_phone_normalized", ""),
        customer_type=doc.get("customer_type", "tenant"),
        transaction_type=doc.get("transaction_type", "rent"),
        looking_for=doc.get("looking_for", ""),
        preferred_locations=doc.get("preferred_locations", []),
        preferred_societies=doc.get("preferred_societies", []),
        budget_min=float(doc.get("budget_min", 0.0)),
        budget_max=float(doc.get("budget_max", 0.0)),
        maintenance_preference=doc.get("maintenance_preference", "included"),
        tenant_type=doc.get("tenant_type", "Family"),
        priority=doc.get("priority", "warm"),
        lead_source=doc.get("lead_source"),
        status=doc.get("status", "new"),
        abort_reason=doc.get("abort_reason"),
        responsible_employee_id=doc.get("responsible_employee_id"),
        responsible_employee_name=doc.get("responsible_employee_name"),
        notes=doc.get("notes"),
        last_followup_at=ensure_utc(doc.get("last_followup_at")),
        next_followup_at=ensure_utc(doc.get("next_followup_at")),
        created_by=str(doc.get("created_by", "")),
        created_by_name=doc.get("created_by_name"),
        updated_by=str(doc.get("updated_by", "")) if doc.get("updated_by") else None,
        created_at=ensure_utc(doc.get("created_at")) or datetime.now(timezone.utc),
        updated_at=ensure_utc(doc.get("updated_at")) or datetime.now(timezone.utc),
        archived_at=ensure_utc(doc.get("archived_at")),
    )


@router.get("", response_model=EnquiryListResponse)
async def list_enquiries(
    search: Optional[str] = Query(None, description="Search by customer name, phone, enquiry ID, or requirement"),
    customer_id: Optional[str] = None,
    transaction_type: Optional[str] = Query(None, pattern="^(rent|sale)$"),
    looking_for: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    min_budget: Optional[float] = Query(None, ge=0),
    max_budget: Optional[float] = Query(None, ge=0),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    active_only: Optional[bool] = Query(None, description="Filter only active enquiries"),
    current_user: dict = Depends(get_current_user),
):
    """PRD REQ-ENQ-01: Query and filter company customer enquiries."""
    db = get_database()
    query: dict = {}

    if customer_id:
        query["customer_id"] = customer_id
    if transaction_type:
        query["transaction_type"] = transaction_type
    if looking_for:
        query["looking_for"] = looking_for
    if status:
        query["status"] = status
    elif active_only:
        query["status"] = {"$nin": ["deal_closed", "aborted", "cancelled", "lost", "archived"]}
    if priority:
        query["priority"] = priority

    if min_budget is not None or max_budget is not None:
        if min_budget is not None and max_budget is not None:
            query["budget_min"] = {"$lte": max_budget}
            query["budget_max"] = {"$gte": min_budget}
        elif min_budget is not None:
            query["budget_max"] = {"$gte": min_budget}
        elif max_budget is not None:
            query["budget_min"] = {"$lte": max_budget}

    if search and search.strip():
        term = search.strip()
        escaped = re.escape(term)
        normalized_num = normalize_phone(term)
        or_clauses = [
            {"enquiry_id": {"$regex": escaped, "$options": "i"}},
            {"customer_name": {"$regex": escaped, "$options": "i"}},
            {"looking_for": {"$regex": escaped, "$options": "i"}},
            {"preferred_locations": {"$regex": escaped, "$options": "i"}},
        ]
        if normalized_num:
            or_clauses.append({"customer_phone_normalized": {"$regex": normalized_num}})
        query["$or"] = or_clauses

    skip = (page - 1) * limit
    total = await db.enquiries.count_documents(query)
    cursor = db.enquiries.find(query).sort([("created_at", -1)]).skip(skip).limit(limit)
    items_docs = await cursor.to_list(length=limit)

    return EnquiryListResponse(
        items=[enquiry_doc_to_response(d) for d in items_docs],
        total=total,
        page=page,
        limit=limit,
        pages=math.ceil(total / limit) if total > 0 else 0,
    )


@router.post("", response_model=EnquiryResponse, status_code=status.HTTP_201_CREATED)
async def create_enquiry(
    enq_in: EnquiryCreate,
    current_user: dict = Depends(get_current_user),
):
    """
    PRD REQ-ENQ-02: Create enquiry linked to a customer profile.
    One customer can own multiple enquiries.
    """
    db = get_database()

    # Look up customer
    clean_cid = str(enq_in.customer_id).strip()
    cust_query_list = [
        {"customer_id": clean_cid},
        {"customer_id": clean_cid.upper()},
        {"customer_id": {"$regex": f"^{re.escape(clean_cid)}$", "$options": "i"}},
    ]
    if ObjectId.is_valid(clean_cid):
        cust_query_list.append({"_id": ObjectId(clean_cid)})
    customer = await db.customers.find_one({"$or": cust_query_list})
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Customer with ID '{enq_in.customer_id}' does not exist.",
        )

    enquiry_id = await generate_enquiry_id(db)
    now = datetime.now(timezone.utc)

    doc = enq_in.model_dump()
    doc["enquiry_id"] = enquiry_id
    doc["customer_id"] = customer.get("customer_id") or str(customer["_id"])
    doc["customer_name"] = customer["name"]
    doc["customer_phone"] = customer["primary_phone"]
    doc["customer_phone_normalized"] = customer.get("primary_phone_normalized", normalize_phone(customer["primary_phone"]))
    doc["customer_type"] = customer.get("customer_type", "tenant")
    doc["created_by"] = str(current_user["_id"])
    doc["created_by_name"] = current_user.get("full_name", "")
    doc["created_at"] = now
    doc["updated_at"] = now

    result = await db.enquiries.insert_one(doc)
    doc["_id"] = result.inserted_id

    # Broadcast notification to admins and staff
    await broadcast_notification(
        db=db,
        target_roles=["super_admin", "admin", "executive"],
        title=f"New Enquiry: {customer.get('name')} • {enq_in.looking_for}",
        body=f"Budget: ₹{enq_in.budget_min:,.0f} - ₹{enq_in.budget_max:,.0f} | Loc: {', '.join(enq_in.preferred_locations[:2]) if enq_in.preferred_locations else 'Any'}",
        notification_type="lead_assigned",
        entity_type="enquiry",
        entity_id=enquiry_id,
    )

    await log_activity(
        db=db,
        event_type="enquiry.created",
        entity_type="enquiry",
        entity_id=enquiry_id,
        actor_id=str(current_user["_id"]),
        actor_name=current_user.get("full_name", ""),
        actor_role=current_user.get("role", ""),
        details={
            "customer_id": customer["customer_id"],
            "looking_for": enq_in.looking_for,
            "budget": f"{enq_in.budget_min}-{enq_in.budget_max}",
            "status": enq_in.status,
        },
    )

    return enquiry_doc_to_response(doc)


@router.get("/{enquiry_id}", response_model=EnquiryResponse)
async def get_enquiry(
    enquiry_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Retrieve details of a single enquiry record."""
    db = get_database()
    doc = await get_enquiry_doc(db, enquiry_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Enquiry '{enquiry_id}' not found.")
    return enquiry_doc_to_response(doc)


@router.patch("/{enquiry_id}", response_model=EnquiryResponse)
async def update_enquiry(
    enquiry_id: str,
    enq_update: EnquiryUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Update mutable enquiry fields."""
    db = get_database()
    doc = await get_enquiry_doc(db, enquiry_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Enquiry '{enquiry_id}' not found.")

    update_fields = enq_update.model_dump(exclude_unset=True)
    if not update_fields:
        return enquiry_doc_to_response(doc)

    now = datetime.now(timezone.utc)
    update_fields["updated_at"] = now
    update_fields["updated_by"] = str(current_user["_id"])

    await db.enquiries.update_one({"enquiry_id": doc["enquiry_id"]}, {"$set": update_fields})
    updated = await db.enquiries.find_one({"enquiry_id": doc["enquiry_id"]})

    await log_activity(
        db=db,
        event_type="enquiry.updated",
        entity_type="enquiry",
        entity_id=doc["enquiry_id"],
        actor_id=str(current_user["_id"]),
        actor_name=current_user.get("full_name", ""),
        actor_role=current_user.get("role", ""),
        details={"updated_fields": list(update_fields.keys())},
    )

    return enquiry_doc_to_response(updated)


@router.post("/{enquiry_id}/status", response_model=EnquiryResponse)
async def change_enquiry_status(
    enquiry_id: str,
    status_in: EnquiryStatusChange,
    current_user: dict = Depends(get_current_user),
):
    """Change enquiry status (e.g. to deal_closed, aborted with reason, etc.)."""
    db = get_database()
    doc = await get_enquiry_doc(db, enquiry_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Enquiry '{enquiry_id}' not found.")

    now = datetime.now(timezone.utc)
    update_doc: dict = {
        "status": status_in.status,
        "updated_at": now,
        "updated_by": str(current_user["_id"]),
    }
    if status_in.status == "aborted":
        update_doc["abort_reason"] = status_in.reason or "No reason specified"
    if status_in.notes:
        update_doc["notes"] = f"{doc.get('notes', '')}\n[{status_in.status.upper()}]: {status_in.notes}".strip()

    await db.enquiries.update_one({"enquiry_id": doc["enquiry_id"]}, {"$set": update_doc})
    updated = await db.enquiries.find_one({"enquiry_id": doc["enquiry_id"]})

    await log_activity(
        db=db,
        event_type="enquiry.status_changed",
        entity_type="enquiry",
        entity_id=doc["enquiry_id"],
        actor_id=str(current_user["_id"]),
        actor_name=current_user.get("full_name", ""),
        actor_role=current_user.get("role", ""),
        details={"old_status": doc.get("status"), "new_status": status_in.status, "reason": status_in.reason},
    )

    return enquiry_doc_to_response(updated)


@router.post("/{enquiry_id}/close-deal", response_model=DealResponse)
async def close_enquiry_deal(
    enquiry_id: str,
    payload: DealCloseRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    PRD Section 5.15 & Phase 11: Deal Close Lifecycle.
    - Sets enquiry status to deal_closed.
    - Automatically updates property status to Rented (for rent) or Sold (for sale).
    - Persists an immutable deal record in the 'deals' collection.
    - If initiated from a visit, updates linked visit outcome.
    - Broadcasts notification and writes audit log.
    """
    db = get_database()
    now = datetime.now(timezone.utc)

    enquiry = await get_enquiry_doc(db, enquiry_id)
    if not enquiry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Enquiry '{enquiry_id}' not found.")

    if enquiry.get("status") == "deal_closed":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Enquiry is already closed.")

    # 1. Look up property
    prop_id_clean = payload.property_id.strip().upper()
    prop = await db.properties.find_one({"property_id": prop_id_clean})
    if not prop:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Property '{prop_id_clean}' does not exist in inventory.",
        )

    # 2. Determine target status based on enquiry transaction type
    trans_type = enquiry.get("transaction_type", "rent").lower()
    target_property_status = "rented" if trans_type == "rent" else "sold"

    # 3. Update property status atomically
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

    # 4. Generate sequential Deal ID and persist deal document
    deal_id = await generate_deal_id(db)
    deal_amount = payload.deal_amount if payload.deal_amount is not None else float(prop.get("expected_amount", 0.0))
    exec_id = payload.responsible_executive_id or enquiry.get("responsible_employee_id") or str(current_user["_id"])
    exec_name = enquiry.get("responsible_employee_name") or current_user.get("full_name", "Staff Member")

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
        "deal_date": payload.deal_date or now,
        "responsible_executive_id": str(exec_id),
        "responsible_executive_name": exec_name,
        "final_remarks": payload.final_remarks or "Deal closed successfully.",
        "created_at": now,
    }
    deal_res = await db.deals.insert_one(deal_doc)
    deal_doc["_id"] = deal_res.inserted_id

    # 5. Update enquiry to deal_closed
    await db.enquiries.update_one(
        {"enquiry_id": enquiry["enquiry_id"]},
        {
            "$set": {
                "status": "deal_closed",
                "closed_at": now,
                "deal_id": deal_id,
                "closed_property_id": prop["property_id"],
                "final_remarks": payload.final_remarks,
                "updated_at": now,
                "updated_by": str(current_user["_id"]),
            }
        },
    )

    # 6. If visit_id provided, mark linked visit as completed with outcome
    if payload.visit_id:
        v_clean = payload.visit_id.strip().upper()
        await db.visits.update_one(
            {"visit_id": v_clean},
            {
                "$set": {
                    "status": "completed",
                    "outcome": f"Deal Closed ({deal_id} • {prop['property_id']})",
                    "remarks": payload.final_remarks or "Visit culminated in closed deal.",
                    "completed_at": now,
                    "updated_at": now,
                }
            },
        )

    # 7. Broadcast in-app & push notification
    await broadcast_notification(
        db=db,
        target_roles=["super_admin", "admin"],
        title="Deal Closed! 🏆",
        body=f"{enquiry.get('customer_name')} closed deal on {prop['property_id']} ({target_property_status.capitalize()}) by {exec_name}.",
        notification_type="deal_closed",
        entity_type="deal",
        entity_id=deal_id,
    )

    # 8. Log audit trail
    await log_activity(
        db=db,
        event_type="deal.closed",
        entity_type="deal",
        entity_id=deal_id,
        actor_id=str(current_user["_id"]),
        actor_name=current_user.get("full_name", ""),
        actor_role=current_user.get("role", ""),
        details={
            "deal_id": deal_id,
            "enquiry_id": enquiry["enquiry_id"],
            "property_id": prop["property_id"],
            "property_status": target_property_status,
            "deal_amount": deal_amount,
            "customer_id": enquiry["customer_id"],
        },
    )

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


@router.post("/{enquiry_id}/abort", response_model=EnquiryResponse)
async def abort_enquiry(
    enquiry_id: str,
    payload: EnquiryAbortRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    PRD Section 5.15: Abort Enquiry.
    - Requires mandatory abort reason and optional remarks.
    - Sets enquiry status to 'aborted' while preserving all history and searchability.
    """
    db = get_database()
    now = datetime.now(timezone.utc)

    enquiry = await get_enquiry_doc(db, enquiry_id)
    if not enquiry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Enquiry '{enquiry_id}' not found.")

    update_fields = {
        "status": "aborted",
        "abort_reason": payload.reason.strip(),
        "final_remarks": payload.final_remarks,
        "aborted_at": now,
        "updated_at": now,
        "updated_by": str(current_user["_id"]),
    }

    await db.enquiries.update_one({"enquiry_id": enquiry["enquiry_id"]}, {"$set": update_fields})
    updated = await db.enquiries.find_one({"enquiry_id": enquiry["enquiry_id"]})

    # If linked visit_id provided, update visit outcome
    if payload.visit_id:
        v_clean = payload.visit_id.strip().upper()
        await db.visits.update_one(
            {"visit_id": v_clean},
            {
                "$set": {
                    "outcome": f"Enquiry Aborted: {payload.reason}",
                    "remarks": payload.final_remarks or "Visit concluded with aborted enquiry.",
                    "status": "cancelled",
                    "cancellation_reason": payload.reason,
                    "updated_at": now,
                }
            },
        )

    await log_activity(
        db=db,
        event_type="enquiry.aborted",
        entity_type="enquiry",
        entity_id=enquiry["enquiry_id"],
        actor_id=str(current_user["_id"]),
        actor_name=current_user.get("full_name", ""),
        actor_role=current_user.get("role", ""),
        details={"reason": payload.reason, "remarks": payload.final_remarks},
    )

    return enquiry_doc_to_response(updated)


@router.post("/{enquiry_id}/restore", response_model=EnquiryResponse)
async def restore_enquiry(
    enquiry_id: str,
    current_user: dict = Depends(require_roles(["super_admin", "admin"])),
):
    """
    PRD Section 5.16: Restore aborted or archived enquiry back to active pipeline.
    """
    db = get_database()
    now = datetime.now(timezone.utc)

    enquiry = await get_enquiry_doc(db, enquiry_id)
    if not enquiry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Enquiry '{enquiry_id}' not found.")

    update_fields = {
        "status": "contacted",
        "abort_reason": None,
        "archived_at": None,
        "restored_at": now,
        "updated_at": now,
        "updated_by": str(current_user["_id"]),
    }

    await db.enquiries.update_one({"enquiry_id": enquiry["enquiry_id"]}, {"$set": update_fields})
    updated = await db.enquiries.find_one({"enquiry_id": enquiry["enquiry_id"]})

    await log_activity(
        db=db,
        event_type="enquiry.restored",
        entity_type="enquiry",
        entity_id=enquiry["enquiry_id"],
        actor_id=str(current_user["_id"]),
        actor_name=current_user.get("full_name", ""),
        actor_role=current_user.get("role", ""),
        details={"restored_status": "contacted", "previous_status": enquiry.get("status")},
    )

    return enquiry_doc_to_response(updated)


@router.get("/{enquiry_id}/timeline")
async def get_enquiry_timeline(
    enquiry_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Retrieve full chronological action timeline of an enquiry across all staff interactions:
    Status changes, site visits, follow-up calls, notes, and deal progress.
    """
    db = get_database()
    doc = await get_enquiry_doc(db, enquiry_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Enquiry '{enquiry_id}' not found.")

    # Pre-fetch users to resolve real human names (never generic "Field Executive" or "Staff Member")
    users_list = await db.users.find({}).to_list(200)
    user_name_map = {}
    for u in users_list:
        uid = str(u["_id"])
        uname = u.get("full_name") or u.get("name") or u.get("email") or ""
        if uname:
            user_name_map[uid] = uname
            if u.get("email"):
                user_name_map[u["email"]] = uname
    fallback_user_name = next((u.get("full_name") or u.get("name") for u in users_list if u.get("full_name") or u.get("name")), current_user.get("full_name", "Admin"))

    events = []

    # 1. Activity events
    activity_cursor = db.activity_events.find({
        "entity_type": "enquiry",
        "entity_id": doc["enquiry_id"],
    }).sort("created_at", -1).limit(50)
    for act in await activity_cursor.to_list(length=50):
        details = act.get("details", {})
        desc = details.get("reason") or details.get("notes") or (
            f"Changed from {details.get('old_status')} to {details.get('new_status')}" if "new_status" in details else ""
        )
        ts = ensure_utc(act.get("created_at"))
        act_user_id = str(act.get("user_id") or act.get("actor_id") or "")
        act_actor = (
            user_name_map.get(act_user_id)
            or act.get("actor_name")
            or current_user.get("full_name")
            or fallback_user_name
        )
        events.append({
            "id": str(act["_id"]),
            "type": "activity",
            "event_type": act.get("event_type", "action"),
            "actor_name": act_actor,
            "actor_role": act.get("actor_role") or "staff",
            "title": act.get("event_type", "").replace(".", " ").title(),
            "description": desc or "Action recorded on enquiry",
            "timestamp": ts.isoformat() if ts else None,
        })

    # 2. Visits linked to this enquiry
    visits_cursor = db.visits.find({"enquiry_id": doc["enquiry_id"]}).sort("created_at", -1).limit(20)
    for v in await visits_cursor.to_list(length=20):
        v_title = f"Site Visit #{v.get('visit_id')} • {v.get('property_society') or v.get('property_id')}"
        v_desc = f"Status: {v.get('status', '').title()}"
        if v.get("outcome"):
            v_desc += f" | Outcome: {v.get('outcome')}"
        if v.get("remarks"):
            v_desc += f" | Remarks: {v.get('remarks')}"

        v_ts = ensure_utc(v.get("created_at") or v.get("scheduled_at"))
        v_sched = ensure_utc(v.get("scheduled_at"))
        assigned_val = str(v.get("assigned_to") or "")
        claimed_val = str(v.get("claimed_by") or "")
        created_val = str(v.get("created_by") or "")
        v_actor = (
            user_name_map.get(assigned_val)
            or user_name_map.get(claimed_val)
            or user_name_map.get(created_val)
            or v.get("assigned_name")
            or v.get("claimed_by_name")
            or current_user.get("full_name")
            or fallback_user_name
        )
        events.append({
            "id": str(v["_id"]),
            "type": "visit",
            "event_type": "site_visit",
            "actor_name": v_actor,
            "actor_role": "executive",
            "title": v_title,
            "description": v_desc,
            "timestamp": v_ts.isoformat() if v_ts else None,
            "scheduled_at": v_sched.isoformat() if v_sched else None,
        })

    # 3. Followups linked to this enquiry
    followups_cursor = db.followups.find({"enquiry_id": doc["enquiry_id"]}).sort("created_at", -1).limit(20)
    for f in await followups_cursor.to_list(length=20):
        f_desc = f.get("notes", "Follow-up logged")
        if f.get("remarks"):
            f_desc += f" | Response: {f.get('remarks')}"
        f_ts = ensure_utc(f.get("created_at") or f.get("scheduled_at"))
        resp_id = str(f.get("responsible_user_id") or f.get("created_by") or "")
        f_actor = (
            user_name_map.get(resp_id)
            or f.get("responsible_name")
            or f.get("creator_name")
            or current_user.get("full_name")
            or fallback_user_name
        )
        events.append({
            "id": str(f["_id"]),
            "type": "followup",
            "event_type": "followup_call",
            "actor_name": f_actor,
            "actor_role": "executive",
            "title": f"Follow-up Call • {f.get('status', '').title()}",
            "description": f_desc,
            "timestamp": f_ts.isoformat() if f_ts else None,
        })

    # Sort all events chronologically descending (newest first)
    events.sort(key=lambda x: str(x.get("timestamp") or ""), reverse=True)
    return {"enquiry_id": doc["enquiry_id"], "events": events}

