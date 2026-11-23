import asyncio
import re
from datetime import datetime, timezone
import pytest
from httpx import AsyncClient
from backend.app.core.database import get_database
from backend.app.core.security import get_password_hash


async def get_auth_token(client: AsyncClient, email: str, role: str) -> str:
    db = get_database()
    pwd = "Admin@123456" if email.lower() == "code.mathub@gmail.com" else "TestPass@123"
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
        json={"email": email, "password": pwd, "device_id": f"device-{role}-prop"},
    )
    if res.status_code != 200:
        await db.users.update_one(
            {"email_normalized": email.lower()},
            {"$set": {"password_hash": get_password_hash(pwd), "account_status": "active"}}
        )
        res = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": pwd, "device_id": f"device-{role}-prop"},
        )
    return res.json()["access_token"]


@pytest.mark.asyncio
async def test_property_id_generation_concurrency_and_format(async_client: AsyncClient):
    """
    PRD REQ-PROP-01 & Exit Criteria:
    - Exactly 5 uppercase letters + 3 digits (e.g. ABCDE123).
    - Unique under concurrent create requests.
    """
    token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    headers = {"Authorization": f"Bearer {token}"}

    # Fetch a valid society ID
    soc_res = await async_client.get("/api/v1/societies", headers=headers)
    assert soc_res.status_code == 200
    society_id = soc_res.json()[0]["society_id"]

    id_pattern = re.compile(r"^[A-Z]{5}[0-9]{3}$")

    async def create_single_prop(idx: int):
        return await async_client.post(
            "/api/v1/properties",
            headers=headers,
            json={
                "transaction_type": "rent",
                "property_type": "2 BHK",
                "society_id": society_id,
                "location": "Sector 4 Greater Noida",
                "expected_amount": 20000 + idx * 500,
                "owner_name": f"Concurrent Owner {idx}",
                "owner_phone": f"+91 99990 {idx:05d}",
            },
        )

    # Launch 15 concurrent property creations
    responses = await asyncio.gather(*[create_single_prop(i) for i in range(15)])

    generated_ids = []
    for r in responses:
        assert r.status_code == 201
        data = r.json()
        prop_id = data["property_id"]
        assert id_pattern.match(prop_id), f"ID '{prop_id}' does not match 5-letters+3-digits format"
        generated_ids.append(prop_id)

    # Confirm all generated IDs are strictly unique
    assert len(generated_ids) == len(set(generated_ids)), "Collision detected among generated property IDs"


@pytest.mark.asyncio
async def test_property_create_full_schema_and_pricing(async_client: AsyncClient):
    """PRD REQ-PROP-02: Property full numeric pricing, additional charges, and phone normalization."""
    token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    headers = {"Authorization": f"Bearer {token}"}

    soc_res = await async_client.get("/api/v1/societies", headers=headers)
    society_id = soc_res.json()[0]["society_id"]

    payload = {
        "transaction_type": "rent",
        "property_type": "3 BHK + 3T",
        "bhk": "3 BHK",
        "additional_rooms": ["Study", "Terrace"],
        "society_id": society_id,
        "location": "Noida Extension",
        "expected_amount": 24000.0,
        "currency": "INR",
        "negotiable": True,
        "security_deposit": 48000.0,
        "maintenance_mode": "extra",
        "maintenance_amount": 2500.0,
        "additional_charges": [
            {"name": "Club Membership", "amount": 500.0, "frequency": "monthly"},
            {"name": "Move-in Fee", "amount": 1000.0, "frequency": "one_time"},
        ],
        "owner_name": "Vikram Malhotra",
        "owner_phone": "+91 (987) 654-3210",
        "agent_name": "Sunil Broker",
        "agent_phone": "9812345678",
        "tenant_type": "Family",
        "notes": "Spacious park-facing unit on the 12th floor.",
    }

    res = await async_client.post("/api/v1/properties", headers=headers, json=payload)
    assert res.status_code == 201
    prop = res.json()

    assert prop["expected_amount"] == 24000.0
    assert prop["negotiable"] is True
    assert prop["security_deposit"] == 48000.0
    assert prop["maintenance_mode"] == "extra"
    assert prop["maintenance_amount"] == 2500.0
    assert len(prop["additional_charges"]) == 2
    assert prop["owner_phone_normalized"] == "919876543210"
    assert prop["agent_phone_normalized"] == "9812345678"
    assert "photos" not in prop
    assert "documents" not in prop


@pytest.mark.asyncio
async def test_executive_can_create_and_update_property(async_client: AsyncClient):
    """PRD Section 3.2: Both Admin and Executive can view, create, and edit company properties."""
    exec_token = await get_auth_token(async_client, "exec.prop@test.com", "executive")
    headers = {"Authorization": f"Bearer {exec_token}"}

    soc_res = await async_client.get("/api/v1/societies", headers=headers)
    society_id = soc_res.json()[0]["society_id"]

    # Executive creates property
    create_res = await async_client.post(
        "/api/v1/properties",
        headers=headers,
        json={
            "transaction_type": "sale",
            "property_type": "Villa",
            "society_id": society_id,
            "location": "Greater noida west",
            "expected_amount": 8500000.0,
            "owner_name": "Meera Patel",
            "owner_phone": "9871122334",
        },
    )
    assert create_res.status_code == 201
    prop_id = create_res.json()["property_id"]

    # Executive updates price and status
    update_res = await async_client.patch(
        f"/api/v1/properties/{prop_id}",
        headers=headers,
        json={"expected_amount": 8200000.0, "status": "reserved"},
    )
    assert update_res.status_code == 200
    updated = update_res.json()
    assert updated["expected_amount"] == 8200000.0
    assert updated["status"] == "reserved"


@pytest.mark.asyncio
async def test_server_side_search_and_filters(async_client: AsyncClient):
    """PRD REQ-PROP-02: Server-side search by ID, society, location, price range."""
    token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    headers = {"Authorization": f"Bearer {token}"}

    soc_res = await async_client.get("/api/v1/societies", headers=headers)
    society_id = soc_res.json()[0]["society_id"]

    # Create distinct target property
    prop_res = await async_client.post(
        "/api/v1/properties",
        headers=headers,
        json={
            "transaction_type": "rent",
            "property_type": "Penthouse",
            "society_id": society_id,
            "location": "Knowledge Park III",
            "expected_amount": 45000.0,
            "owner_name": "Unique Searchable Name",
            "owner_phone": "9876500099",
        },
    )
    assert prop_res.status_code == 201
    target_id = prop_res.json()["property_id"]

    # Search by property ID
    s_res = await async_client.get(f"/api/v1/properties?search={target_id}", headers=headers)
    assert s_res.status_code == 200
    s_data = s_res.json()
    assert any(p["property_id"] == target_id for p in s_data["items"])

    # Search by owner name substring
    s_owner = await async_client.get("/api/v1/properties?search=Searchable", headers=headers)
    assert s_owner.status_code == 200
    assert any(p["property_id"] == target_id for p in s_owner.json()["items"])

    # Filter by price range
    s_price = await async_client.get("/api/v1/properties?min_price=40000&max_price=50000", headers=headers)
    assert s_price.status_code == 200
    assert any(p["property_id"] == target_id for p in s_price.json()["items"])


@pytest.mark.asyncio
async def test_property_archive_and_unarchive_lifecycle(async_client: AsyncClient):
    """PRD REQ-PROP-04 & 05: Archiving retains records, hides from default view, and allows unarchive."""
    token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    headers = {"Authorization": f"Bearer {token}"}

    soc_res = await async_client.get("/api/v1/societies", headers=headers)
    society_id = soc_res.json()[0]["society_id"]

    # Create property
    c_res = await async_client.post(
        "/api/v1/properties",
        headers=headers,
        json={
            "transaction_type": "rent",
            "property_type": "1 BHK",
            "society_id": society_id,
            "location": "Techzone 4",
            "expected_amount": 16000.0,
            "owner_name": "To Be Archived",
            "owner_phone": "9988776655",
        },
    )
    prop_id = c_res.json()["property_id"]

    # Archive
    arch_res = await async_client.post(f"/api/v1/properties/{prop_id}/archive", headers=headers)
    assert arch_res.status_code == 200
    assert arch_res.json()["status"] == "archived"
    assert arch_res.json()["archived_at"] is not None

    # Excluded from default list
    list_res = await async_client.get(f"/api/v1/properties?search={prop_id}", headers=headers)
    assert list_res.status_code == 200
    assert not any(p["property_id"] == prop_id for p in list_res.json()["items"])

    # Visible when include_archived=true
    list_arch = await async_client.get(f"/api/v1/properties?search={prop_id}&include_archived=true", headers=headers)
    assert list_arch.status_code == 200
    assert any(p["property_id"] == prop_id for p in list_arch.json()["items"])

    # Unarchive
    unarch_res = await async_client.post(f"/api/v1/properties/{prop_id}/unarchive", headers=headers)
    assert unarch_res.status_code == 200
    assert unarch_res.json()["status"] == "available"
    assert unarch_res.json()["archived_at"] is None


@pytest.mark.asyncio
async def test_super_admin_only_permanent_deletion(async_client: AsyncClient):
    """
    PRD REQ-PROP-06 & Section 3.1:
    Only Super Admin can permanently delete records.
    Admin and Executive roles receive 403 Forbidden.
    """
    sa_token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    exec_token = await get_auth_token(async_client, "exec.delete.test@test.com", "executive")
    admin_token = await get_auth_token(async_client, "admin.delete.test@test.com", "admin")

    soc_res = await async_client.get("/api/v1/societies", headers={"Authorization": f"Bearer {sa_token}"})
    society_id = soc_res.json()[0]["id"]

    # Create property to delete
    create_res = await async_client.post(
        "/api/v1/properties",
        headers={"Authorization": f"Bearer {sa_token}"},
        json={
            "transaction_type": "rent",
            "property_type": "2 BHK",
            "society_id": society_id,
            "location": "Noida Extension",
            "expected_amount": 21000.0,
            "owner_name": "Delete Test Owner",
            "owner_phone": "9900112233",
        },
    )
    prop_id = create_res.json()["property_id"]

    # Executive attempts delete -> 403 Forbidden
    del_exec = await async_client.delete(f"/api/v1/properties/{prop_id}", headers={"Authorization": f"Bearer {exec_token}"})
    assert del_exec.status_code == 403

    # Admin attempts delete -> 403 Forbidden
    del_admin = await async_client.delete(f"/api/v1/properties/{prop_id}", headers={"Authorization": f"Bearer {admin_token}"})
    assert del_admin.status_code == 403

    # Super Admin deletes -> 200 OK
    del_sa = await async_client.delete(f"/api/v1/properties/{prop_id}", headers={"Authorization": f"Bearer {sa_token}"})
    assert del_sa.status_code == 200

    # Verify gone
    get_res = await async_client.get(f"/api/v1/properties/{prop_id}", headers={"Authorization": f"Bearer {sa_token}"})
    assert get_res.status_code == 404
