from datetime import datetime, timezone
import pytest
from httpx import AsyncClient
from backend.app.core.database import get_database
from backend.app.core.security import get_password_hash


async def get_auth_token(client: AsyncClient, email: str, role: str) -> str:
    db = get_database()
    pwd = "Admin@123456" if email.lower() == "code.mathub@gmail.com" else "TestPass@123"
    # Ensure user exists
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
        json={"email": email, "password": pwd, "device_id": f"device-{role}"},
    )
    if res.status_code != 200:
        # Update password in case of existing test user with different password
        await db.users.update_one(
            {"email_normalized": email.lower()},
            {"$set": {"password_hash": get_password_hash(pwd), "account_status": "active"}}
        )
        res = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": pwd, "device_id": f"device-{role}"},
        )
    return res.json()["access_token"]


@pytest.mark.asyncio
async def test_baseline_workbook_societies_seeded(async_client: AsyncClient):
    """PRD REQ-MSTR-01: Baseline workbook societies are seeded and searchable."""
    token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    headers = {"Authorization": f"Bearer {token}"}

    res = await async_client.get("/api/v1/societies", headers=headers)
    assert res.status_code == 200
    societies = res.json()
    assert len(societies) >= 10
    names = [s["name"] for s in societies]
    assert "Ajnara Homes" in names
    assert "RG Luxury Homes" in names
    assert "Centurial Park Terrace Homes" in names


@pytest.mark.asyncio
async def test_baseline_master_data_categories_seeded(async_client: AsyncClient):
    """PRD REQ-MSTR-03: Master data categories are seeded."""
    token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    headers = {"Authorization": f"Bearer {token}"}

    # Query property types
    res_pt = await async_client.get("/api/v1/master-data?category=property_type", headers=headers)
    assert res_pt.status_code == 200
    pts = [item["value"] for item in res_pt.json()]
    assert "2 BHK" in pts
    assert "2 BHK + 2T" in pts
    assert "3 BHK" in pts

    # Query locations
    res_loc = await async_client.get("/api/v1/master-data?category=location", headers=headers)
    assert res_loc.status_code == 200
    locs = [item["value"] for item in res_loc.json()]
    assert "Techzone 4" in locs
    assert "Noida Extension" in locs


@pytest.mark.asyncio
async def test_admin_creates_society_with_unique_id(async_client: AsyncClient):
    """PRD Section 5.6: Admin can create a society with auto-generated unique ID."""
    token = await get_auth_token(async_client, "admin.test@estateflow.com", "admin")
    headers = {"Authorization": f"Bearer {token}"}

    # Clean up test society if exists
    db = get_database()
    await db.societies.delete_many({"name_normalized": "ace city plaza"})

    res = await async_client.post(
        "/api/v1/societies",
        headers=headers,
        json={"name": "ACE City Plaza"},
    )
    assert res.status_code == 201
    data = res.json()
    assert data["name"] == "ACE City Plaza"
    assert data["society_id"].startswith("SOC")
    assert data["is_active"] is True


@pytest.mark.asyncio
async def test_society_duplicate_name_rejected(async_client: AsyncClient):
    """PRD Section 8.4: Normalized society name must be unique."""
    token = await get_auth_token(async_client, "admin.test@estateflow.com", "admin")
    headers = {"Authorization": f"Bearer {token}"}

    # Attempt to create already existing society
    res = await async_client.post(
        "/api/v1/societies",
        headers=headers,
        json={"name": "ajnara homes"},
    )
    assert res.status_code == 409
    assert "already exists" in res.json()["detail"]


@pytest.mark.asyncio
async def test_executive_cannot_create_society(async_client: AsyncClient):
    """PRD Section 3.1: Executive cannot add or manage societies."""
    token = await get_auth_token(async_client, "exec.test@estateflow.com", "executive")
    headers = {"Authorization": f"Bearer {token}"}

    res = await async_client.post(
        "/api/v1/societies",
        headers=headers,
        json={"name": "Unauthorized Society"},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_executive_cannot_create_master_data(async_client: AsyncClient):
    """PRD Section 3.1: Executive cannot add or manage master data options."""
    token = await get_auth_token(async_client, "exec.test@estateflow.com", "executive")
    headers = {"Authorization": f"Bearer {token}"}

    res = await async_client.post(
        "/api/v1/master-data",
        headers=headers,
        json={"category": "property_type", "value": "10 BHK Penthouse"},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_society_toggle_active_status(async_client: AsyncClient):
    """PRD Section 5.6: Deactivate an option already in use rather than deleting it."""
    token = await get_auth_token(async_client, "admin.test@estateflow.com", "admin")
    headers = {"Authorization": f"Bearer {token}"}

    res = await async_client.patch("/api/v1/societies/SOC0001/toggle-status", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["society_id"] == "SOC0001"
    assert "is_active" in data

    # Toggle back to active
    res_back = await async_client.patch("/api/v1/societies/SOC0001/toggle-status", headers=headers)
    assert res_back.status_code == 200
    assert res_back.json()["is_active"] is True
