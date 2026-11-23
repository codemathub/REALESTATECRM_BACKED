from datetime import datetime
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class TaskCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=200, description="Task headline or assignment title")
    task_type: str = Field(default="lead_assignment", description="Operational task category")
    assigned_to: str = Field(..., description="Target employee user ID or email")
    priority: Literal["low", "medium", "high", "urgent"] = Field(default="medium")
    target_client_name: Optional[str] = Field(None, description="Client name if lead assignment")
    target_client_phone: Optional[str] = Field(None, description="Client phone number")
    monthly_budget: Optional[str] = Field(None, description="Monthly budget or ticket size")
    asset_preference: Optional[str] = Field(None, description="Asset specification e.g. Commercial Studio Sector 62")
    property_id: Optional[str] = Field(None, description="Associated Property ID ABCDE123")
    enquiry_id: Optional[str] = Field(None, description="Associated Enquiry ID ENQ00001")
    lockbox_tag: Optional[str] = Field(None, description="Lockbox tag e.g. #LB-04B")
    sla_minutes: int = Field(default=30, ge=5, le=1440, description="Standard response SLA in minutes")
    due_at: Optional[datetime] = Field(None, description="Task deadline")
    instructions: Optional[str] = Field(None, max_length=1500, description="Detailed instructions from Admin")


class TaskAcknowledge(BaseModel):
    acknowledgement_note: Optional[str] = Field(None, max_length=500)


class TaskComplete(BaseModel):
    completion_remarks: Optional[str] = Field(None, max_length=1000)
    remarks: Optional[str] = Field(None, max_length=1000)

    def get_remarks(self) -> Optional[str]:
        return self.completion_remarks or self.remarks


class TaskCancel(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500, description="Mandatory cancellation reason")


class TaskResponse(BaseModel):
    id: str
    task_id: str
    title: str
    task_type: str
    assigned_to: str
    assigned_name: Optional[str] = None
    assigned_by: str
    assigned_by_name: Optional[str] = None
    priority: str
    status: Literal["pending", "acknowledged", "in_progress", "completed", "cancelled"]
    target_client_name: Optional[str] = None
    target_client_phone: Optional[str] = None
    monthly_budget: Optional[str] = None
    asset_preference: Optional[str] = None
    property_id: Optional[str] = None
    enquiry_id: Optional[str] = None
    lockbox_tag: Optional[str] = None
    sla_minutes: int
    due_at: Optional[datetime] = None
    instructions: Optional[str] = None
    acknowledged_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    completion_remarks: Optional[str] = None
    cancellation_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class TasksListResponse(BaseModel):
    items: List[TaskResponse]
    total: int
    pending_ack_count: int
    in_progress_count: int
    completed_count: int


class AnnouncementCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=200, description="Branch notice headline")
    body: str = Field(..., min_length=5, max_length=2000, description="Full broadcast message")
    audience: Literal["all", "executives", "admins"] = Field(default="all")
    priority: Literal["normal", "urgent"] = Field(default="normal")
    expires_at: Optional[datetime] = Field(None, description="Notice expiry timestamp")


class AnnouncementResponse(BaseModel):
    id: str
    title: str
    body: str
    audience: str
    priority: str
    created_by: str
    created_by_name: Optional[str] = None
    created_at: datetime
    expires_at: Optional[datetime] = None
