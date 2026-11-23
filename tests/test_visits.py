import pytest
from datetime import datetime, timezone, timedelta
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
        json={"email": email, "password": pwd, "device_id": f"device-{role}-vis"},
    )
    if res.status_code != 200:
        await db.users.update_one(
            {"email_normalized": email.lower()},
            {"$set": {"password_hash": get_password_hash(pwd), "account_status": "active"}}
        )
        res = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": pwd, "device_id": f"device-{role}-vis"},
        )
    return res.json()["access_token"]


async def setup_test_enquiry_and_property(client: AsyncClient, headers: dict):
    db = get_database()
    # 1. Society
    soc_res = await client.post(
        "/api/v1/societies",
        headers=headers,
        json={"name": "Visits Enclave Test Society"}
    )
    if soc_res.status_code == 201:
        soc_id = soc_res.json()["society_id"]
    else:
        socs = await client.get("/api/v1/societies?search=Visits+Enclave", headers=headers)
        soc_id = socs.json()[0]["society_id"]

    # 2. Property
    prop_res = await client.post(
        "/api/v1/properties",
        headers=headers,
        json={
            "transaction_type": "rent",
            "property_type": "3 BHK",
            "society_id": soc_id,
            "location": "Sector 62 Gurgaon",
            "expected_amount": 65000.0,
            "owner_name": "Mr Visit Owner",
            "owner_phone": "9777711223",
        },
    )
    pid = prop_res.json()["property_id"]

    # 3. Customer
    cust_phone = "9777799881"
    cust_res = await client.post(
        "/api/v1/customers",
        headers=headers,
        json={
            "name": "Visit Test Customer",
            "primary_phone": cust_phone,
            "customer_type": "tenant",
            "shared_phone_reason": "Automated test customer setup",
        },
    )
    cid = cust_res.json()["customer_id"]

    # 4. Enquiry
    enq_res = await client.post(
        "/api/v1/enquiries",
        headers=headers,
        json={
            "customer_id": cid,
            "looking_for": "3 BHK",
            "budget_min": 60000,
            "budget_max": 70000,
            "notes": "Prefers morning visits",
        },
    )
    eid = enq_res.json()["enquiry_id"]

    return eid, pid


@pytest.mark.asyncio
async def test_admin_creates_unclaimed_visit_and_executive_claims_it(async_client: AsyncClient):
    """PRD Section 5.11: Admin creates visit available for claiming, executive claims it."""
    admin_token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    exec_token = await get_auth_token(async_client, "exec1.visits@estateflow.com", "executive")
    exec_headers = {"Authorization": f"Bearer {exec_token}"}

    eid, pid = await setup_test_enquiry_and_property(async_client, admin_headers)

    visit_time = datetime.now(timezone.utc) + timedelta(days=2)
    create_res = await async_client.post(
        "/api/v1/visits",
        headers=admin_headers,
        json={
            "enquiry_id": eid,
            "property_id": pid,
            "scheduled_at": visit_time.isoformat(),
            "notes": "Client prefers morning sunlight; show clubhouse",
        },
    )
    assert create_res.status_code == 201
    vdata = create_res.json()
    vid = vdata["visit_id"]

    assert vid.startswith("VIS")
    assert vdata["claim_status"] == "unclaimed"
    assert vdata["assigned_to"] is None
    assert vdata["status"] == "scheduled"

    # Executive queries open pool
    pool_res = await async_client.get("/api/v1/visits?open_claims_only=true", headers=exec_headers)
    assert pool_res.status_code == 200
    pool_items = pool_res.json()["items"]
    assert any(v["visit_id"] == vid for v in pool_items)

    # Executive claims visit
    claim_res = await async_client.post(f"/api/v1/visits/{vid}/claim", headers=exec_headers)
    assert claim_res.status_code == 200
    claimed = claim_res.json()
    assert claimed["claim_status"] == "claimed"
    assert claimed["claimed_by"] is not None
    assert claimed["assigned_to"] == claimed["claimed_by"]


@pytest.mark.asyncio
async def test_atomic_claim_race_condition_second_claim_returns_409(async_client: AsyncClient):
    """
    PRD Section 5.11 & REQ-VIS-01:
    Atomic conditional claiming ensures the first claimant wins and the second gets 409 Conflict.
    """
    admin_token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    exec1_token = await get_auth_token(async_client, "exec1.race@estateflow.com", "executive")
    exec1_headers = {"Authorization": f"Bearer {exec1_token}"}

    exec2_token = await get_auth_token(async_client, "exec2.race@estateflow.com", "executive")
    exec2_headers = {"Authorization": f"Bearer {exec2_token}"}

    eid, pid = await setup_test_enquiry_and_property(async_client, admin_headers)

    visit_time = datetime.now(timezone.utc) + timedelta(days=1)
    res = await async_client.post(
        "/api/v1/visits",
        headers=admin_headers,
        json={"enquiry_id": eid, "property_id": pid, "scheduled_at": visit_time.isoformat()},
    )
    vid = res.json()["visit_id"]

    # First claim succeeds
    claim1 = await async_client.post(f"/api/v1/visits/{vid}/claim", headers=exec1_headers)
    assert claim1.status_code == 200
    assert claim1.json()["claim_status"] == "claimed"

    # Second claim fails with 409 Conflict
    claim2 = await async_client.post(f"/api/v1/visits/{vid}/claim", headers=exec2_headers)
    assert claim2.status_code == 409
    assert "already claimed" in claim2.json()["detail"].lower()


@pytest.mark.asyncio
async def test_executive_creates_visit_auto_assigned_to_self(async_client: AsyncClient):
    """PRD Section 5.11: Executive creates visit; by default it is assigned to the creating Executive."""
    admin_token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    exec_token = await get_auth_token(async_client, "exec.creator@estateflow.com", "executive")
    exec_headers = {"Authorization": f"Bearer {exec_token}"}

    eid, pid = await setup_test_enquiry_and_property(async_client, admin_headers)

    visit_time = datetime.now(timezone.utc) + timedelta(days=3)
    res = await async_client.post(
        "/api/v1/visits",
        headers=exec_headers,
        json={"enquiry_id": eid, "property_id": pid, "scheduled_at": visit_time.isoformat()},
    )
    assert res.status_code == 201
    data = res.json()
    assert data["claim_status"] == "direct_assigned"
    assert data["assigned_to"] is not None


@pytest.mark.asyncio
async def test_visit_completion_lifecycle(async_client: AsyncClient):
    """PRD Section 5.11: Complete visit with optional outcome and remarks."""
    token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    headers = {"Authorization": f"Bearer {token}"}

    eid, pid = await setup_test_enquiry_and_property(async_client, headers)

    v_res = await async_client.post(
        "/api/v1/visits",
        headers=headers,
        json={
            "enquiry_id": eid,
            "property_id": pid,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
        },
    )
    vid = v_res.json()["visit_id"]

    comp_res = await async_client.post(
        f"/api/v1/visits/{vid}/complete",
        headers=headers,
        json={"outcome": "Liked Property", "remarks": "Wants to proceed with formal negotiation"},
    )
    assert comp_res.status_code == 200
    comp_data = comp_res.json()
    assert comp_data["status"] == "completed"
    assert comp_data["outcome"] == "Liked Property"


@pytest.mark.asyncio
async def test_visit_reschedule_and_cancel_require_reasons(async_client: AsyncClient):
    """PRD Section 5.11: Reschedule and cancel enforce mandatory reason strings."""
    token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    headers = {"Authorization": f"Bearer {token}"}

    eid, pid = await setup_test_enquiry_and_property(async_client, headers)
    v_res = await async_client.post(
        "/api/v1/visits",
        headers=headers,
        json={
            "enquiry_id": eid,
            "property_id": pid,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        },
    )
    vid = v_res.json()["visit_id"]

    # 1. Reschedule without reason fails
    bad_res = await async_client.post(
        f"/api/v1/visits/{vid}/reschedule",
        headers=headers,
        json={"new_scheduled_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(), "reason": ""},
    )
    assert bad_res.status_code == 422

    # 2. Reschedule with reason succeeds
    good_res = await async_client.post(
        f"/api/v1/visits/{vid}/reschedule",
        headers=headers,
        json={
            "new_scheduled_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
            "reason": "Client travelling out of town",
        },
    )
    assert good_res.status_code == 200
    assert good_res.json()["status"] == "rescheduled"
    assert good_res.json()["reschedule_reason"] == "Client travelling out of town"

    # 3. Cancel with reason succeeds
    cancel_res = await async_client.post(
        f"/api/v1/visits/{vid}/cancel",
        headers=headers,
        json={"reason": "Customer cancelled requirement"},
    )
    assert cancel_res.status_code == 200
    assert cancel_res.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_admin_reassigns_visit_and_executive_forbidden(async_client: AsyncClient):
    """PRD Section 5.11: Only Admin/Super Admin can reassign visit."""
    admin_token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    exec1_token = await get_auth_token(async_client, "exec.one@estateflow.com", "executive")
    exec1_headers = {"Authorization": f"Bearer {exec1_token}"}

    exec2_token = await get_auth_token(async_client, "exec.two@estateflow.com", "executive")

    db = get_database()
    exec2_user = await db.users.find_one({"email_normalized": "exec.two@estateflow.com"})
    exec2_id = str(exec2_user["_id"])

    eid, pid = await setup_test_enquiry_and_property(async_client, admin_headers)
    v_res = await async_client.post(
        "/api/v1/visits",
        headers=admin_headers,
        json={
            "enquiry_id": eid,
            "property_id": pid,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        },
    )
    vid = v_res.json()["visit_id"]

    # Executive attempts to reassign -> 403 Forbidden
    forbidden = await async_client.post(
        f"/api/v1/visits/{vid}/reassign",
        headers=exec1_headers,
        json={"new_assigned_to": exec2_id},
    )
    assert forbidden.status_code == 403

    # Admin reassigns -> 200 OK
    reassigned = await async_client.post(
        f"/api/v1/visits/{vid}/reassign",
        headers=admin_headers,
        json={"new_assigned_to": exec2_id},
    )
    assert reassigned.status_code == 200
    assert reassigned.json()["assigned_to"] == exec2_id
