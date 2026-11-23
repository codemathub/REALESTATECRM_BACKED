from typing import Optional
from pydantic import BaseModel, Field


class WhatsAppLogShareRequest(BaseModel):
    property_id: Optional[str] = None
    enquiry_id: Optional[str] = None
    customer_phone: Optional[str] = None
    share_channel: str = Field("whatsapp_native", description="whatsapp_native or whatsapp_web")
    message_excerpt: Optional[str] = None


class WhatsAppStatusResponse(BaseModel):
    is_configured: bool
    mode_a_manual_available: bool = True
    mode_b_direct_api_available: bool = False
    phone_number_id: Optional[str] = None
    business_account_id: Optional[str] = None
    webhook_verified: bool = False
    message: str


class WhatsAppSendDirectRequest(BaseModel):
    recipient_phone: str
    template_name: str
    property_id: Optional[str] = None
    enquiry_id: Optional[str] = None
