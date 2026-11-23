from datetime import datetime
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class NotificationResponse(BaseModel):
    id: str
    user_id: str
    title: str
    body: str
    notification_type: str = "system"
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    is_read: bool = False
    created_at: datetime


class NotificationsListResponse(BaseModel):
    items: List[NotificationResponse]
    total: int
    unread_count: int


class PushTokenRequest(BaseModel):
    token: Optional[str] = None
    push_token: Optional[str] = None
    device_id: Optional[str] = "default-device"
    platform: str = Field(default="android", description="android or ios")

    def get_token(self) -> str:
        return self.push_token or self.token or ""
