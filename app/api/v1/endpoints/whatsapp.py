from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from backend.app.core.deps import get_current_user
from backend.app.core.database import get_database
from backend.app.schemas.whatsapp import (
    WhatsAppLogShareRequest,
    WhatsAppStatusResponse,
    WhatsAppSendDirectRequest,
)

router = APIRouter()


@router.post("/log-share")
async def log_whatsapp_share(
    payload: WhatsAppLogShareRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    PRD 5.20 & REQ-INT-03: Mode A - Log manual WhatsApp share event.
    Stores that a share action was initiated from the mobile/web client.
    """
    db = get_database()
    now = datetime.now(timezone.utc)
    user_id = str(current_user["_id"])
    role = current_user.get("role", "executive")

    await db.activity_events.insert_one({
        "event_type": "whatsapp.share_initiated",
        "entity_type": "property" if payload.property_id else "enquiry",
        "entity_id": payload.property_id or payload.enquiry_id or "general",
        "actor_id": user_id,
        "actor_role": role,
        "details": {
            "channel": payload.share_channel,
            "property_id": payload.property_id,
            "enquiry_id": payload.enquiry_id,
            "customer_phone": payload.customer_phone,
            "message_excerpt": payload.message_excerpt[:100] if payload.message_excerpt else None,
        },
        "created_at": now,
    })

    return {"status": "logged", "timestamp": now.isoformat()}


@router.get("/status", response_model=WhatsAppStatusResponse)
async def get_whatsapp_status(
    current_user: dict = Depends(get_current_user),
):
    """
    PRD 5.20 & REQ-INT-04: Report status of Mode A (manual) and Mode B (direct API).
    Mode B remains disabled until real Meta WABA credentials and webhook are supplied.
    """
    return WhatsAppStatusResponse(
        is_configured=False,
        mode_a_manual_available=True,
        mode_b_direct_api_available=False,
        phone_number_id=None,
        business_account_id=None,
        webhook_verified=False,
        message="Mode A (Manual WhatsApp deep link sharing) is fully operational. Mode B (Direct Cloud API) is disabled until official Meta WABA credentials are provided in System Settings.",
    )


@router.post("/send-direct")
async def send_direct_whatsapp(
    payload: WhatsAppSendDirectRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    PRD 5.20: Direct WhatsApp Business API delivery.
    Gated until real WABA credentials are provided.
    """
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Direct WhatsApp Business Platform API is currently disabled. Please configure official Meta WABA credentials and approved templates in System Settings.",
    )
