import math
import re
from datetime import datetime, timezone
from typing import Optional, List
from bson import ObjectId
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.app.core.deps import get_current_user, require_roles
from backend.app.core.database import get_database
from backend.app.schemas.customer import (
    CustomerCreate,
    CustomerUpdate,
    CustomerResponse,
    CustomerMatchItem,
    DuplicatePhoneCheckResponse,
)
from backend.app.services.property_service import normalize_phone, log_activity
from backend.app.services.sequence_service import generate_customer_id
from backend.app.services.matching_service import find_matching_properties

router = APIRouter()


async def get_customer_doc(db, identifier: str) -> Optional[dict]:
    identifier_clean = identifier.strip()
    query_or = [
        {"customer_id": identifier_clean.upper()},
        {"customer_id": identifier_clean},
        {"customer_id": {"$regex": f"^{re.escape(identifier_clean)}$", "$options": "i"}},
    ]
    if ObjectId.is_valid(identifier_clean):
        query_or.append({"_id": ObjectId(identifier_clean)})
    
    doc = await db.customers.find_one({"$or": query_or})
    if not doc:
        # Also match by phone or name
        phone_norm = re.sub(r"\D", "", identifier_clean)
        phone_query = [{"primary_phone": identifier_clean}]
        if phone_norm:
            phone_query.append({"primary_phone_normalized": phone_norm})
            phone_query.append({"primary_phone": {"$regex": re.escape(phone_norm)}})
        phone_query.append({"name": {"$regex": f"^{re.escape(identifier_clean)}$", "$options": "i"}})
        doc = await db.customers.find_one({"$or": phone_query})

    if not doc:
        # Check if identifier is an enquiry ID linked to a customer
        enq = await db.enquiries.find_one({"enquiry_id": {"$regex": f"^{re.escape(identifier_clean)}$", "$options": "i"}})
        if enq and enq.get("customer_id"):
            doc = await get_customer_doc(db, enq["customer_id"])

    return doc


def customer_doc_to_response(doc: dict, enquiries_count: int = 0) -> CustomerResponse:
    return CustomerResponse(
        id=str(doc["_id"]),
        customer_id=doc["customer_id"],
        name=doc["name"],
        primary_phone=doc["primary_phone"],
        primary_phone_normalized=doc.get("primary_phone_normalized", ""),
        alternate_phone=doc.get("alternate_phone"),
        alternate_phone_normalized=doc.get("alternate_phone_normalized"),
        whatsapp_phone=doc.get("whatsapp_phone"),
        whatsapp_phone_normalized=doc.get("whatsapp_phone_normalized"),
        customer_type=doc.get("customer_type", "tenant"),
        lead_source=doc.get("lead_source", "Walk-in"),
        priority=doc.get("priority", "warm"),
        notes=doc.get("notes"),
        shared_phone_reason=doc.get("shared_phone_reason"),
        enquiries_count=enquiries_count,
        created_by=str(doc.get("created_by", "")),
        created_by_name=doc.get("created_by_name"),
        created_at=doc.get("created_at") or datetime.now(timezone.utc),
        updated_at=doc.get("updated_at") or datetime.now(timezone.utc),
    )


@router.get("/check-phone", response_model=DuplicatePhoneCheckResponse)
async def check_duplicate_phone(
    phone: str = Query(..., min_length=4, description="Phone number to check for duplicates"),
    current_user: dict = Depends(get_current_user),
):
    """
    PRD Section 5.8: Query backend for duplicate phone matches using normalized phone values.
    Enables frontend to offer the 3 approved choices:
    1) Open existing customer
    2) Add new enquiry to existing customer
    3) Create a separate profile with shared phone reason
    """
    db = get_database()
    normalized = normalize_phone(phone)
    if not normalized or len(normalized) < 4:
        return DuplicatePhoneCheckResponse(has_duplicate=False, matches=[])

    cursor = db.customers.find({
        "$or": [
            {"primary_phone_normalized": normalized},
            {"alternate_phone_normalized": normalized},
            {"whatsapp_phone_normalized": normalized},
        ]
    })
    matches_docs = await cursor.to_list(length=20)

    matches: List[CustomerMatchItem] = []
    for d in matches_docs:
        cid = d["customer_id"]
        enq_count = await db.enquiries.count_documents({
            "$or": [{"customer_id": cid}, {"customer_id": str(d["_id"])}]
        })
        matches.append(
            CustomerMatchItem(
                id=str(d["_id"]),
                customer_id=cid,
                name=d["name"],
                primary_phone=d["primary_phone"],
                primary_phone_normalized=d.get("primary_phone_normalized", ""),
                customer_type=d.get("customer_type", "tenant"),
                enquiries_count=enq_count,
                created_at=d.get("created_at") or datetime.now(timezone.utc),
            )
        )

    return DuplicatePhoneCheckResponse(
        has_duplicate=len(matches) > 0,
        matches=matches,
    )


@router.get("", response_model=dict)
async def list_customers(
    search: Optional[str] = Query(None, description="Search by customer name, ID, or phone"),
    customer_type: Optional[str] = None,
    priority: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """PRD REQ-CUS-01: List and search customers across company directory."""
    db = get_database()
    query: dict = {}

    if customer_type:
        query["customer_type"] = customer_type
    if priority:
        query["priority"] = priority

    if search and search.strip():
        term = search.strip()
        escaped = re.escape(term)
        normalized_num = normalize_phone(term)
        or_clauses = [
            {"name": {"$regex": escaped, "$options": "i"}},
            {"customer_id": {"$regex": escaped, "$options": "i"}},
        ]
        if normalized_num:
            or_clauses.append({"primary_phone_normalized": {"$regex": normalized_num}})
        query["$or"] = or_clauses

    skip = (page - 1) * limit
    total = await db.customers.count_documents(query)
    cursor = db.customers.find(query).sort([("created_at", -1)]).skip(skip).limit(limit)
    items_docs = await cursor.to_list(length=limit)

    results = []
    for d in items_docs:
        cid = d["customer_id"]
        enq_count = await db.enquiries.count_documents({
            "$or": [{"customer_id": cid}, {"customer_id": str(d["_id"])}]
        })
        results.append(customer_doc_to_response(d, enq_count))

    return {
        "items": [r.model_dump() for r in results],
        "total": total,
        "page": page,
        "limit": limit,
        "pages": math.ceil(total / limit) if total > 0 else 0,
    }


@router.post("", response_model=CustomerResponse, status_code=status.HTTP_201_CREATED)
async def create_customer(
    customer_in: CustomerCreate,
    current_user: dict = Depends(get_current_user),
):
    """
    PRD REQ-CUS-02 & Section 5.8: Create customer profile with normalized phones.
    If phone number matches an existing customer, requires `shared_phone_reason` or returns 409 Conflict.
    """
    db = get_database()
    primary_norm = normalize_phone(customer_in.primary_phone)
    alt_norm = normalize_phone(customer_in.alternate_phone) if customer_in.alternate_phone else None
    wa_norm = normalize_phone(customer_in.whatsapp_phone) if customer_in.whatsapp_phone else None

    # Check for duplicate primary phone
    existing_match = await db.customers.find_one({"primary_phone_normalized": primary_norm})
    if existing_match and not customer_in.shared_phone_reason:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": f"A customer profile with phone '{customer_in.primary_phone}' already exists: {existing_match['name']} ({existing_match['customer_id']}).",
                "existing_customer_id": existing_match["customer_id"],
                "existing_customer_name": existing_match["name"],
                "action_required": "Choose to add enquiry to existing customer or provide a 'shared_phone_reason'.",
            },
        )

    customer_id = await generate_customer_id(db)
    now = datetime.now(timezone.utc)

    doc = customer_in.model_dump()
    doc["customer_id"] = customer_id
    doc["primary_phone_normalized"] = primary_norm
    doc["alternate_phone_normalized"] = alt_norm
    doc["whatsapp_phone_normalized"] = wa_norm
    doc["created_by"] = str(current_user["_id"])
    doc["created_by_name"] = current_user.get("full_name", "")
    doc["created_at"] = now
    doc["updated_at"] = now

    result = await db.customers.insert_one(doc)
    doc["_id"] = result.inserted_id

    await log_activity(
        db=db,
        event_type="customer.created",
        entity_type="customer",
        entity_id=customer_id,
        actor_id=str(current_user["_id"]),
        actor_name=current_user.get("full_name", ""),
        actor_role=current_user.get("role", ""),
        details={"name": customer_in.name, "primary_phone": customer_in.primary_phone, "customer_type": customer_in.customer_type},
    )

    return customer_doc_to_response(doc, enquiries_count=0)


@router.get("/{customer_id}", response_model=dict)
async def get_customer(
    customer_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Retrieve customer details along with all their linked enquiries."""
    db = get_database()
    doc = await get_customer_doc(db, customer_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Customer '{customer_id}' not found.")

    cid = doc["customer_id"]
    enq_cursor = db.enquiries.find({
        "$or": [{"customer_id": cid}, {"customer_id": str(doc["_id"])}]
    }).sort([("created_at", -1)])
    enquiries_docs = await enq_cursor.to_list(length=100)

    # Format enquiries
    formatted_enquiries = []
    for eq in enquiries_docs:
        eq_id = str(eq["_id"])
        eq["id"] = eq_id
        eq["_id"] = eq_id
        formatted_enquiries.append(eq)

    customer_resp = customer_doc_to_response(doc, enquiries_count=len(formatted_enquiries))
    return {
        "customer": customer_resp.model_dump(),
        "enquiries": formatted_enquiries,
    }


@router.get("/{customer_id}/360", summary="Customer 360-Degree Panoramic Dossier")
async def get_customer_360(
    customer_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    360-Degree Customer Dossier:
    Returns complete panoramic view including profile, all enquiries,
    all site visits, all follow-ups & notes, and matching available properties.
    """
    db = get_database()
    doc = await get_customer_doc(db, customer_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Customer '{customer_id}' not found.")

    cid = doc["customer_id"]
    phone = doc.get("primary_phone")
    phone_norm = doc.get("primary_phone_normalized")

    # Pre-fetch system users to resolve actual human names and roles (never generic placeholders)
    users_list = await db.users.find({}).to_list(200)
    user_name_map = {}
    user_role_map = {}
    for u in users_list:
        uid = str(u["_id"])
        uname = u.get("full_name") or u.get("name") or u.get("email") or "Executive"
        urole = u.get("role", "executive")
        user_name_map[uid] = uname
        user_role_map[uid] = urole
        if u.get("email"):
            user_name_map[u["email"]] = uname
            user_role_map[u["email"]] = urole

    # 1. Linked Enquiries
    enq_query = {"$or": [{"customer_id": cid}, {"customer_id": str(doc["_id"])}]}
    if phone_norm:
        enq_query["$or"].append({"customer_phone_normalized": phone_norm})
    enquiries_docs = await db.enquiries.find(enq_query).sort([("created_at", -1)]).to_list(100)

    formatted_enquiries = []
    matched_properties_dict = {}
    for eq in enquiries_docs:
        eq["id"] = str(eq["_id"])
        eq["_id"] = str(eq["_id"])
        # Resolve real creator name & role ("who added this requirement")
        creator_id = str(eq.get("created_by") or eq.get("assigned_to") or "")
        creator_name = user_name_map.get(creator_id) or eq.get("created_by_name") or eq.get("creator_name") or current_user.get("full_name", "Admin")
        creator_role = user_role_map.get(creator_id) or eq.get("created_by_role") or "executive"
        eq["created_by_name"] = creator_name
        eq["created_by_role"] = creator_role
        formatted_enquiries.append(eq)

        # Get matching properties for active enquiries
        if eq.get("status") not in ["closed_won", "closed_lost", "cancelled"]:
            try:
                matches = await find_matching_properties(db, eq, limit=5)
                for m in matches:
                    pid = m.property.property_id
                    if pid not in matched_properties_dict or m.match_score > matched_properties_dict[pid]["match_score"]:
                        matched_properties_dict[pid] = {
                            "property": m.property.model_dump(),
                            "match_score": m.match_score,
                            "match_reasons": m.match_reasons,
                            "for_enquiry": eq.get("enquiry_id"),
                        }
            except Exception:
                pass

    # 2. Site Visits
    visit_query = {"$or": [{"customer_id": cid}]}
    if phone:
        visit_query["$or"].append({"customer_phone": phone})
    visits_docs = await db.visits.find(visit_query).sort([("scheduled_at", -1)]).to_list(100)
    formatted_visits = []
    for v in visits_docs:
        v["id"] = str(v["_id"])
        v["_id"] = str(v["_id"])
        # Resolve real human executive name
        assigned_val = str(v.get("assigned_to") or "")
        claimed_val = str(v.get("claimed_by") or "")
        created_val = str(v.get("created_by") or "")
        real_exec = (
            user_name_map.get(assigned_val)
            or user_name_map.get(claimed_val)
            or user_name_map.get(created_val)
            or v.get("agent_name")
            or v.get("assigned_name")
            or v.get("claimed_by_name")
            or v.get("created_by_name")
            or current_user.get("full_name", "Executive")
        )
        v["assigned_name"] = real_exec
        v["claimed_by_name"] = user_name_map.get(claimed_val) or v.get("claimed_by_name")
        formatted_visits.append(v)

    # 3. Follow-ups & Executive Notes
    fu_query = {"$or": [{"customer_id": cid}]}
    if phone:
        fu_query["$or"].append({"customer_phone": phone})
    followups_docs = await db.followups.find(fu_query).sort([("scheduled_date", -1), ("created_at", -1)]).to_list(100)
    formatted_followups = []
    for f in followups_docs:
        f["id"] = str(f["_id"])
        f["_id"] = str(f["_id"])
        formatted_followups.append(f)

    # 4. Summary Stats
    active_enquiries_count = sum(1 for e in formatted_enquiries if e.get("status") not in ["closed_won", "closed_lost", "cancelled"])
    pending_visits_count = sum(1 for v in formatted_visits if v.get("status") in ["scheduled", "in_progress"])
    pending_followups_count = sum(1 for f in formatted_followups if f.get("status") in ["pending", "overdue"])

    customer_resp = customer_doc_to_response(doc, enquiries_count=len(formatted_enquiries))

    return {
        "customer": customer_resp.model_dump(),
        "enquiries": formatted_enquiries,
        "visits": formatted_visits,
        "followups": formatted_followups,
        "matched_properties": list(matched_properties_dict.values()),
        "stats": {
            "total_enquiries": len(formatted_enquiries),
            "active_enquiries": active_enquiries_count,
            "total_visits": len(formatted_visits),
            "pending_visits": pending_visits_count,
            "total_followups": len(formatted_followups),
            "pending_followups": pending_followups_count,
            "matched_properties_count": len(matched_properties_dict),
        },
    }


@router.patch("/{customer_id}", response_model=CustomerResponse)
async def update_customer(
    customer_id: str,
    customer_update: CustomerUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Update customer details."""
    db = get_database()
    doc = await get_customer_doc(db, customer_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Customer '{customer_id}' not found.")

    update_fields = customer_update.model_dump(exclude_unset=True)
    if not update_fields:
        return customer_doc_to_response(doc)

    if "primary_phone" in update_fields:
        update_fields["primary_phone_normalized"] = normalize_phone(update_fields["primary_phone"])
    if "alternate_phone" in update_fields:
        update_fields["alternate_phone_normalized"] = normalize_phone(update_fields["alternate_phone"])
    if "whatsapp_phone" in update_fields:
        update_fields["whatsapp_phone_normalized"] = normalize_phone(update_fields["whatsapp_phone"])

    now = datetime.now(timezone.utc)
    update_fields["updated_at"] = now
    update_fields["updated_by"] = str(current_user["_id"])

    await db.customers.update_one({"customer_id": doc["customer_id"]}, {"$set": update_fields})
    updated = await db.customers.find_one({"customer_id": doc["customer_id"]})

    await log_activity(
        db=db,
        event_type="customer.updated",
        entity_type="customer",
        entity_id=doc["customer_id"],
        actor_id=str(current_user["_id"]),
        actor_name=current_user.get("full_name", ""),
        actor_role=current_user.get("role", ""),
        details={"updated_fields": list(update_fields.keys())},
    )

    return customer_doc_to_response(updated)


class CustomerNoteCreate(BaseModel):
    notes: str
    scheduled_at: Optional[datetime] = None
    enquiry_id: Optional[str] = None


@router.post("/{customer_id}/followups", summary="Add follow-up note or schedule next call for customer")
async def add_customer_followup(
    customer_id: str,
    payload: CustomerNoteCreate,
    current_user: dict = Depends(get_current_user),
):
    """Log an executive follow-up note directly for a customer."""
    db = get_database()
    doc = await get_customer_doc(db, customer_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Customer '{customer_id}' not found.")

    cid = doc["customer_id"]
    now = datetime.now(timezone.utc)
    user_id_str = str(current_user["_id"])

    # Resolve enquiry_id
    enq_id = payload.enquiry_id
    enq_req = None
    if not enq_id:
        latest_enq = await db.enquiries.find_one({"customer_id": cid}, sort=[("created_at", -1)])
        if latest_enq:
            enq_id = latest_enq["enquiry_id"]
            enq_req = latest_enq.get("looking_for")
        else:
            from backend.app.services.sequence_service import generate_enquiry_id
            enq_id = await generate_enquiry_id(db)
            enq_doc = {
                "enquiry_id": enq_id,
                "customer_id": cid,
                "customer_name": doc.get("name", "Customer"),
                "customer_phone": doc.get("primary_phone", ""),
                "customer_phone_normalized": doc.get("primary_phone_normalized", ""),
                "transaction_type": "rent",
                "looking_for": "General Requirement",
                "status": "new",
                "created_by": user_id_str,
                "created_at": now,
                "updated_at": now,
            }
            await db.enquiries.insert_one(enq_doc)
            enq_req = "General Requirement"

    from backend.app.services.sequence_service import generate_followup_id
    followup_id = await generate_followup_id(db)

    f_doc = {
        "followup_id": followup_id,
        "customer_id": cid,
        "customer_name": doc.get("name", "Customer"),
        "customer_phone": doc.get("primary_phone", ""),
        "enquiry_id": enq_id,
        "enquiry_requirement": enq_req,
        "scheduled_date": payload.scheduled_at or now,
        "scheduled_at": payload.scheduled_at or now,
        "responsible_employee": user_id_str,
        "responsible_name": current_user.get("full_name", "Staff"),
        "notes": payload.notes,
        "status": "scheduled" if (payload.scheduled_at and payload.scheduled_at > now) else "completed",
        "outcome": "Follow-up note logged via mobile",
        "created_by": user_id_str,
        "created_at": now,
        "updated_at": now,
    }
    await db.followups.insert_one(f_doc)

    await log_activity(
        db=db,
        event_type="followup.created",
        entity_type="customer",
        entity_id=cid,
        actor_id=user_id_str,
        actor_name=current_user.get("full_name", ""),
        actor_role=current_user.get("role", "executive"),
        details={"followup_id": followup_id, "notes": payload.notes, "enquiry_id": enq_id},
    )

    f_doc["id"] = str(f_doc["_id"])
    f_doc["_id"] = str(f_doc["_id"])
    return f_doc


@router.delete("/{customer_id}", status_code=status.HTTP_200_OK)
async def delete_customer(
    customer_id: str,
    current_user: dict = Depends(require_roles(["super_admin"])),
):
    """Super Admin only permanent deletion."""
    db = get_database()
    doc = await get_customer_doc(db, customer_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Customer '{customer_id}' not found.")

    await db.customers.delete_one({"customer_id": doc["customer_id"]})
    return {"detail": f"Customer '{doc['customer_id']}' permanently deleted by Super Admin."}
