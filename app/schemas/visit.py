from datetime import datetime
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class VisitCreate(BaseModel):
    enquiry_id: str = Field(..., description="Target Enquiry ID (e.g. ENQ00001)")
    property_id: str = Field(..., description="Target 8-char Property ID (e.g. ABCDE123)")
    scheduled_at: datetime = Field(..., description="Scheduled visit date and time")
    assigned_to: Optional[str] = Field(None, description="Directly assigned Executive User ID (optional)")
    notes: Optional[str] = Field(None, max_length=1000, description="Visit prep or client notes")


class VisitComplete(BaseModel):
    outcome: Optional[str] = Field(None, max_length=200, description="Optional visit outcome (e.g. Liked property, Wants negotiation)")
    remarks: Optional[str] = Field(None, max_length=1000, description="Detailed completion remarks")


class VisitReschedule(BaseModel):
    new_scheduled_at: datetime = Field(..., description="Updated scheduled date and time")
    reason: str = Field(..., min_length=3, max_length=500, description="Mandatory reason for rescheduling")


class VisitCancel(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500, description="Mandatory reason for cancellation")


class VisitReassign(BaseModel):
    new_assigned_to: str = Field(..., description="Executive user ID to reassign visit to")


class VisitResponse(BaseModel):
    id: str
    visit_id: str
    customer_id: str
    customer_name: str
    customer_phone: str
    enquiry_id: str
    property_id: str
    property_society: str
    property_bhk: Optional[str] = None
    property_location: str
    property_amount: float
    property_transaction_type: str
    scheduled_at: datetime
    assigned_to: Optional[str] = None
    assigned_name: Optional[str] = None
    claim_status: Literal["direct_assigned", "unclaimed", "claimed"]
    claimed_by: Optional[str] = None
    claimed_by_name: Optional[str] = None
    claimed_at: Optional[datetime] = None
    status: Literal["scheduled", "in_progress", "completed", "rescheduled", "cancelled"]
    outcome: Optional[str] = None
    remarks: Optional[str] = None
    reschedule_reason: Optional[str] = None
    cancellation_reason: Optional[str] = None
    created_by: str
    created_at: datetime
    updated_at: datetime


class VisitsListResponse(BaseModel):
    items: List[VisitResponse]
    total: int
    open_claims_count: int
    today_visits_count: int
