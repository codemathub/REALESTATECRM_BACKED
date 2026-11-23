from datetime import datetime
from typing import List, Literal, Optional
from pydantic import BaseModel, Field, field_validator
from backend.app.services.property_service import validate_real_mobile


class AdditionalCharge(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    amount: float = Field(..., ge=0)
    frequency: Literal["one_time", "monthly"] = "monthly"


class PropertyBase(BaseModel):
    transaction_type: Literal["rent", "sale"] = "rent"
    property_type: str = Field(..., min_length=1, description="e.g. 2 BHK, 3 BHK, Villa")
    bhk: Optional[str] = None
    additional_rooms: List[str] = Field(default_factory=list)
    society_id: str = Field(..., min_length=1, description="Unique Society ID e.g. SOC0001")
    location: str = Field(..., min_length=1)
    
    # Pricing
    expected_amount: float = Field(..., ge=0, description="Numeric amount in INR")
    currency: str = "INR"
    negotiable: bool = False
    security_deposit: Optional[float] = Field(None, ge=0)
    maintenance_mode: Literal["included", "extra"] = "included"
    maintenance_amount: Optional[float] = Field(None, ge=0)
    additional_charges: List[AdditionalCharge] = Field(default_factory=list)
    
    # Contacts
    owner_name: str = Field(..., min_length=2, max_length=120)
    owner_phone: str = Field(..., min_length=10, max_length=20)
    agent_name: Optional[str] = None
    agent_phone: Optional[str] = None
    tenant_type: Optional[str] = None
    
    # Status & Notes
    status: Literal["available", "rented", "sold", "reserved", "under_negotiation", "archived"] = "available"
    notes: Optional[str] = None

    @field_validator("owner_name")
    @classmethod
    def validate_owner_name(cls, v: str) -> str:
        clean = v.strip()
        if len(clean) < 2:
            raise ValueError("Property owner name is compulsory and must be at least 2 characters.")
        return clean

    @field_validator("owner_phone")
    @classmethod
    def validate_owner_phone(cls, v: str) -> str:
        return validate_real_mobile(v, field_name="Property owner mobile number")

    @field_validator("agent_phone")
    @classmethod
    def validate_agent_phone(cls, v: Optional[str]) -> Optional[str]:
        if not v or not str(v).strip():
            return None
        return validate_real_mobile(v, field_name="Agent mobile number")


class PropertyCreate(PropertyBase):
    pass


class PropertyUpdate(BaseModel):
    transaction_type: Optional[Literal["rent", "sale"]] = None
    property_type: Optional[str] = None
    bhk: Optional[str] = None
    additional_rooms: Optional[List[str]] = None
    society_id: Optional[str] = None
    location: Optional[str] = None
    expected_amount: Optional[float] = Field(None, ge=0)
    negotiable: Optional[bool] = None
    security_deposit: Optional[float] = Field(None, ge=0)
    maintenance_mode: Optional[Literal["included", "extra"]] = None
    maintenance_amount: Optional[float] = Field(None, ge=0)
    additional_charges: Optional[List[AdditionalCharge]] = None
    owner_name: Optional[str] = None
    owner_phone: Optional[str] = None
    agent_name: Optional[str] = None
    agent_phone: Optional[str] = None
    tenant_type: Optional[str] = None
    status: Optional[Literal["available", "rented", "sold", "reserved", "under_negotiation", "archived"]] = None
    notes: Optional[str] = None

    @field_validator("owner_name")
    @classmethod
    def validate_opt_owner_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        clean = v.strip()
        if len(clean) < 2:
            raise ValueError("Property owner name must be at least 2 characters.")
        return clean

    @field_validator("owner_phone")
    @classmethod
    def validate_opt_owner_phone(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return validate_real_mobile(v, field_name="Property owner mobile number")

    @field_validator("agent_phone")
    @classmethod
    def validate_opt_agent_phone(cls, v: Optional[str]) -> Optional[str]:
        if not v or not str(v).strip():
            return None
        return validate_real_mobile(v, field_name="Agent mobile number")


class PropertyResponse(PropertyBase):
    id: str
    property_id: str = Field(..., description="5 uppercase letters + 3 digits, e.g. ABCDE123")
    society_name: str
    owner_phone_normalized: str
    agent_phone_normalized: Optional[str] = None
    created_by: str
    created_by_name: Optional[str] = None
    updated_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    archived_at: Optional[datetime] = None
    archived_by: Optional[str] = None


class PropertyListResponse(BaseModel):
    items: List[PropertyResponse]
    total: int
    page: int
    limit: int
    pages: int
