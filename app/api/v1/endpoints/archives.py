import math
import re
from datetime import datetime, timezone
from typing import Optional
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.app.core.deps import get_current_user, require_roles
from backend.app.core.database import get_database
from backend.app.schemas.archive import (
    ArchiveStatsResponse,
    PermanentDeleteRequest,
    RestoreRecordResponse,
)
from backend.app.schemas.property import PropertyListResponse, PropertyResponse
from backend.app.schemas.enquiry import EnquiryListResponse, EnquiryResponse
from backend.app.schemas.deal import DealListResponse, DealResponse
from backend.app.api.v1.endpoints.properties import property_doc_to_response
from backend.app.api.v1.endpoints.enquiries import enquiry_doc_to_response
from backend.app.services.property_service import log_activity

router = APIRouter()


@router.get("/stats", response_model=ArchiveStatsResponse)
async def get_archive_stats(current_user: dict = Depends(get_current_user)):
    """PRD Section 5.15 & 5.16: Returns aggregated counts of archived and historical records."""
    db = get_database()

    closed_deals = await db.deals.count_documents({})
    aborted_enquiries = await db.enquiries.count_documents({"status": "aborted"})
    rented_props = await db.properties.count_documents({"status": "rented"})
    sold_props = await db.properties.count_documents({"status": "sold"})
    archived_props = await db.properties.count_documents({"status": "archived"})

    total_arch_props = await db.properties.count_documents({"status": {"$in": ["rented", "sold", "archived"]}})
    total_arch_enqs = await db.enquiries.count_documents({"status": {"$in": ["deal_closed", "aborted"]}})

    return ArchiveStatsResponse(
        closed_deals_count=closed_deals,
        aborted_enquiries_count=aborted_enquiries,
        rented_properties_count=rented_props,
        sold_properties_count=sold_props,
        archived_properties_count=archived_props,
        total_archived_properties=total_arch_props,
        total_archived_enquiries=total_arch_enqs,
    )


@router.get("/properties", response_model=PropertyListResponse)
async def list_archived_properties(
    search: Optional[str] = None,
    status_filter: Optional[str] = Query(None, description="rented, sold, archived, or omit for all"),
    transaction_type: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """PRD Section 5.16: Query archived, rented, and sold properties."""
    db = get_database()

    if status_filter and status_filter.lower() != "all":
        query: dict = {"status": status_filter.lower()}
    else:
        query = {"status": {"$in": ["rented", "sold", "archived"]}}

    if transaction_type and transaction_type.lower() != "all":
        query["transaction_type"] = transaction_type.lower()

    if search and search.strip():
        term = search.strip()
        escaped = re.escape(term)
        query["$or"] = [
            {"property_id": {"$regex": f"^{escaped}", "$options": "i"}},
            {"society_name": {"$regex": escaped, "$options": "i"}},
            {"location": {"$regex": escaped, "$options": "i"}},
            {"owner_name": {"$regex": escaped, "$options": "i"}},
            {"property_type": {"$regex": escaped, "$options": "i"}},
        ]

    skip = (page - 1) * limit
    total = await db.properties.count_documents(query)
    cursor = db.properties.find(query).sort("updated_at", -1).skip(skip).limit(limit)
    docs = await cursor.to_list(length=limit)

    return PropertyListResponse(
        items=[property_doc_to_response(d) for d in docs],
        total=total,
        page=page,
        limit=limit,
        pages=math.ceil(total / limit) if total > 0 else 0,
    )


@router.get("/enquiries", response_model=EnquiryListResponse)
async def list_archived_enquiries(
    search: Optional[str] = None,
    status_filter: Optional[str] = Query(None, description="deal_closed, aborted, or omit for all"),
    transaction_type: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """PRD Section 5.16: Query deal-closed and aborted enquiries."""
    db = get_database()

    if status_filter and status_filter.lower() != "all":
        query: dict = {"status": status_filter.lower()}
    else:
        query = {"status": {"$in": ["deal_closed", "aborted"]}}

    if transaction_type and transaction_type.lower() != "all":
        query["transaction_type"] = transaction_type.lower()

    if search and search.strip():
        term = search.strip()
        escaped = re.escape(term)
        query["$or"] = [
            {"enquiry_id": {"$regex": f"^{escaped}", "$options": "i"}},
            {"customer_name": {"$regex": escaped, "$options": "i"}},
            {"looking_for": {"$regex": escaped, "$options": "i"}},
            {"abort_reason": {"$regex": escaped, "$options": "i"}},
            {"final_remarks": {"$regex": escaped, "$options": "i"}},
        ]

    skip = (page - 1) * limit
    total = await db.enquiries.count_documents(query)
    cursor = db.enquiries.find(query).sort("updated_at", -1).skip(skip).limit(limit)
    docs = await cursor.to_list(length=limit)

    return EnquiryListResponse(
        items=[enquiry_doc_to_response(d) for d in docs],
        total=total,
        page=page,
        limit=limit,
        pages=math.ceil(total / limit) if total > 0 else 0,
    )


@router.get("/deals", response_model=DealListResponse)
async def list_closed_deals(
    search: Optional[str] = None,
    transaction_type: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """PRD Section 5.15: Query closed deals history."""
    db = get_database()
    query: dict = {}

    if transaction_type and transaction_type.lower() != "all":
        query["transaction_type"] = transaction_type.lower()

    if search and search.strip():
        term = search.strip()
        escaped = re.escape(term)
        query["$or"] = [
            {"deal_id": {"$regex": f"^{escaped}", "$options": "i"}},
            {"customer_name": {"$regex": escaped, "$options": "i"}},
            {"property_id": {"$regex": f"^{escaped}", "$options": "i"}},
            {"property_society": {"$regex": escaped, "$options": "i"}},
            {"responsible_executive_name": {"$regex": escaped, "$options": "i"}},
        ]

    skip = (page - 1) * limit
    total = await db.deals.count_documents(query)
    cursor = db.deals.find(query).sort("deal_date", -1).skip(skip).limit(limit)
    docs = await cursor.to_list(length=limit)

    items = [
        DealResponse(
            id=str(d["_id"]),
            deal_id=d["deal_id"],
            enquiry_id=d["enquiry_id"],
            customer_id=d["customer_id"],
            customer_name=d["customer_name"],
            customer_phone=d.get("customer_phone"),
            property_id=d["property_id"],
            property_society=d.get("property_society"),
            property_location=d.get("property_location"),
            property_bhk=d.get("property_bhk"),
            transaction_type=d["transaction_type"],
            deal_amount=d.get("deal_amount", 0.0),
            deal_date=d["deal_date"],
            responsible_executive_id=d["responsible_executive_id"],
            responsible_executive_name=d["responsible_executive_name"],
            final_remarks=d.get("final_remarks"),
            created_at=d["created_at"],
        )
        for d in docs
    ]

    return DealListResponse(
        items=items,
        total=total,
        page=page,
        limit=limit,
        pages=math.ceil(total / limit) if total > 0 else 0,
    )


@router.post("/properties/{property_id}/restore", response_model=RestoreRecordResponse)
async def restore_archived_property(
    property_id: str,
    current_user: dict = Depends(require_roles(["super_admin", "admin"])),
):
    """PRD Section 5.16: Restore archived/rented/sold property back to available status."""
    db = get_database()
    now = datetime.now(timezone.utc)

    prop = await db.properties.find_one({"property_id": property_id.upper()})
    if not prop:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Property '{property_id}' not found.")

    old_status = prop.get("status")
    await db.properties.update_one(
        {"property_id": prop["property_id"]},
        {
            "$set": {
                "status": "available",
                "archived_at": None,
                "archived_by": None,
                "restored_at": now,
                "restored_by": str(current_user["_id"]),
                "updated_at": now,
                "updated_by": str(current_user["_id"]),
            }
        },
    )

    await log_activity(
        db=db,
        event_type="property.restored_from_archive",
        entity_type="property",
        entity_id=prop["property_id"],
        actor_id=str(current_user["_id"]),
        actor_name=current_user.get("full_name", ""),
        actor_role=current_user.get("role", ""),
        details={"previous_status": old_status, "new_status": "available"},
    )

    return RestoreRecordResponse(
        message=f"Property '{prop['property_id']}' restored to active available inventory.",
        entity_type="property",
        entity_id=prop["property_id"],
        restored_status="available",
    )


@router.post("/enquiries/{enquiry_id}/restore", response_model=RestoreRecordResponse)
async def restore_archived_enquiry(
    enquiry_id: str,
    current_user: dict = Depends(require_roles(["super_admin", "admin"])),
):
    """PRD Section 5.16: Restore aborted or closed enquiry back to active status."""
    db = get_database()
    now = datetime.now(timezone.utc)

    enquiry = await db.enquiries.find_one({"enquiry_id": enquiry_id.upper()})
    if not enquiry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Enquiry '{enquiry_id}' not found.")

    old_status = enquiry.get("status")
    await db.enquiries.update_one(
        {"enquiry_id": enquiry["enquiry_id"]},
        {
            "$set": {
                "status": "contacted",
                "abort_reason": None,
                "archived_at": None,
                "restored_at": now,
                "restored_by": str(current_user["_id"]),
                "updated_at": now,
                "updated_by": str(current_user["_id"]),
            }
        },
    )

    await log_activity(
        db=db,
        event_type="enquiry.restored_from_archive",
        entity_type="enquiry",
        entity_id=enquiry["enquiry_id"],
        actor_id=str(current_user["_id"]),
        actor_name=current_user.get("full_name", ""),
        actor_role=current_user.get("role", ""),
        details={"previous_status": old_status, "new_status": "contacted"},
    )

    return RestoreRecordResponse(
        message=f"Enquiry '{enquiry['enquiry_id']}' restored to active pipeline.",
        entity_type="enquiry",
        entity_id=enquiry["enquiry_id"],
        restored_status="contacted",
    )


@router.delete("/properties/{property_id}/permanent")
async def permanently_delete_property(
    property_id: str,
    payload: PermanentDeleteRequest,
    current_user: dict = Depends(require_roles(["super_admin"])),
):
    """
    PRD Section 5.16: Exceptional permanent deletion.
    - Super Admin ONLY.
    - Requires high-friction confirmation and mandatory audit reason.
    - Permanent deletion from database while preserving immutable activity log.
    """
    db = get_database()
    prop = await db.properties.find_one({"property_id": property_id.upper()})
    if not prop:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Property '{property_id}' not found.")

    await db.properties.delete_one({"property_id": prop["property_id"]})

    await log_activity(
        db=db,
        event_type="property.permanent_deletion",
        entity_type="property",
        entity_id=prop["property_id"],
        actor_id=str(current_user["_id"]),
        actor_name=current_user.get("full_name", ""),
        actor_role=current_user.get("role", ""),
        details={
            "reason": payload.reason,
            "deleted_property_id": prop["property_id"],
            "society_name": prop.get("society_name"),
            "owner_name": prop.get("owner_name"),
        },
    )

    return {"detail": f"Property '{prop['property_id']}' permanently deleted by Super Admin with reason: '{payload.reason}'."}


@router.delete("/enquiries/{enquiry_id}/permanent")
async def permanently_delete_enquiry(
    enquiry_id: str,
    payload: PermanentDeleteRequest,
    current_user: dict = Depends(require_roles(["super_admin"])),
):
    """
    PRD Section 5.16: Exceptional permanent deletion of an enquiry.
    - Super Admin ONLY.
    - Requires high-friction confirmation and mandatory audit reason.
    - Permanent deletion from database while preserving immutable activity log.
    """
    db = get_database()
    enquiry = await db.enquiries.find_one({"enquiry_id": enquiry_id.upper()})
    if not enquiry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Enquiry '{enquiry_id}' not found.")

    await db.enquiries.delete_one({"enquiry_id": enquiry["enquiry_id"]})

    await log_activity(
        db=db,
        event_type="enquiry.permanent_deletion",
        entity_type="enquiry",
        entity_id=enquiry["enquiry_id"],
        actor_id=str(current_user["_id"]),
        actor_name=current_user.get("full_name", ""),
        actor_role=current_user.get("role", ""),
        details={
            "reason": payload.reason,
            "deleted_enquiry_id": enquiry["enquiry_id"],
            "customer_id": enquiry.get("customer_id"),
            "customer_name": enquiry.get("customer_name"),
        },
    )

    return {"detail": f"Enquiry '{enquiry['enquiry_id']}' permanently deleted by Super Admin with reason: '{payload.reason}'."}
