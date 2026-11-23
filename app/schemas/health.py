from pydantic import BaseModel, Field
from datetime import datetime


class HealthCheckResponse(BaseModel):
    status: str = Field(..., json_schema_extra={"example": "ok"})
    environment: str = Field(..., json_schema_extra={"example": "development"})
    database: str = Field(..., json_schema_extra={"example": "connected"})
    timestamp: datetime
    version: str = Field(default="1.0.0")
