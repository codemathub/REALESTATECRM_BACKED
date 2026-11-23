import logging
import re
from datetime import datetime, timezone
from typing import List, Optional
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status, Request
from backend.app.core.database import get_database
from backend.app.core.deps import get_current_user, require_roles, log_audit_event
from backend.app.schemas.master_data import SocietyCreate, SocietyResponse

logger = logging.getLogger("estateflow.societies.routes")
router = APIRouter(prefix="/societies", tags=["Societies"])


async def _generate_next_society_id(db) -> str:
    """Generates the next sequential unique Society ID (e.g. SOC0001, SOC0002)."""
    cursor = db.societies.find({"society_id": {"$regex": r"^SOC\d{4}$"}}).sort("society_id", -1).limit(1)
    last = await cursor.to_list(length=1)
    if last and "society_id" in last[0]:
        num_str = last[0]["society_id"][3:]
        next_num = int(num_str) + 1
    else:
        next_num = 1
    return f"SOC{next_num:04d}"


@router.get("", response_model=List[SocietyResponse], summary="List & search societies")
async def list_societies(
    q: Optional[str] = None,
    active_only: bool = True,
    current_user: dict = Depends(get_current_user),
):
    db = get_database()
    query = {}
    if active_only:
        query["is_active"] = True

    if q and q.strip():
        search_regex = re.compile(re.escape(q.strip()), re.IGNORECASE)
        query["$or"] = [
            {"name": {"$regex": search_regex}},
            {"society_id": {"$regex": search_regex}},
        ]

    cursor = db.societies.find(query).sort("name", 1)
    societies = await cursor.to_list(length=300)

    # Aggregate property counts per society
    results = []
    for s in societies:
        prop_count = await db.properties.count_documents({"society_id": s["society_id"]})
        results.append(
            SocietyResponse(
                id=str(s["_id"]),
                society_id=s["society_id"],
                name=s["name"],
                is_active=s["is_active"],
                properties_count=prop_count,
                created_at=s.get("created_at") or datetime.now(timezone.utc),
            )
        )
    return results


@router.post("", response_model=SocietyResponse, status_code=status.HTTP_201_CREATED, summary="Add society (Admin/Super Admin only)")
async def create_society(
    req: SocietyCreate,
    request: Request,
    current_user: dict = Depends(require_roles(["super_admin", "admin"])),
):
    db = get_database()
    norm_name = req.name.strip().lower()

    # Duplicate check on normalized name
    existing = await db.societies.find_one({"name_normalized": norm_name})
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Society with name '{req.name}' already exists ({existing['society_id']}).",
        )

    soc_id = await _generate_next_society_id(db)
    now = datetime.now(timezone.utc)

    soc_doc = {
        "society_id": soc_id,
        "name": req.name.strip(),
        "name_normalized": norm_name,
        "is_active": True,
        "created_by": current_user["_id"],
        "created_at": now,
        "updated_at": now,
    }

    res = await db.societies.insert_one(soc_doc)

    await log_audit_event(
        actor_id=str(current_user["_id"]),
        actor_role=current_user["role"],
        event_type="society.created",
        entity_type="society",
        entity_id=soc_id,
        diff={"name": req.name.strip(), "society_id": soc_id},
        request=request,
    )

    return SocietyResponse(
        id=str(res.inserted_id),
        society_id=soc_id,
        name=req.name.strip(),
        is_active=True,
        properties_count=0,
        created_at=now,
    )


@router.get("/{society_ref}", response_model=SocietyResponse, summary="Get society detail by society_id or ObjectId")
async def get_society(
    society_ref: str,
    current_user: dict = Depends(get_current_user),
):
    db = get_database()
    query = {"$or": [{"society_id": society_ref.upper()}]}
    try:
        query["$or"].append({"_id": ObjectId(society_ref)})
    except Exception:
        pass

    society = await db.societies.find_one(query)
    if not society:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Society not found")

    prop_count = await db.properties.count_documents({"society_id": society["society_id"]})
    return SocietyResponse(
        id=str(society["_id"]),
        society_id=society["society_id"],
        name=society["name"],
        is_active=society["is_active"],
        properties_count=prop_count,
        created_at=society.get("created_at") or datetime.now(timezone.utc),
    )


@router.patch("/{society_ref}/toggle-status", summary="Deactivate or activate society (Admin/Super Admin only)")
async def toggle_society_status(
    society_ref: str,
    request: Request,
    current_user: dict = Depends(require_roles(["super_admin", "admin"])),
):
    """PRD Section 5.6: Deactivate an option already in use rather than deleting it."""
    db = get_database()
    query = {"$or": [{"society_id": society_ref.upper()}]}
    try:
        query["$or"].append({"_id": ObjectId(society_ref)})
    except Exception:
        pass

    society = await db.societies.find_one(query)
    if not society:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Society not found")

    new_status = not society.get("is_active", True)
    now = datetime.now(timezone.utc)

    await db.societies.update_one(
        {"_id": society["_id"]},
        {"$set": {"is_active": new_status, "updated_at": now}}
    )

    await log_audit_event(
        actor_id=str(current_user["_id"]),
        actor_role=current_user["role"],
        event_type="society.status_toggled",
        entity_type="society",
        entity_id=society["society_id"],
        diff={"previous_active": society.get("is_active"), "new_active": new_status},
        request=request,
    )

    return {
        "society_id": society["society_id"],
        "name": society["name"],
        "is_active": new_status,
        "message": f"Society '{society['name']}' is now {'active' if new_status else 'deactivated'}.",
    }
