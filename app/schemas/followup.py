from datetime import datetime
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class FollowUpCreate(BaseModel):
    enquiry_id: str = Field(..., description="Target Enquiry ID (e.g. ENQ00001)")
    scheduled_at: datetime = Field(..., description="Scheduled follow-up date and time")
    responsible_employee: Optional[str] = Field(None, description="Employee user ID responsible (defaults to current user)")
    notes: str = Field(..., min_length=2, max_length=1000, description="Follow-up agenda or call objective")


class FollowUpComplete(BaseModel):
    outcome: str = Field(..., min_length=2, max_length=200, description="Outcome of follow-up (e.g. Client requested callback / budget adjustment)")
    remarks: Optional[str] = Field(None, max_length=1000, description="Detailed call remarks")
    next_followup_at: Optional[datetime] = Field(None, description="Optional next scheduled follow-up datetime")
    next_followup_notes: Optional[str] = Field(None, max_length=1000, description="Notes for the next follow-up if scheduling")


class FollowUpReschedule(BaseModel):
    new_scheduled_at: datetime = Field(..., description="Updated scheduled date and time")
    reason: str = Field(..., min_length=3, max_length=500, description="Reason for rescheduling")


class FollowUpCancel(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500, description="Reason for cancellation")


class FollowUpResponse(BaseModel):
    id: str
    followup_id: str
    customer_id: str
    customer_name: str
    customer_phone: str
    enquiry_id: str
    enquiry_requirement: Optional[str] = None
    scheduled_at: datetime
    responsible_employee: str
    responsible_name: Optional[str] = None
    notes: str
    status: Literal["scheduled", "due", "completed", "missed", "cancelled"]
    outcome: Optional[str] = None
    remarks: Optional[str] = None
    completed_at: Optional[datetime] = None
    reschedule_reason: Optional[str] = None
    cancellation_reason: Optional[str] = None
    created_by: str
    created_at: datetime
    updated_at: datetime


class FollowUpsListResponse(BaseModel):
    items: List[FollowUpResponse]
    total: int
    overdue_count: int
    today_count: int
    upcoming_count: int


class CalendarAgendaItem(BaseModel):
    date: str
    total_count: int
    followups: List[FollowUpResponse]
