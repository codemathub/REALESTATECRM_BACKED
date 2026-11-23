from datetime import datetime
from typing import List, Literal, Optional
from pydantic import BaseModel, Field, field_validator
from backend.app.services.property_service import validate_real_mobile


class CustomerBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    primary_phone: str = Field(..., min_length=10, max_length=20)
    alternate_phone: Optional[str] = None
    whatsapp_phone: Optional[str] = None
    customer_type: Literal["tenant", "buyer", "agent", "owner"] = "tenant"
    lead_source: Optional[str] = "Walk-in"
    priority: Literal["hot", "warm", "cold"] = "warm"
    notes: Optional[str] = None
    shared_phone_reason: Optional[str] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        clean = v.strip()
        if len(clean) < 2:
            raise ValueError("Customer name is compulsory and must be at least 2 characters.")
        return clean

    @field_validator("primary_phone")
    @classmethod
    def validate_primary_phone(cls, v: str) -> str:
        return validate_real_mobile(v, field_name="Customer mobile number")

    @field_validator("alternate_phone", "whatsapp_phone")
    @classmethod
    def validate_other_phones(cls, v: Optional[str]) -> Optional[str]:
        if not v or not str(v).strip():
            return None
        return validate_real_mobile(v, field_name="Secondary phone number")


class CustomerCreate(CustomerBase):
    pass


class CustomerUpdate(BaseModel):
    name: Optional[str] = None
    primary_phone: Optional[str] = None
    alternate_phone: Optional[str] = None
    whatsapp_phone: Optional[str] = None
    customer_type: Optional[Literal["tenant", "buyer", "agent", "owner"]] = None
    lead_source: Optional[str] = None
    priority: Optional[Literal["hot", "warm", "cold"]] = None
    notes: Optional[str] = None

    @field_validator("primary_phone")
    @classmethod
    def validate_optional_primary_phone(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return validate_real_mobile(v, field_name="Customer mobile number")


class CustomerMatchItem(BaseModel):
    id: str
    customer_id: str
    name: str
    primary_phone: str
    primary_phone_normalized: str
    customer_type: str
    enquiries_count: int
    created_at: datetime


class DuplicatePhoneCheckResponse(BaseModel):
    has_duplicate: bool
    matches: List[CustomerMatchItem]


class CustomerResponse(CustomerBase):
    id: str
    customer_id: str
    primary_phone_normalized: str
    alternate_phone_normalized: Optional[str] = None
    whatsapp_phone_normalized: Optional[str] = None
    enquiries_count: int = 0
    created_by: str
    created_by_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime
