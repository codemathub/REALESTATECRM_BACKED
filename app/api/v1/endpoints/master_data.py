import logging
from datetime import datetime, timezone
from typing import List, Optional
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status, Request
from backend.app.core.database import get_database
from backend.app.core.deps import get_current_user, require_roles, log_audit_event
from backend.app.schemas.master_data import MasterDataOptionCreate, MasterDataOptionResponse

logger = logging.getLogger("estateflow.master_data.routes")
router = APIRouter(prefix="/master-data", tags=["Master Data"])

VALID_CATEGORIES = [
    "property_type",
    "additional_room",
    "location",
    "tenant_type",
    "lead_source",
    "customer_type",
    "maintenance_mode",
]


@router.get("", response_model=List[MasterDataOptionResponse], summary="List configured dropdown options by category")
async def list_master_data(
    category: Optional[str] = None,
    active_only: bool = True,
    current_user: dict = Depends(get_current_user),
):
    db = get_database()
    query = {}
    if category:
        query["category"] = category
    if active_only:
        query["is_active"] = True

    cursor = db.master_data_options.find(query).sort([("category", 1), ("sort_order", 1), ("value", 1)])
    options = await cursor.to_list(length=500)

    return [
        MasterDataOptionResponse(
            id=str(o["_id"]),
            category=o["category"],
            value=o["value"],
            is_active=o["is_active"],
            sort_order=o.get("sort_order", 0),
            metadata=o.get("metadata", {}),
            created_at=o.get("created_at") or datetime.now(timezone.utc),
        )
        for o in options
    ]


@router.post("", response_model=MasterDataOptionResponse, status_code=status.HTTP_201_CREATED, summary="Add dropdown option (Admin/Super Admin only)")
async def create_master_data_option(
    req: MasterDataOptionCreate,
    request: Request,
    current_user: dict = Depends(require_roles(["super_admin", "admin"])),
):
    db = get_database()
    cat = req.category.strip().lower()
    if cat not in VALID_CATEGORIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid category '{req.category}'. Allowed categories: {VALID_CATEGORIES}",
        )

    norm_val = req.value.strip().lower()

    # Compound duplicate check
    existing = await db.master_data_options.find_one({"category": cat, "value_normalized": norm_val})
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Option '{req.value}' already exists under category '{cat}'.",
        )

    now = datetime.now(timezone.utc)
    doc = {
        "category": cat,
        "value": req.value.strip(),
        "value_normalized": norm_val,
        "sort_order": req.sort_order or 0,
        "is_active": True,
        "metadata": req.metadata or {},
        "created_by": current_user["_id"],
        "created_at": now,
        "updated_at": now,
    }

    res = await db.master_data_options.insert_one(doc)

    await log_audit_event(
        actor_id=str(current_user["_id"]),
        actor_role=current_user["role"],
        event_type="master_data.created",
        entity_type="master_data",
        entity_id=str(res.inserted_id),
        diff={"category": cat, "value": req.value.strip()},
        request=request,
    )

    return MasterDataOptionResponse(
        id=str(res.inserted_id),
        category=cat,
        value=req.value.strip(),
        is_active=True,
        sort_order=req.sort_order or 0,
        metadata=req.metadata or {},
        created_at=now,
    )


@router.patch("/{option_id}/toggle-status", summary="Deactivate or reactivate dropdown option (Admin/Super Admin only)")
async def toggle_master_data_status(
    option_id: str,
    request: Request,
    current_user: dict = Depends(require_roles(["super_admin", "admin"])),
):
    """PRD Section 5.6: Deactivate an option already in use rather than deleting it."""
    db = get_database()
    try:
        obj_id = ObjectId(option_id)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid option ID format")

    option = await db.master_data_options.find_one({"_id": obj_id})
    if not option:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Option not found")

    new_status = not option.get("is_active", True)
    now = datetime.now(timezone.utc)

    await db.master_data_options.update_one(
        {"_id": obj_id},
        {"$set": {"is_active": new_status, "updated_at": now}}
    )

    await log_audit_event(
        actor_id=str(current_user["_id"]),
        actor_role=current_user["role"],
        event_type="master_data.status_toggled",
        entity_type="master_data",
        entity_id=option_id,
        diff={"category": option["category"], "value": option["value"], "is_active": new_status},
        request=request,
    )

    return {
        "id": option_id,
        "category": option["category"],
        "value": option["value"],
        "is_active": new_status,
        "message": f"Option '{option['value']}' under '{option['category']}' is now {'active' if new_status else 'deactivated'}.",
    }
