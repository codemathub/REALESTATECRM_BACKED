from datetime import datetime, timezone
import io
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from fastapi.responses import StreamingResponse

from backend.app.core.deps import get_current_user, require_roles
from backend.app.core.database import get_database
from backend.app.schemas.excel import (
    ImportPreviewResponse,
    ImportCommitRequest,
    ImportCommitResponse,
)
from backend.app.services.excel_service import (
    export_properties_to_excel,
    export_enquiries_to_excel,
    export_deals_to_excel,
    parse_and_preview_excel,
    commit_excel_import,
)

router = APIRouter()


@router.get("/export/properties")
async def export_properties(
    current_user: dict = Depends(require_roles(["super_admin", "admin"])),
):
    """
    PRD 5.19 & REQ-INT-02: Export all operational properties as an Excel (.xlsx) file.
    Strictly blocked for Executive roles (403 Forbidden).
    """
    db = get_database()
    file_bytes = await export_properties_to_excel(db)

    # Log export audit event
    await db.activity_events.insert_one({
        "event_type": "export.properties",
        "entity_type": "property",
        "actor_id": str(current_user["_id"]),
        "actor_role": current_user.get("role", "admin"),
        "details": {"format": "xlsx", "timestamp": datetime.now(timezone.utc).isoformat()},
        "created_at": datetime.now(timezone.utc),
    })

    filename = f"estateflow_properties_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.xlsx"
    return StreamingResponse(
        io.BytesIO(file_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/export/enquiries")
async def export_enquiries(
    current_user: dict = Depends(require_roles(["super_admin", "admin"])),
):
    """
    PRD 5.19 & REQ-INT-02: Export active customer enquiries as an Excel (.xlsx) file.
    Strictly blocked for Executive roles (403 Forbidden).
    """
    db = get_database()
    file_bytes = await export_enquiries_to_excel(db)

    # Log export audit event
    await db.activity_events.insert_one({
        "event_type": "export.enquiries",
        "entity_type": "enquiry",
        "actor_id": str(current_user["_id"]),
        "actor_role": current_user.get("role", "admin"),
        "details": {"format": "xlsx", "timestamp": datetime.now(timezone.utc).isoformat()},
        "created_at": datetime.now(timezone.utc),
    })

    filename = f"estateflow_enquiries_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.xlsx"
    return StreamingResponse(
        io.BytesIO(file_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/export/deals")
async def export_deals(
    current_user: dict = Depends(require_roles(["super_admin", "admin"])),
):
    """
    PRD 5.19 & REQ-INT-02: Export deal closings & financial settlements to Excel.
    Strictly blocked for Executive roles (403 Forbidden).
    """
    db = get_database()
    file_bytes = await export_deals_to_excel(db)

    # Log export audit event
    await db.activity_events.insert_one({
        "event_type": "export.deals",
        "entity_type": "deal",
        "actor_id": str(current_user["_id"]),
        "actor_role": current_user.get("role", "admin"),
        "details": {"format": "xlsx", "timestamp": datetime.now(timezone.utc).isoformat()},
        "created_at": datetime.now(timezone.utc),
    })

    filename = f"estateflow_deals_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.xlsx"
    return StreamingResponse(
        io.BytesIO(file_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.post("/import/preview", response_model=ImportPreviewResponse)
async def import_preview(
    file: UploadFile = File(...),
    current_user: dict = Depends(require_roles(["super_admin", "admin"])),
):
    """
    PRD 5.19 & REQ-INT-01: Dry-run preview and row-level validation for legacy workbook upload.
    Maps legacy status values and checks existing IDs.
    """
    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .xlsx or .xls Excel workbooks are supported.",
        )

    file_bytes = await file.read()
    db = get_database()

    try:
        preview = await parse_and_preview_excel(file_bytes, file.filename, db)
        return preview
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/import/confirm", response_model=ImportCommitResponse)
async def import_confirm(
    payload: ImportCommitRequest,
    current_user: dict = Depends(require_roles(["super_admin", "admin"])),
):
    """
    PRD 5.19 & REQ-INT-01: Final commit of validated rows into MongoDB collections.
    """
    db = get_database()
    user_id = str(current_user["_id"])
    user_role = current_user.get("role", "admin")

    result = await commit_excel_import(
        sheet_type=payload.sheet_type,
        file_name=payload.file_name,
        rows=payload.rows,
        user_id=user_id,
        user_role=user_role,
        db=db,
    )
    return result
