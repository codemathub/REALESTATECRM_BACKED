import pytest
from httpx import AsyncClient
from backend.app.core.database import get_database
from backend.app.core.security import get_password_hash


async def get_auth_token(client: AsyncClient, email: str, role: str) -> str:
    db = get_database()
    pwd = "Admin@123456" if email.lower() == "code.mathub@gmail.com" else "TestPass@123"
    user = await db.users.find_one({"email_normalized": email.lower()})
    if not user:
        from datetime import datetime, timezone
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
        json={"email": email, "password": pwd, "device_id": f"device-{role}-cust"},
    )
    if res.status_code != 200:
        await db.users.update_one(
            {"email_normalized": email.lower()},
            {"$set": {"password_hash": get_password_hash(pwd), "account_status": "active"}}
        )
        res = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": pwd, "device_id": f"device-{role}-cust"},
        )
    return res.json()["access_token"]


@pytest.mark.asyncio
async def test_customer_create_and_phone_normalization(async_client: AsyncClient):
    """PRD REQ-CUS-01: Create customer profile with sequential ID and normalized phone."""
    token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    headers = {"Authorization": f"Bearer {token}"}

    db = get_database()
    await db.customers.delete_many({"primary_phone_normalized": {"$in": ["919811234567", "9811234567"]}})

    payload = {
        "name": "Arjun Kapoor",
        "primary_phone": "+91 (981) 123-4567",
        "alternate_phone": "0120-456789",
        "whatsapp_phone": "9811234567",
        "customer_type": "tenant",
        "lead_source": "Reference",
        "priority": "hot",
        "notes": "Looking to relocate by next month.",
    }

    res = await async_client.post("/api/v1/customers", headers=headers, json=payload)
    assert res.status_code == 201
    cust = res.json()

    assert cust["customer_id"].startswith("CUS")
    assert cust["primary_phone_normalized"] == "919811234567"
    assert cust["whatsapp_phone_normalized"] == "9811234567"
    assert cust["name"] == "Arjun Kapoor"


@pytest.mark.asyncio
async def test_duplicate_phone_check_endpoint(async_client: AsyncClient):
    """PRD Section 5.8: Query backend for duplicate phone matches using normalized phone values."""
    token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    headers = {"Authorization": f"Bearer {token}"}

    # Query with exact digits or formatted variation
    check_res = await async_client.get("/api/v1/customers/check-phone?phone=9811234567", headers=headers)
    assert check_res.status_code == 200
    data = check_res.json()
    assert data["has_duplicate"] is True
    assert len(data["matches"]) >= 1
    match = data["matches"][0]
    assert match["name"] == "Arjun Kapoor"


@pytest.mark.asyncio
async def test_duplicate_phone_requires_shared_phone_reason(async_client: AsyncClient):
    """
    PRD Section 5.8 & Exit Criteria:
    - Attempting duplicate creation without reason is rejected with 409 Conflict.
    - Providing shared_phone_reason allows creating a separate profile.
    """
    token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    headers = {"Authorization": f"Bearer {token}"}

    # Attempt duplicate without reason
    dup_res = await async_client.post(
        "/api/v1/customers",
        headers=headers,
        json={
            "name": "Rohan Kapoor",
            "primary_phone": "+91 9811234567",
            "customer_type": "buyer",
        },
    )
    assert dup_res.status_code == 409

    # Provide shared phone reason
    success_res = await async_client.post(
        "/api/v1/customers",
        headers=headers,
        json={
            "name": "Rohan Kapoor",
            "primary_phone": "+91 9811234567",
            "customer_type": "buyer",
            "shared_phone_reason": "Brother sharing family phone number for investment requirement.",
        },
    )
    assert success_res.status_code == 201
    assert success_res.json()["shared_phone_reason"] is not None


@pytest.mark.asyncio
async def test_one_customer_multiple_enquiries(async_client: AsyncClient):
    """
    PRD REQ-CUS-02 & Exit Criteria:
    One customer can own multiple enquiries.
    """
    token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    headers = {"Authorization": f"Bearer {token}"}

    db = get_database()
    await db.customers.delete_many({"primary_phone_normalized": "919777788888"})

    # Create a fresh customer
    c_res = await async_client.post(
        "/api/v1/customers",
        headers=headers,
        json={
            "name": "Pooja Sharma",
            "primary_phone": "+91 97777 88888",
            "customer_type": "tenant",
        },
    )
    assert c_res.status_code == 201
    cid = c_res.json()["customer_id"]

    # Add Enquiry 1 (2 BHK Rent)
    enq1_res = await async_client.post(
        "/api/v1/enquiries",
        headers=headers,
        json={
            "customer_id": cid,
            "transaction_type": "rent",
            "looking_for": "2 BHK",
            "preferred_locations": ["Sector 4 Greater Noida"],
            "budget_min": 18000.0,
            "budget_max": 22000.0,
            "tenant_type": "Family",
            "priority": "hot",
        },
    )
    assert enq1_res.status_code == 201
    enq1 = enq1_res.json()
    assert enq1["enquiry_id"].startswith("ENQ")
    assert enq1["customer_name"] == "Pooja Sharma"

    # Add Enquiry 2 (3 BHK Sale for parents)
    enq2_res = await async_client.post(
        "/api/v1/enquiries",
        headers=headers,
        json={
            "customer_id": cid,
            "transaction_type": "sale",
            "looking_for": "3 BHK + 3T",
            "preferred_locations": ["Noida Extension"],
            "budget_min": 7500000.0,
            "budget_max": 9000000.0,
            "priority": "warm",
            "notes": "Looking to buy apartment for retired parents.",
        },
    )
    assert enq2_res.status_code == 201
    enq2 = enq2_res.json()
    assert enq2["enquiry_id"] != enq1["enquiry_id"]

    # Verify customer detail reflects both enquiries
    cust_detail = await async_client.get(f"/api/v1/customers/{cid}", headers=headers)
    assert cust_detail.status_code == 200
    c_data = cust_detail.json()
    assert c_data["customer"]["enquiries_count"] == 2
    assert len(c_data["enquiries"]) == 2


@pytest.mark.asyncio
async def test_server_side_enquiry_search_and_budget_filters(async_client: AsyncClient):
    """PRD REQ-ENQ-01: Server-side search and budget range filtering."""
    token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    headers = {"Authorization": f"Bearer {token}"}

    # Filter enquiries with budget range 20000 - 25000
    res = await async_client.get("/api/v1/enquiries?min_budget=19000&max_budget=23000", headers=headers)
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) >= 1
    assert any("2 BHK" in e["looking_for"] for e in items)

    # Search by customer name
    s_res = await async_client.get("/api/v1/enquiries?search=Pooja", headers=headers)
    assert s_res.status_code == 200
    assert any(e["customer_name"] == "Pooja Sharma" for e in s_res.json()["items"])


@pytest.mark.asyncio
async def test_enquiry_status_transition_and_abort(async_client: AsyncClient):
    """PRD Section 5.9: Controlled enquiry status transition and abort with reason."""
    token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    headers = {"Authorization": f"Bearer {token}"}

    db = get_database()
    await db.customers.delete_many({"primary_phone_normalized": "9998881122"})

    # Create customer and enquiry
    c = await async_client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Status Test Cust", "primary_phone": "9998881122"},
    )
    cid = c.json()["customer_id"]

    enq = await async_client.post(
        "/api/v1/enquiries",
        headers=headers,
        json={"customer_id": cid, "looking_for": "1 BHK", "budget_min": 12000, "budget_max": 15000},
    )
    eid = enq.json()["enquiry_id"]

    # Transition to visit_scheduled
    t1 = await async_client.post(
        f"/api/v1/enquiries/{eid}/status",
        headers=headers,
        json={"status": "visit_scheduled", "notes": "Client confirmed visit for tomorrow 4 PM."},
    )
    assert t1.status_code == 200
    assert t1.json()["status"] == "visit_scheduled"

    # Transition to aborted with reason
    t2 = await async_client.post(
        f"/api/v1/enquiries/{eid}/status",
        headers=headers,
        json={"status": "aborted", "reason": "Client signed lease in another city.", "notes": "Deal aborted."},
    )
    assert t2.status_code == 200
    assert t2.json()["status"] == "aborted"
    assert t2.json()["abort_reason"] == "Client signed lease in another city."

    # Inactive/aborted enquiry remains queryable
    aborted_list = await async_client.get(f"/api/v1/enquiries?status=aborted&search={eid}", headers=headers)
    assert aborted_list.status_code == 200
    assert any(e["enquiry_id"] == eid for e in aborted_list.json()["items"])
