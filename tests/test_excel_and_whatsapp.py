import io
from datetime import datetime, timezone
import pytest
import openpyxl
from httpx import AsyncClient

from backend.app.core.database import get_database
from backend.app.core.security import get_password_hash


async def get_auth_token(client: AsyncClient, email: str, role: str) -> str:
    db = get_database()
    pwd = "TestPass@123"
    user = await db.users.find_one({"email_normalized": email.lower()})
    if not user:
        now = datetime.now(timezone.utc)
        await db.users.insert_one({
            "full_name": f"Test {role.capitalize()}",
            "email": email,
            "email_normalized": email.lower(),
            "role": role,
            "account_status": "active",
            "password_hash": get_password_hash(pwd),
            "created_at": now,
            "updated_at": now,
        })

    res = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": pwd, "device_id": f"device-{role}-excel"},
    )
    if res.status_code != 200:
        await db.users.update_one(
            {"email_normalized": email.lower()},
            {"$set": {"password_hash": get_password_hash(pwd), "account_status": "active"}},
        )
        res = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": pwd, "device_id": f"device-{role}-excel"},
        )
    return res.json()["access_token"]


@pytest.mark.asyncio
async def test_excel_export_allowed_for_admin_and_super_admin(async_client: AsyncClient):
    """PRD 5.19 & REQ-INT-02: Super Admin and Admin can export permitted company data."""
    admin_token = await get_auth_token(async_client, "excel_admin@estateflow.com", "admin")
    headers = {"Authorization": f"Bearer {admin_token}"}

    # 1. Export Properties
    res_prop = await async_client.get("/api/v1/excel/export/properties", headers=headers)
    assert res_prop.status_code == 200
    assert res_prop.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert len(res_prop.content) > 1000  # Valid Excel binary

    # 2. Export Enquiries
    res_enq = await async_client.get("/api/v1/excel/export/enquiries", headers=headers)
    assert res_enq.status_code == 200
    assert len(res_enq.content) > 1000

    # 3. Export Deals
    res_deal = await async_client.get("/api/v1/excel/export/deals", headers=headers)
    assert res_deal.status_code == 200
    assert len(res_deal.content) > 1000


@pytest.mark.asyncio
async def test_executive_excel_export_strictly_blocked(async_client: AsyncClient):
    """PRD 5.19 & REQ-INT-02: Executive cannot export via Excel, CSV, or download."""
    exec_token = await get_auth_token(async_client, "excel_exec@estateflow.com", "executive")
    headers = {"Authorization": f"Bearer {exec_token}"}

    res_prop = await async_client.get("/api/v1/excel/export/properties", headers=headers)
    assert res_prop.status_code == 403
    assert "Operation not permitted" in res_prop.json()["detail"] or "Forbidden" in res_prop.json()["detail"]

    res_enq = await async_client.get("/api/v1/excel/export/enquiries", headers=headers)
    assert res_enq.status_code == 403

    res_deal = await async_client.get("/api/v1/excel/export/deals", headers=headers)
    assert res_deal.status_code == 403


@pytest.mark.asyncio
async def test_excel_import_preview_and_validation(async_client: AsyncClient):
    """PRD 5.19 & REQ-INT-01: Preview rows, validate, show duplicate warnings and legacy status mapping."""
    admin_token = await get_auth_token(async_client, "excel_admin@estateflow.com", "admin")
    headers = {"Authorization": f"Bearer {admin_token}"}

    # Create dummy in-memory workbook with 2 properties (1 valid with legacy status, 1 missing society)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Properties"
    ws.append(["Property ID", "Society Name", "Location", "BHK", "Expected Price", "Status", "Owner Name", "Owner Phone"])
    ws.append(["PROP9991", "Royal Palms", "Golf Course Ext", "3 BHK", 65000, "On Hold", "Vipul Sharma", "9811234901"])
    ws.append(["", "", "Golf Course Ext", "2 BHK", 35000, "Available", "Unknown Owner", "9811234902"])  # Missing society

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)

    files = {"file": ("test_import.xlsx", out.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    res = await async_client.post("/api/v1/excel/import/preview", headers=headers, files=files)
    assert res.status_code == 200
    data = res.json()

    assert data["sheet_type"] == "properties"
    assert data["total_rows"] == 2
    assert data["valid_count"] == 1
    assert data["error_count"] == 1
    assert "on hold" in data["status_mapping_applied"]
    assert data["status_mapping_applied"]["on hold"] == "under_negotiation"


@pytest.mark.asyncio
async def test_excel_import_commit(async_client: AsyncClient):
    """PRD 5.19 & REQ-INT-01: Confirm & commit validated rows into MongoDB."""
    admin_token = await get_auth_token(async_client, "excel_admin@estateflow.com", "admin")
    headers = {"Authorization": f"Bearer {admin_token}"}

    payload = {
        "sheet_type": "properties",
        "file_name": "test_import.xlsx",
        "rows": [
            {
                "society_name": "Apex Athena",
                "location": "Sector 75",
                "bhk": "3 BHK",
                "expected_amount": 45000,
                "status": "available",
                "owner_name": "Rohan Verma",
                "owner_phone": "9876543210",
            }
        ],
    }

    res = await async_client.post("/api/v1/excel/import/confirm", headers=headers, json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["imported_count"] == 1

    # Verify property exists in DB
    db = get_database()
    prop = await db.properties.find_one({"society_name": "Apex Athena", "owner_name": "Rohan Verma"})
    assert prop is not None
    assert prop["status"] == "available"


@pytest.mark.asyncio
async def test_whatsapp_manual_share_logging(async_client: AsyncClient):
    """PRD 5.20 & REQ-INT-03: Mode A - Manual share logs an activity event."""
    exec_token = await get_auth_token(async_client, "excel_exec@estateflow.com", "executive")
    headers = {"Authorization": f"Bearer {exec_token}"}

    payload = {
        "property_id": "PROP0001",
        "share_channel": "whatsapp_native",
        "customer_phone": "9811234901",
        "message_excerpt": "3 BHK at Royal Palms available for rent",
    }
    res = await async_client.post("/api/v1/whatsapp/log-share", headers=headers, json=payload)
    assert res.status_code == 200
    assert res.json()["status"] == "logged"

    # Verify event logged in activity_events
    db = get_database()
    event = await db.activity_events.find_one({"event_type": "whatsapp.share_initiated", "entity_id": "PROP0001"})
    assert event is not None


@pytest.mark.asyncio
async def test_whatsapp_direct_api_gated(async_client: AsyncClient):
    """PRD 5.20 & REQ-INT-04: Mode B - Direct API returns disabled status without WABA credentials."""
    exec_token = await get_auth_token(async_client, "excel_exec@estateflow.com", "executive")
    headers = {"Authorization": f"Bearer {exec_token}"}

    status_res = await async_client.get("/api/v1/whatsapp/status", headers=headers)
    assert status_res.status_code == 200
    assert status_res.json()["mode_b_direct_api_available"] is False

    send_res = await async_client.post(
        "/api/v1/whatsapp/send-direct",
        headers=headers,
        json={"recipient_phone": "9811234901", "template_name": "property_proposal"},
    )
    assert send_res.status_code == 503
