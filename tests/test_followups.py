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
        json={"email": email, "password": pwd, "device_id": f"device-{role}-fol"},
    )
    if res.status_code != 200:
        await db.users.update_one(
            {"email_normalized": email.lower()},
            {"$set": {"password_hash": get_password_hash(pwd), "account_status": "active"}}
        )
        res = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": pwd, "device_id": f"device-{role}-fol"},
        )
    return res.json()["access_token"]


async def setup_test_enquiry(client: AsyncClient, headers: dict) -> str:
    cust_res = await client.post(
        "/api/v1/customers",
        headers=headers,
        json={
            "name": "Followup Test Customer",
            "primary_phone": "9811122334",
            "customer_type": "tenant",
            "shared_phone_reason": "Followup test customer isolation",
        },
    )
    cid = cust_res.json()["customer_id"]

    enq_res = await client.post(
        "/api/v1/enquiries",
        headers=headers,
        json={
            "customer_id": cid,
            "looking_for": "3 BHK in Sector 62",
            "budget_min": 50000,
            "budget_max": 75000,
            "notes": "Followup lifecycle test enquiry",
        },
    )
    return enq_res.json()["enquiry_id"]


@pytest.mark.asyncio
async def test_create_followup_and_enquiry_sync(async_client: AsyncClient):
    """PRD Section 5.12: Follow-up created with sequential FOL ID and enquiry sync."""
    token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    headers = {"Authorization": f"Bearer {token}"}

    eid = await setup_test_enquiry(async_client, headers)

    sched_time = datetime.now(timezone.utc) + timedelta(days=1)
    res = await async_client.post(
        "/api/v1/followups",
        headers=headers,
        json={
            "enquiry_id": eid,
            "scheduled_at": sched_time.isoformat(),
            "notes": "Discuss 3 BHK floor plans and pricing options.",
        },
    )
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["followup_id"].startswith("FOL")
    assert data["enquiry_id"] == eid
    assert data["status"] == "scheduled"
    assert data["notes"] == "Discuss 3 BHK floor plans and pricing options."

    # Verify enquiry derived timestamp
    enq_res = await async_client.get(f"/api/v1/enquiries/{eid}", headers=headers)
    assert enq_res.status_code == 200
    enq_data = enq_res.json()
    assert enq_data["next_followup_at"] is not None


@pytest.mark.asyncio
async def test_followup_range_filtering_and_kpis(async_client: AsyncClient):
    """Test overdue, today, and upcoming range filters and KPI counters."""
    token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    headers = {"Authorization": f"Bearer {token}"}

    eid = await setup_test_enquiry(async_client, headers)
    now = datetime.now(timezone.utc)

    # 1. Past follow-up (Overdue)
    past_time = now - timedelta(hours=5)
    past_res = await async_client.post(
        "/api/v1/followups",
        headers=headers,
        json={
            "enquiry_id": eid,
            "scheduled_at": past_time.isoformat(),
            "notes": "Overdue client callback",
        },
    )
    assert past_res.status_code == 201

    # 2. Today follow-up
    today_time = now.replace(hour=18, minute=0, second=0)
    today_res = await async_client.post(
        "/api/v1/followups",
        headers=headers,
        json={
            "enquiry_id": eid,
            "scheduled_at": today_time.isoformat(),
            "notes": "Evening calling slot",
        },
    )
    assert today_res.status_code == 201

    # 3. Upcoming follow-up
    future_time = now + timedelta(days=3)
    future_res = await async_client.post(
        "/api/v1/followups",
        headers=headers,
        json={
            "enquiry_id": eid,
            "scheduled_at": future_time.isoformat(),
            "notes": "Next week portfolio review",
        },
    )
    assert future_res.status_code == 201

    # Check range=overdue
    overdue_list = await async_client.get("/api/v1/followups?range=overdue", headers=headers)
    assert overdue_list.status_code == 200
    overdue_data = overdue_list.json()
    assert overdue_data["overdue_count"] >= 1
    assert any(item["followup_id"] == past_res.json()["followup_id"] for item in overdue_data["items"])

    # Check range=upcoming
    upcoming_list = await async_client.get("/api/v1/followups?range=upcoming", headers=headers)
    assert upcoming_list.status_code == 200
    upcoming_data = upcoming_list.json()
    assert upcoming_data["upcoming_count"] >= 1
    assert any(item["followup_id"] == future_res.json()["followup_id"] for item in upcoming_data["items"])


@pytest.mark.asyncio
async def test_complete_followup_with_chaining(async_client: AsyncClient):
    """PRD Section 5.12: Completing follow-up derives last_followup_at and optionally chains next."""
    token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    headers = {"Authorization": f"Bearer {token}"}

    eid = await setup_test_enquiry(async_client, headers)
    fol_res = await async_client.post(
        "/api/v1/followups",
        headers=headers,
        json={
            "enquiry_id": eid,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "notes": "Initial requirements qualification",
        },
    )
    fid = fol_res.json()["followup_id"]

    # Complete with next follow-up chaining
    next_time = datetime.now(timezone.utc) + timedelta(days=5)
    comp_res = await async_client.post(
        f"/api/v1/followups/{fid}/complete",
        headers=headers,
        json={
            "outcome": "Client requested callback with spouse",
            "remarks": "Spouse will be available on weekend for joint decision",
            "next_followup_at": next_time.isoformat(),
            "next_followup_notes": "Weekend family callback to lock shortlist",
        },
    )
    assert comp_res.status_code == 200, comp_res.text
    comp_data = comp_res.json()
    assert comp_data["status"] == "completed"
    assert comp_data["outcome"] == "Client requested callback with spouse"
    assert comp_data["completed_at"] is not None

    # Check enquiry derived timestamps: both last_followup_at and next_followup_at must be populated!
    enq_res = await async_client.get(f"/api/v1/enquiries/{eid}", headers=headers)
    assert enq_res.status_code == 200
    enq = enq_res.json()
    assert enq["last_followup_at"] is not None
    assert enq["next_followup_at"] is not None


@pytest.mark.asyncio
async def test_reschedule_and_cancel_followup(async_client: AsyncClient):
    """Test follow-up reschedule and cancel lifecycle."""
    token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    headers = {"Authorization": f"Bearer {token}"}

    eid = await setup_test_enquiry(async_client, headers)
    fol_res = await async_client.post(
        "/api/v1/followups",
        headers=headers,
        json={
            "enquiry_id": eid,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "notes": "Initial follow-up test",
        },
    )
    fid = fol_res.json()["followup_id"]

    # Reschedule
    new_dt = datetime.now(timezone.utc) + timedelta(days=4)
    resched_res = await async_client.post(
        f"/api/v1/followups/{fid}/reschedule",
        headers=headers,
        json={
            "new_scheduled_at": new_dt.isoformat(),
            "reason": "Client traveling out of town till Thursday",
        },
    )
    assert resched_res.status_code == 200
    assert resched_res.json()["reschedule_reason"] == "Client traveling out of town till Thursday"

    # Cancel
    cancel_res = await async_client.post(
        f"/api/v1/followups/{fid}/cancel",
        headers=headers,
        json={"reason": "Client already confirmed site visit"},
    )
    assert cancel_res.status_code == 200
    assert cancel_res.json()["status"] == "cancelled"
    assert cancel_res.json()["cancellation_reason"] == "Client already confirmed site visit"


@pytest.mark.asyncio
async def test_calendar_agenda(async_client: AsyncClient):
    """Test grouped calendar agenda."""
    token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    headers = {"Authorization": f"Bearer {token}"}

    today = datetime.now(timezone.utc)
    start_str = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    end_str = (today + timedelta(days=7)).strftime("%Y-%m-%d")

    agenda_res = await async_client.get(
        f"/api/v1/followups/calendar/agenda?start_date={start_str}&end_date={end_str}",
        headers=headers,
    )
    assert agenda_res.status_code == 200
    agenda = agenda_res.json()
    assert isinstance(agenda, list)
