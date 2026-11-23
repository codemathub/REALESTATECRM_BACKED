from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class ReportKPIsResponse(BaseModel):
    # Properties
    total_properties: int
    available_properties: int
    rented_properties: int
    sold_properties: int
    archived_properties: int

    # Enquiries
    total_enquiries: int
    new_enquiries_this_week: int
    active_enquiries: int
    closed_deals: int
    aborted_deals: int

    # Visits
    today_scheduled_visits: int
    completed_visits: int
    open_unclaimed_visits: int

    # Follow-ups
    today_followups: int
    upcoming_followups: int
    overdue_followups: int

    # Financial / Revenue Volume
    total_deal_volume: float
    rent_deal_volume: float
    sale_deal_volume: float


class ExecutivePerformanceItem(BaseModel):
    id: str
    user_id: str
    name: str
    email: str
    role: str
    assigned_enquiries: int
    completed_visits: int
    overdue_followups: int
    pending_tasks: int
    closed_deals: int
    total_deal_volume: float
    conversion_rate: float


class ExecutivePerformanceResponse(BaseModel):
    items: List[ExecutivePerformanceItem]
    total_executives: int


class DrilldownItem(BaseModel):
    id: str
    entity_type: str
    title: str
    subtitle: str
    badge: str
    date: str
    data: dict


class DrilldownResponse(BaseModel):
    metric: str
    title: str
    total: int
    items: List[DrilldownItem]
