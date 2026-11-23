from datetime import datetime
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class DealCloseRequest(BaseModel):
    property_id: str = Field(..., description="Property ID closed for this enquiry (e.g. ABCDE123)")
    deal_date: Optional[datetime] = None
    responsible_executive_id: Optional[str] = None
    deal_amount: Optional[float] = Field(None, ge=0)
    final_remarks: Optional[str] = None
    visit_id: Optional[str] = Field(None, description="Linked visit ID if closed from visit")


class EnquiryAbortRequest(BaseModel):
    reason: str = Field(..., min_length=2, description="Mandatory abort reason")
    final_remarks: Optional[str] = None
    visit_id: Optional[str] = Field(None, description="Linked visit ID if aborted from visit")


class DealResponse(BaseModel):
    id: str
    deal_id: str = Field(..., description="e.g. DEA00001")
    enquiry_id: str
    customer_id: str
    customer_name: str
    customer_phone: Optional[str] = None
    property_id: str
    property_society: Optional[str] = None
    property_location: Optional[str] = None
    property_bhk: Optional[str] = None
    transaction_type: Literal["rent", "sale"]
    deal_amount: float
    deal_date: datetime
    responsible_executive_id: str
    responsible_executive_name: str
    final_remarks: Optional[str] = None
    created_at: datetime


class DealListResponse(BaseModel):
    items: List[DealResponse]
    total: int
    page: int
    limit: int
    pages: int
