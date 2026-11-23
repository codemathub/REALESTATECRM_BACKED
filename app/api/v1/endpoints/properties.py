import math
import re
from datetime import datetime, timezone
from typing import Optional
from bson import ObjectId
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pymongo.errors import DuplicateKeyError

from backend.app.core.deps import get_current_user, require_roles
from backend.app.core.database import get_database
from backend.app.schemas.property import (
    PropertyCreate,
    PropertyUpdate,
    PropertyResponse,
    PropertyListResponse,
)
from backend.app.services.property_service import (
    generate_property_id,
    normalize_phone,
    log_activity,
)

router = APIRouter()


async def get_property_doc(db, property_id: str):
    """Fetch property by 8-char property_id or MongoDB _id."""
    query = {"property_id": property_id.upper()}
    if ObjectId.is_valid(property_id):
        query = {"$or": [{"property_id": property_id.upper()}, {"_id": ObjectId(property_id)}]}
    return await db.properties.find_one(query)


def property_doc_to_response(doc: dict) -> PropertyResponse:
    return PropertyResponse(
        id=str(doc["_id"]),
        property_id=doc["property_id"],
        transaction_type=doc.get("transaction_type", "rent"),
        property_type=doc["property_type"],
        bhk=doc.get("bhk"),
        additional_rooms=doc.get("additional_rooms", []),
        society_id=doc["society_id"],
        society_name=doc.get("society_name", ""),
        location=doc.get("location", ""),
        expected_amount=float(doc.get("expected_amount", 0.0)),
        currency=doc.get("currency", "INR"),
        negotiable=bool(doc.get("negotiable", False)),
        security_deposit=float(doc["security_deposit"]) if doc.get("security_deposit") is not None else None,
        maintenance_mode=doc.get("maintenance_mode", "included"),
        maintenance_amount=float(doc["maintenance_amount"]) if doc.get("maintenance_amount") is not None else None,
        additional_charges=doc.get("additional_charges", []),
        owner_name=doc.get("owner_name", ""),
        owner_phone=doc.get("owner_phone", ""),
        owner_phone_normalized=doc.get("owner_phone_normalized", ""),
        agent_name=doc.get("agent_name"),
        agent_phone=doc.get("agent_phone"),
        agent_phone_normalized=doc.get("agent_phone_normalized"),
        tenant_type=doc.get("tenant_type"),
        status=doc.get("status", "available"),
        notes=doc.get("notes"),
        created_by=str(doc.get("created_by", "")),
        created_by_name=doc.get("created_by_name"),
        updated_by=str(doc.get("updated_by", "")) if doc.get("updated_by") else None,
        created_at=doc.get("created_at") or datetime.now(timezone.utc),
        updated_at=doc.get("updated_at") or datetime.now(timezone.utc),
        archived_at=doc.get("archived_at"),
        archived_by=str(doc.get("archived_by")) if doc.get("archived_by") else None,
    )


@router.get("", response_model=PropertyListResponse)
async def list_properties(
    search: Optional[str] = Query(None, description="Search by ID, society, location, owner name or phone"),
    transaction_type: Optional[str] = Query(None, pattern="^(rent|sale)$"),
    property_type: Optional[str] = None,
    society_id: Optional[str] = None,
    location: Optional[str] = None,
    status: Optional[str] = None,
    min_price: Optional[float] = Query(None, ge=0),
    max_price: Optional[float] = Query(None, ge=0),
    tenant_type: Optional[str] = None,
    maintenance_mode: Optional[str] = None,
    include_archived: bool = Query(False, description="Whether to include archived properties"),
    sort_by: str = Query("newest", pattern="^(newest|oldest|price_asc|price_desc|recently_updated)$"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """PRD REQ-PROP-02: Server-side search, filtering, and pagination of company property inventory."""
    db = get_database()
    query: dict = {}

    # Handle status & archive filter
    if status:
        query["status"] = status
    elif not include_archived:
        query["status"] = {"$ne": "archived"}

    # Exact or category filters
    if transaction_type:
        query["transaction_type"] = transaction_type
    if property_type:
        query["property_type"] = property_type
    if society_id:
        query["society_id"] = society_id
    if location:
        query["location"] = {"$regex": re.escape(location), "$options": "i"}
    if tenant_type:
        query["tenant_type"] = tenant_type
    if maintenance_mode:
        query["maintenance_mode"] = maintenance_mode

    # Price range filter
    if min_price is not None or max_price is not None:
        price_filter = {}
        if min_price is not None:
            price_filter["$gte"] = min_price
        if max_price is not None:
            price_filter["$lte"] = max_price
        query["expected_amount"] = price_filter

    # Global search query across fields
    if search and search.strip():
        term = search.strip()
        escaped_term = re.escape(term)
        normalized_num = normalize_phone(term)
        or_clauses = [
            {"property_id": {"$regex": escaped_term, "$options": "i"}},
            {"unit_number": {"$regex": escaped_term, "$options": "i"}},
            {"society_name": {"$regex": escaped_term, "$options": "i"}},
            {"location": {"$regex": escaped_term, "$options": "i"}},
            {"owner_name": {"$regex": escaped_term, "$options": "i"}},
            {"property_type": {"$regex": escaped_term, "$options": "i"}},
        ]
        if normalized_num:
            or_clauses.append({"owner_phone_normalized": {"$regex": normalized_num}})
        query["$or"] = or_clauses

    # Sorting
    sort_spec = [("created_at", -1)]
    if sort_by == "oldest":
        sort_spec = [("created_at", 1)]
    elif sort_by == "price_asc":
        sort_spec = [("expected_amount", 1)]
    elif sort_by == "price_desc":
        sort_spec = [("expected_amount", -1)]
    elif sort_by == "recently_updated":
        sort_spec = [("updated_at", -1)]

    skip = (page - 1) * limit
    total = await db.properties.count_documents(query)
    cursor = db.properties.find(query).sort(sort_spec).skip(skip).limit(limit)
    items_docs = await cursor.to_list(length=limit)

    return PropertyListResponse(
        items=[property_doc_to_response(d) for d in items_docs],
        total=total,
        page=page,
        limit=limit,
        pages=math.ceil(total / limit) if total > 0 else 0,
    )


@router.post("", response_model=PropertyResponse, status_code=status.HTTP_201_CREATED)
async def create_property(
    prop_in: PropertyCreate,
    current_user: dict = Depends(get_current_user),
):
    """
    PRD REQ-PROP-01: Create property with server-generated unique Property ID (5 letters + 3 digits).
    Both Admin and Executive can create properties.
    """
    db = get_database()

    # Look up society to ensure validity and cache society_name
    soc_query = {"society_id": prop_in.society_id}
    if ObjectId.is_valid(prop_in.society_id):
        soc_query = {"$or": [{"society_id": prop_in.society_id}, {"_id": ObjectId(prop_in.society_id)}]}
    society = await db.societies.find_one(soc_query)
    if not society:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Society with ID '{prop_in.society_id}' does not exist.",
        )

    now = datetime.now(timezone.utc)
    max_retries = 10

    for attempt in range(max_retries):
        property_id = generate_property_id()
        doc = prop_in.model_dump()
        doc["property_id"] = property_id
        doc["society_id"] = society["society_id"]
        doc["society_name"] = society["name"]
        doc["owner_phone_normalized"] = normalize_phone(prop_in.owner_phone)
        doc["agent_phone_normalized"] = normalize_phone(prop_in.agent_phone) if prop_in.agent_phone else None
        doc["created_by"] = str(current_user["_id"])
        doc["created_by_name"] = current_user.get("full_name", "")
        doc["created_at"] = now
        doc["updated_at"] = now
        doc["archived_at"] = None
        doc["archived_by"] = None

        try:
            result = await db.properties.insert_one(doc)
            doc["_id"] = result.inserted_id

            # Log audit event
            await log_activity(
                db=db,
                event_type="property.created",
                entity_type="property",
                entity_id=property_id,
                actor_id=str(current_user["_id"]),
                actor_name=current_user.get("full_name", ""),
                actor_role=current_user.get("role", ""),
                details={
                    "property_id": property_id,
                    "transaction_type": prop_in.transaction_type,
                    "property_type": prop_in.property_type,
                    "society_name": society["name"],
                    "expected_amount": prop_in.expected_amount,
                },
            )

            return property_doc_to_response(doc)
        except DuplicateKeyError:
            if attempt == max_retries - 1:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to generate a unique Property ID after several retries.",
                )
            continue


@router.get("/{property_id}", response_model=PropertyResponse)
async def get_property(
    property_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Retrieve full details of a single property record."""
    db = get_database()
    doc = await db.properties.find_one({"property_id": property_id.upper()})
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Property '{property_id}' not found.",
        )
    return property_doc_to_response(doc)


class PropertyNoteCreate(BaseModel):
    notes: str
    category: Optional[str] = "Owner Follow-up"
    scheduled_call_at: Optional[datetime] = None


@router.post("/{property_id}/notes", response_model=PropertyResponse, summary="Log owner follow-up or property remark")
async def add_property_note(
    property_id: str,
    payload: PropertyNoteCreate,
    current_user: dict = Depends(get_current_user),
):
    """Log an owner conversation remark and optionally register a callback in the executive agenda."""
    db = get_database()
    existing = await db.properties.find_one({"property_id": property_id.upper()})
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Property '{property_id}' not found.",
        )

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%d %b %Y, %I:%M %p")
    author_name = current_user.get("full_name", "Staff")
    prefix = f"[{date_str} • {author_name} ({payload.category})]: {payload.notes.strip()}"

    existing_notes = (existing.get("notes") or "").strip()
    new_notes = f"{prefix}\n\n{existing_notes}".strip() if existing_notes else prefix

    await db.properties.update_one(
        {"property_id": property_id.upper()},
        {"$set": {"notes": new_notes, "updated_at": now, "updated_by": str(current_user["_id"])}},
    )

    # If an owner callback was scheduled, register it directly in followups agenda!
    if payload.scheduled_call_at:
        from backend.app.services.sequence_service import generate_followup_id
        fu_id = await generate_followup_id(db)
        fu_doc = {
            "followup_id": fu_id,
            "customer_id": f"OWNER-{property_id.upper()}",
            "customer_name": f"Owner: {existing.get('owner_name', 'Property Owner')}",
            "customer_phone": existing.get("owner_phone", ""),
            "enquiry_id": property_id.upper(),
            "enquiry_requirement": f"Inventory #{property_id.upper()} ({existing.get('society_name', '')})",
            "scheduled_date": payload.scheduled_call_at,
            "scheduled_at": payload.scheduled_call_at,
            "responsible_employee": str(current_user["_id"]),
            "responsible_name": author_name,
            "notes": f"[{payload.category}] {payload.notes.strip()}",
            "status": "scheduled",
            "outcome": "Owner Follow-up registered via mobile",
            "created_by": str(current_user["_id"]),
            "created_at": now,
            "updated_at": now,
        }
        await db.followups.insert_one(fu_doc)

    await log_activity(
        db=db,
        event_type="property.note_added",
        entity_type="property",
        entity_id=property_id.upper(),
        actor_id=str(current_user["_id"]),
        actor_name=author_name,
        actor_role=current_user.get("role", "executive"),
        details={
            "category": payload.category,
            "notes": payload.notes.strip(),
            "scheduled_call_at": payload.scheduled_call_at.isoformat() if payload.scheduled_call_at else None,
        },
    )

    updated = await db.properties.find_one({"property_id": property_id.upper()})
    return property_doc_to_response(updated)


@router.patch("/{property_id}", response_model=PropertyResponse)
async def update_property(
    property_id: str,
    prop_update: PropertyUpdate,
    current_user: dict = Depends(get_current_user),
):
    """PRD REQ-PROP-03: Update existing property record. Property ID is immutable."""
    db = get_database()
    existing = await db.properties.find_one({"property_id": property_id.upper()})
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Property '{property_id}' not found.",
        )

    update_fields = prop_update.model_dump(exclude_unset=True)
    if not update_fields:
        return property_doc_to_response(existing)

    # If society is being changed, verify society existence and update society_name
    if "society_id" in update_fields:
        soc_query = {"society_id": update_fields["society_id"]}
        if ObjectId.is_valid(update_fields["society_id"]):
            soc_query = {"$or": [{"society_id": update_fields["society_id"]}, {"_id": ObjectId(update_fields["society_id"])}]}
        soc = await db.societies.find_one(soc_query)
        if not soc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Society with ID '{update_fields['society_id']}' does not exist.",
            )
        update_fields["society_name"] = soc["name"]
        update_fields["society_id"] = soc["society_id"]

    if "owner_phone" in update_fields:
        update_fields["owner_phone_normalized"] = normalize_phone(update_fields["owner_phone"])
    if "agent_phone" in update_fields:
        update_fields["agent_phone_normalized"] = normalize_phone(update_fields["agent_phone"])

    now = datetime.now(timezone.utc)
    update_fields["updated_at"] = now
    update_fields["updated_by"] = str(current_user["_id"])

    await db.properties.update_one(
        {"property_id": property_id.upper()},
        {"$set": update_fields},
    )

    updated_doc = await db.properties.find_one({"property_id": property_id.upper()})

    await log_activity(
        db=db,
        event_type="property.updated",
        entity_type="property",
        entity_id=property_id.upper(),
        actor_id=str(current_user["_id"]),
        actor_name=current_user.get("full_name", ""),
        actor_role=current_user.get("role", ""),
        details={"updated_fields": list(update_fields.keys())},
    )

    return property_doc_to_response(updated_doc)


@router.post("/{property_id}/archive", response_model=PropertyResponse)
async def archive_property(
    property_id: str,
    current_user: dict = Depends(get_current_user),
):
    """PRD REQ-PROP-04: Archive property record. Retains history and remains searchable under archive filter."""
    db = get_database()
    existing = await db.properties.find_one({"property_id": property_id.upper()})
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Property '{property_id}' not found.",
        )

    now = datetime.now(timezone.utc)
    await db.properties.update_one(
        {"property_id": property_id.upper()},
        {
            "$set": {
                "status": "archived",
                "archived_at": now,
                "archived_by": str(current_user["_id"]),
                "updated_at": now,
                "updated_by": str(current_user["_id"]),
            }
        },
    )

    updated_doc = await db.properties.find_one({"property_id": property_id.upper()})

    await log_activity(
        db=db,
        event_type="property.archived",
        entity_type="property",
        entity_id=property_id.upper(),
        actor_id=str(current_user["_id"]),
        actor_name=current_user.get("full_name", ""),
        actor_role=current_user.get("role", ""),
        details={"previous_status": existing.get("status")},
    )

    return property_doc_to_response(updated_doc)


@router.post("/{property_id}/unarchive", response_model=PropertyResponse)
async def unarchive_property(
    property_id: str,
    current_user: dict = Depends(get_current_user),
):
    """PRD REQ-PROP-05: Restore archived property back to available status."""
    db = get_database()
    existing = await db.properties.find_one({"property_id": property_id.upper()})
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Property '{property_id}' not found.",
        )

    now = datetime.now(timezone.utc)
    await db.properties.update_one(
        {"property_id": property_id.upper()},
        {
            "$set": {
                "status": "available",
                "archived_at": None,
                "archived_by": None,
                "updated_at": now,
                "updated_by": str(current_user["_id"]),
            }
        },
    )

    updated_doc = await db.properties.find_one({"property_id": property_id.upper()})

    await log_activity(
        db=db,
        event_type="property.unarchived",
        entity_type="property",
        entity_id=property_id.upper(),
        actor_id=str(current_user["_id"]),
        actor_name=current_user.get("full_name", ""),
        actor_role=current_user.get("role", ""),
        details={"restored_status": "available"},
    )

    return property_doc_to_response(updated_doc)


@router.delete("/{property_id}", status_code=status.HTTP_200_OK)
async def delete_property(
    property_id: str,
    current_user: dict = Depends(require_roles(["super_admin"])),
):
    """
    PRD REQ-PROP-06 & Section 3.1:
    Only Super Admin can permanently delete records.
    Admin and Executive roles are strictly rejected with 403 Forbidden.
    """
    db = get_database()
    existing = await db.properties.find_one({"property_id": property_id.upper()})
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Property '{property_id}' not found.",
        )

    await db.properties.delete_one({"property_id": property_id.upper()})

    await log_activity(
        db=db,
        event_type="property.deleted",
        entity_type="property",
        entity_id=property_id.upper(),
        actor_id=str(current_user["_id"]),
        actor_name=current_user.get("full_name", ""),
        actor_role=current_user.get("role", ""),
        details={"deleted_property_id": property_id.upper()},
    )

    return {"detail": f"Property '{property_id.upper()}' permanently deleted by Super Admin."}
