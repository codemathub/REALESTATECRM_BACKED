from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class ArchiveStatsResponse(BaseModel):
    closed_deals_count: int
    aborted_enquiries_count: int
    rented_properties_count: int
    sold_properties_count: int
    archived_properties_count: int
    total_archived_properties: int
    total_archived_enquiries: int


class PermanentDeleteRequest(BaseModel):
    reason: str = Field(..., min_length=3, description="High-friction mandatory audit reason for permanent deletion")


class RestoreRecordResponse(BaseModel):
    message: str
    entity_type: str
    entity_id: str
    restored_status: str
