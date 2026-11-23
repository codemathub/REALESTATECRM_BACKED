from datetime import datetime
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


EnquiryStatus = Literal[
    "new",
    "contacted",
    "follow_up",
    "visit_scheduled",
    "negotiation",
    "on_hold",
    "not_connected",
    "not_interested",
    "deal_closed",
    "aborted",
]


class EnquiryBase(BaseModel):
    customer_id: str = Field(..., description="Customer ID or Reference (e.g. CUS00001)")
    transaction_type: Literal["rent", "sale"] = "rent"
    looking_for: str = Field(..., min_length=1, description="e.g. 2 BHK, 3 BHK")
    preferred_locations: List[str] = Field(default_factory=list)
    preferred_societies: List[str] = Field(default_factory=list)
    budget_min: float = Field(..., ge=0)
    budget_max: float = Field(..., ge=0)
    maintenance_preference: Optional[str] = "included"
    tenant_type: Optional[str] = "Family"
    priority: Literal["hot", "warm", "cold"] = "warm"
    lead_source: Optional[str] = None
    status: EnquiryStatus = "new"
    responsible_employee_id: Optional[str] = None
    notes: Optional[str] = None


class EnquiryCreate(EnquiryBase):
    pass


class EnquiryUpdate(BaseModel):
    transaction_type: Optional[Literal["rent", "sale"]] = None
    looking_for: Optional[str] = None
    preferred_locations: Optional[List[str]] = None
    preferred_societies: Optional[List[str]] = None
    budget_min: Optional[float] = Field(None, ge=0)
    budget_max: Optional[float] = Field(None, ge=0)
    maintenance_preference: Optional[str] = None
    tenant_type: Optional[str] = None
    priority: Optional[Literal["hot", "warm", "cold"]] = None
    status: Optional[EnquiryStatus] = None
    abort_reason: Optional[str] = None
    responsible_employee_id: Optional[str] = None
    notes: Optional[str] = None


class EnquiryStatusChange(BaseModel):
    status: EnquiryStatus
    reason: Optional[str] = None
    notes: Optional[str] = None


class EnquiryResponse(EnquiryBase):
    id: str
    enquiry_id: str = Field(..., description="e.g. ENQ00001")
    customer_name: str
    customer_phone: str
    customer_phone_normalized: str
    customer_type: str
    abort_reason: Optional[str] = None
    responsible_employee_name: Optional[str] = None
    last_followup_at: Optional[datetime] = None
    next_followup_at: Optional[datetime] = None
    created_by: str
    created_by_name: Optional[str] = None
    updated_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    archived_at: Optional[datetime] = None


class EnquiryListResponse(BaseModel):
    items: List[EnquiryResponse]
    total: int
    page: int
    limit: int
    pages: int
