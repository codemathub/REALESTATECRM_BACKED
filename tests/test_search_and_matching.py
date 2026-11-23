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
        json={"email": email, "password": pwd, "device_id": f"device-{role}-srch"},
    )
    if res.status_code != 200:
        await db.users.update_one(
            {"email_normalized": email.lower()},
            {"$set": {"password_hash": get_password_hash(pwd), "account_status": "active"}}
        )
        res = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": pwd, "device_id": f"device-{role}-srch"},
        )
    return res.json()["access_token"]


@pytest.mark.asyncio
async def test_global_search_cross_entity(async_client: AsyncClient):
    """PRD REQ-SRCH-01: Global search returns matching properties, customers, and enquiries."""
    token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    headers = {"Authorization": f"Bearer {token}"}

    db = get_database()
    soc_res = await async_client.get("/api/v1/societies", headers=headers)
    soc_id = soc_res.json()[0]["society_id"]

    # Clean existing test entities
    keyword = "OmegaZeta"
    await db.customers.delete_many({"primary_phone_normalized": "919666611111"})

    # 1. Create Property with keyword in owner name
    p_res = await async_client.post(
        "/api/v1/properties",
        headers=headers,
        json={
            "transaction_type": "rent",
            "property_type": "2 BHK",
            "society_id": soc_id,
            "location": "Greater noida west",
            "expected_amount": 21000.0,
            "owner_name": f"Mr {keyword} Owner",
            "owner_phone": "9666600000",
        },
    )
    assert p_res.status_code == 201

    # 2. Create Customer with keyword in name
    c_res = await async_client.post(
        "/api/v1/customers",
        headers=headers,
        json={
            "name": f"Deepak {keyword}",
            "primary_phone": "+91 96666 11111",
            "customer_type": "tenant",
        },
    )
    assert c_res.status_code == 201
    cid = c_res.json()["customer_id"]

    # 3. Create Enquiry with keyword in notes
    e_res = await async_client.post(
        "/api/v1/enquiries",
        headers=headers,
        json={
            "customer_id": cid,
            "looking_for": "2 BHK",
            "budget_min": 18000,
            "budget_max": 23000,
            "notes": f"High priority for {keyword}",
        },
    )
    assert e_res.status_code == 201

    # Execute global search
    search_res = await async_client.get(f"/api/v1/search?q={keyword}", headers=headers)
    assert search_res.status_code == 200
    data = search_res.json()

    assert data["total"] >= 2
    assert any(keyword in p["owner_name"] for p in data["properties"])
    assert any(keyword in c["name"] for c in data["customers"])


@pytest.mark.asyncio
async def test_global_search_type_filtering(async_client: AsyncClient):
    """PRD REQ-SRCH-01: Global search respects scope filters."""
    token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    headers = {"Authorization": f"Bearer {token}"}

    keyword = "OmegaZeta"

    # Search with type=properties
    p_only = await async_client.get(f"/api/v1/search?q={keyword}&type=properties", headers=headers)
    assert p_only.status_code == 200
    p_data = p_only.json()
    assert len(p_data["properties"]) >= 1
    assert len(p_data["customers"]) == 0
    assert len(p_data["enquiries"]) == 0

    # Search with type=customers
    c_only = await async_client.get(f"/api/v1/search?q={keyword}&type=customers", headers=headers)
    assert c_only.status_code == 200
    c_data = c_only.json()
    assert len(c_data["properties"]) == 0
    assert len(c_data["customers"]) >= 1
    assert len(c_data["enquiries"]) == 0


@pytest.mark.asyncio
async def test_deterministic_matching_rules(async_client: AsyncClient):
    """
    PRD Section 5.10 & REQ-MATCH-01:
    Deterministic matching engine verifies:
    - Rent matches Rent; Sale does not match Rent
    - Available status only; Rented/Sold excluded
    - Compatible BHK and within budget
    - Non-available and out-of-budget strictly excluded
    """
    token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    headers = {"Authorization": f"Bearer {token}"}

    db = get_database()
    soc_create = await async_client.post(
        "/api/v1/societies",
        headers=headers,
        json={"name": "Match Deterministic Society"}
    )
    if soc_create.status_code == 201:
        target_soc = soc_create.json()
    else:
        soc_list = await async_client.get("/api/v1/societies?search=Match+Deterministic", headers=headers)
        target_soc = soc_list.json()[0]

    soc_id = target_soc["society_id"]
    soc_name = target_soc["name"]

    await db.customers.delete_many({"primary_phone_normalized": "919555522222"})

    # Create test customer and enquiry: Rent, 2 BHK, budget 20,000 - 24,000
    cust = await async_client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Match Candidate", "primary_phone": "+91 95555 22222", "customer_type": "tenant"},
    )
    cid = cust.json()["customer_id"]

    enq = await async_client.post(
        "/api/v1/enquiries",
        headers=headers,
        json={
            "customer_id": cid,
            "transaction_type": "rent",
            "looking_for": "2 BHK",
            "preferred_societies": [soc_name],
            "preferred_locations": ["Sector 4 Greater Noida"],
            "budget_min": 20000.0,
            "budget_max": 24000.0,
        },
    )
    eid = enq.json()["enquiry_id"]

    # 1. Candidate Prop A: Rent, 2 BHK, 22000, Available -> MUST MATCH
    prop_a = await async_client.post(
        "/api/v1/properties",
        headers=headers,
        json={
            "transaction_type": "rent",
            "property_type": "2 BHK",
            "society_id": soc_id,
            "location": "Sector 4 Greater Noida",
            "expected_amount": 22000.0,
            "owner_name": "Match A Owner",
            "owner_phone": "9555511001",
        },
    )
    id_a = prop_a.json()["property_id"]

    # 2. Candidate Prop B: Rent, 2 BHK + 2T, 23000, Available -> MUST MATCH (compatible BHK)
    prop_b = await async_client.post(
        "/api/v1/properties",
        headers=headers,
        json={
            "transaction_type": "rent",
            "property_type": "2 BHK + 2T",
            "society_id": soc_id,
            "location": "Sector 4 Greater Noida",
            "expected_amount": 23000.0,
            "owner_name": "Match B Owner",
            "owner_phone": "9555511002",
        },
    )
    id_b = prop_b.json()["property_id"]

    # 3. Candidate Prop C: Rent, 2 BHK, 45000, Available -> MUST NOT MATCH (way over budget)
    prop_c = await async_client.post(
        "/api/v1/properties",
        headers=headers,
        json={
            "transaction_type": "rent",
            "property_type": "2 BHK",
            "society_id": soc_id,
            "location": "Sector 4 Greater Noida",
            "expected_amount": 45000.0,
            "owner_name": "Overbudget Owner",
            "owner_phone": "9555511003",
        },
    )
    id_c = prop_c.json()["property_id"]

    # 4. Candidate Prop D: Sale, 2 BHK, 6500000, Available -> MUST NOT MATCH (Sale vs Rent)
    prop_d = await async_client.post(
        "/api/v1/properties",
        headers=headers,
        json={
            "transaction_type": "sale",
            "property_type": "2 BHK",
            "society_id": soc_id,
            "location": "Sector 4 Greater Noida",
            "expected_amount": 6500000.0,
            "owner_name": "Sale Owner",
            "owner_phone": "9555511004",
        },
    )
    id_d = prop_d.json()["property_id"]

    # 5. Candidate Prop E: Rent, 2 BHK, 22000, RENTED -> MUST NOT MATCH (status rented)
    prop_e = await async_client.post(
        "/api/v1/properties",
        headers=headers,
        json={
            "transaction_type": "rent",
            "property_type": "2 BHK",
            "society_id": soc_id,
            "location": "Sector 4 Greater Noida",
            "expected_amount": 22000.0,
            "status": "rented",
            "owner_name": "Rented Owner",
            "owner_phone": "9555511005",
        },
    )
    id_e = prop_e.json()["property_id"]

    # Query matches for enquiry
    match_res = await async_client.get(f"/api/v1/search/matches/{eid}", headers=headers)
    assert match_res.status_code == 200
    match_data = match_res.json()

    matched_ids = [m["property"]["property_id"] for m in match_data["matches"]]

    # Valid matches
    assert id_a in matched_ids, "Prop A should be matched"
    assert id_b in matched_ids, "Prop B should be matched as compatible BHK"

    # Incompatible / out-of-budget / wrong transaction / rented MUST NOT match
    assert id_c not in matched_ids, "Prop C (over budget) should not be matched"
    assert id_d not in matched_ids, "Prop D (Sale vs Rent) should not be matched"
    assert id_e not in matched_ids, "Prop E (Rented status) should not be matched"

    # Confirm match reasons are populated
    match_a = next(m for m in match_data["matches"] if m["property"]["property_id"] == id_a)
    assert len(match_a["match_reasons"]) >= 2
    assert any("Budget" in r for r in match_a["match_reasons"])


@pytest.mark.asyncio
async def test_matching_is_read_only_and_advisory(async_client: AsyncClient):
    """PRD REQ-MATCH-01: Matching is purely advisory and must not change property status."""
    token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    headers = {"Authorization": f"Bearer {token}"}

    # Query enquiries list
    enq_res = await async_client.get("/api/v1/enquiries", headers=headers)
    assert enq_res.status_code == 200
    items = enq_res.json()["items"]
    assert len(items) >= 1
    eid = items[0]["enquiry_id"]

    # Run match
    res = await async_client.get(f"/api/v1/search/matches/{eid}", headers=headers)
    assert res.status_code == 200

    # Verify matching properties remained available
    for m in res.json()["matches"]:
        pid = m["property"]["property_id"]
        prop_check = await async_client.get(f"/api/v1/properties/{pid}", headers=headers)
        assert prop_check.status_code == 200
        assert prop_check.json()["status"] == "available"
