from typing import List, Literal, Optional
from pydantic import BaseModel, Field
from backend.app.schemas.property import PropertyResponse
from backend.app.schemas.customer import CustomerResponse
from backend.app.schemas.enquiry import EnquiryResponse


class SearchResultItem(BaseModel):
    id: str
    entity_type: Literal["property", "customer", "enquiry"]
    title: str
    subtitle: str
    badge: str
    data: dict


class GlobalSearchResponse(BaseModel):
    query: str
    total: int
    properties: List[PropertyResponse]
    customers: List[CustomerResponse]
    enquiries: List[EnquiryResponse]
    results: List[SearchResultItem]


class PropertyMatchItem(BaseModel):
    property: PropertyResponse
    match_score: int = Field(..., ge=0, le=100)
    match_reasons: List[str]


class EnquiryMatchesResponse(BaseModel):
    enquiry_id: str
    customer_name: str
    looking_for: str
    transaction_type: str
    budget_range: str
    match_count: int
    matches: List[PropertyMatchItem]
