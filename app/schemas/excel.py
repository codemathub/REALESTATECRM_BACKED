from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class ImportErrorItem(BaseModel):
    row_number: int
    field: str
    error: str
    raw_value: Optional[str] = None


class ImportPreviewRow(BaseModel):
    row_number: int
    is_valid: bool
    data: Dict[str, Any]
    errors: List[str] = []
    warnings: List[str] = []


class ImportPreviewResponse(BaseModel):
    sheet_type: str = Field(..., description="properties or enquiries")
    file_name: str
    total_rows: int
    valid_count: int
    error_count: int
    warning_count: int
    status_mapping_applied: Dict[str, str] = {}
    preview_rows: List[ImportPreviewRow]
    errors: List[ImportErrorItem] = []


class ImportCommitRequest(BaseModel):
    sheet_type: str
    file_name: str
    rows: List[Dict[str, Any]]


class ImportCommitResponse(BaseModel):
    sheet_type: str
    file_name: str
    imported_count: int
    skipped_count: int
    message: str
