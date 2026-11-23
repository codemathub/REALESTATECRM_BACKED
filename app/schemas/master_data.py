from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime


class SocietyCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=150, description="Display name of the residential society")


class SocietyResponse(BaseModel):
    id: str
    society_id: str
    name: str
    is_active: bool
    properties_count: int = 0
    created_at: datetime


class MasterDataOptionCreate(BaseModel):
    category: str = Field(..., description="property_type, additional_room, location, tenant_type, lead_source, customer_type, maintenance_mode")
    value: str = Field(..., min_length=1, max_length=100)
    sort_order: Optional[int] = 0
    metadata: Optional[Dict[str, Any]] = None


class MasterDataOptionResponse(BaseModel):
    id: str
    category: str
    value: str
    is_active: bool
    sort_order: int = 0
    metadata: Optional[Dict[str, Any]] = None
    created_at: datetime
