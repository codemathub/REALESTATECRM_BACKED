import pytest
from datetime import datetime, timezone
from httpx import AsyncClient
from backend.app.core.database import get_database
from backend.app.core.security import get_password_hash
from backend.app.services.sequence_service import generate_deal_id


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
        json={"email": email, "password": pwd, "device_id": f"device-{role}-deal"},
    )
    if res.status_code != 200:
        await db.users.update_one(
            {"email_normalized": email.lower()},
            {"$set": {"password_hash": get_password_hash(pwd), "account_status": "active"}}
        )
        res = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": pwd, "device_id": f"device-{role}-deal"},
        )
    return res.json()["access_token"]


@pytest.mark.asyncio
async def test_sequential_deal_id_generation(async_client: AsyncClient):
    db = get_database()
    deal_id1 = await generate_deal_id(db)
    deal_id2 = await generate_deal_id(db)

    assert deal_id1.startswith("DEA")
    assert deal_id2.startswith("DEA")
    num1 = int(deal_id1.replace("DEA", ""))
    num2 = int(deal_id2.replace("DEA", ""))
    assert num2 == num1 + 1


@pytest.mark.asyncio
async def test_rent_deal_close_updates_property_to_rented(async_client: AsyncClient):
    token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    headers = {"Authorization": f"Bearer {token}"}
    db = get_database()

    # 1. Create a rental property
    prop_res = await async_client.post(
        "/api/v1/properties",
        headers=headers,
        json={
            "transaction_type": "rent",
            "property_type": "Apartment",
            "bhk": "2 BHK",
            "society_id": "SOC0001",
            "location": "Sector 43, Gurgaon",
            "expected_amount": 35000,
            "currency": "INR",
            "negotiable": True,
            "owner_name": "Rajiv Kapoor",
            "owner_phone": "+91 98111 22334",
            "maintenance_mode": "included",
        },
    )
    assert prop_res.status_code == 201
    prop_data = prop_res.json()
    prop_id = prop_data["property_id"]

    # 2. Create customer and rent enquiry
    cust_res = await async_client.post(
        "/api/v1/customers",
        headers=headers,
        json={
            "name": "Amit Sharma",
            "primary_phone": "+91 99112 33445",
            "customer_type": "tenant",
        },
    )
    assert cust_res.status_code == 201
    cust_id = cust_res.json()["customer_id"]

    enq_res = await async_client.post(
        "/api/v1/enquiries",
        headers=headers,
        json={
            "customer_id": cust_id,
            "transaction_type": "rent",
            "looking_for": "2 BHK",
            "budget_min": 30000,
            "budget_max": 40000,
        },
    )
    assert enq_res.status_code == 201
    enq_id = enq_res.json()["enquiry_id"]

    # 3. Close Deal
    close_res = await async_client.post(
        f"/api/v1/enquiries/{enq_id}/close-deal",
        headers=headers,
        json={
            "property_id": prop_id,
            "deal_amount": 34000,
            "final_remarks": "Agreement signed for 11 months with security deposit.",
        },
    )
    assert close_res.status_code == 200
    deal_data = close_res.json()
    assert deal_data["deal_id"].startswith("DEA")
    assert deal_data["property_id"] == prop_id
    assert deal_data["transaction_type"] == "rent"
    assert deal_data["deal_amount"] == 34000

    # 4. Verify property status automatically changed to "rented"
    prop_check = await async_client.get(f"/api/v1/properties/{prop_id}", headers=headers)
    assert prop_check.status_code == 200
    assert prop_check.json()["status"] == "rented"

    # 5. Verify enquiry status updated to "deal_closed"
    enq_check = await async_client.get(f"/api/v1/enquiries/{enq_id}", headers=headers)
    assert enq_check.status_code == 200
    assert enq_check.json()["status"] == "deal_closed"


@pytest.mark.asyncio
async def test_sale_deal_close_updates_property_to_sold(async_client: AsyncClient):
    token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create a sale property
    prop_res = await async_client.post(
        "/api/v1/properties",
        headers=headers,
        json={
            "transaction_type": "sale",
            "property_type": "Floor",
            "bhk": "3 BHK",
            "society_id": "SOC0001",
            "location": "Golf Course Road, Gurgaon",
            "expected_amount": 18500000,
            "currency": "INR",
            "negotiable": True,
            "owner_name": "Suresh Singhal",
            "owner_phone": "+91 98222 33445",
            "maintenance_mode": "extra",
        },
    )
    assert prop_res.status_code == 201
    prop_id = prop_res.json()["property_id"]

    # 2. Create customer and sale enquiry
    cust_res = await async_client.post(
        "/api/v1/customers",
        headers=headers,
        json={
            "name": "Vikram Sethi",
            "primary_phone": "+91 97333 44556",
            "customer_type": "buyer",
        },
    )
    assert cust_res.status_code == 201
    cust_id = cust_res.json()["customer_id"]

    enq_res = await async_client.post(
        "/api/v1/enquiries",
        headers=headers,
        json={
            "customer_id": cust_id,
            "transaction_type": "sale",
            "looking_for": "3 BHK",
            "budget_min": 15000000,
            "budget_max": 20000000,
        },
    )
    assert enq_res.status_code == 201
    enq_id = enq_res.json()["enquiry_id"]

    # 3. Close Deal
    close_res = await async_client.post(
        f"/api/v1/enquiries/{enq_id}/close-deal",
        headers=headers,
        json={
            "property_id": prop_id,
            "deal_amount": 18000000,
            "final_remarks": "Registry completed at sub-registrar office.",
        },
    )
    assert close_res.status_code == 200

    # 4. Verify property status automatically changed to "sold"
    prop_check = await async_client.get(f"/api/v1/properties/{prop_id}", headers=headers)
    assert prop_check.status_code == 200
    assert prop_check.json()["status"] == "sold"


@pytest.mark.asyncio
async def test_abort_enquiry_requires_reason(async_client: AsyncClient):
    token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create enquiry
    cust_res = await async_client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Rohit Verma", "primary_phone": "+91 96444 55667", "customer_type": "tenant"},
    )
    cust_id = cust_res.json()["customer_id"]

    enq_res = await async_client.post(
        "/api/v1/enquiries",
        headers=headers,
        json={"customer_id": cust_id, "transaction_type": "rent", "looking_for": "1 BHK", "budget_min": 15000, "budget_max": 22000},
    )
    enq_id = enq_res.json()["enquiry_id"]

    # 2. Abort enquiry
    abort_res = await async_client.post(
        f"/api/v1/enquiries/{enq_id}/abort",
        headers=headers,
        json={
            "reason": "Client relocated to Bangalore due to company transfer",
            "final_remarks": "No longer seeking Delhi NCR property.",
        },
    )
    assert abort_res.status_code == 200
    aborted_data = abort_res.json()
    assert aborted_data["status"] == "aborted"
    assert "Bangalore" in aborted_data["abort_reason"]


@pytest.mark.asyncio
async def test_close_deal_from_visit(async_client: AsyncClient):
    token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create property & enquiry
    prop_res = await async_client.post(
        "/api/v1/properties",
        headers=headers,
        json={
            "transaction_type": "rent",
            "property_type": "Apartment",
            "society_id": "SOC0001",
            "location": "DLF Phase 5",
            "expected_amount": 50000,
            "currency": "INR",
            "negotiable": True,
            "owner_name": "Meera Taneja",
            "owner_phone": "+91 95555 66778",
            "maintenance_mode": "included",
        },
    )
    prop_id = prop_res.json()["property_id"]

    cust_res = await async_client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Sunil Grover", "primary_phone": "+91 94666 77889", "customer_type": "tenant"},
    )
    cust_id = cust_res.json()["customer_id"]

    enq_res = await async_client.post(
        "/api/v1/enquiries",
        headers=headers,
        json={"customer_id": cust_id, "transaction_type": "rent", "looking_for": "3 BHK", "budget_min": 45000, "budget_max": 55000},
    )
    enq_id = enq_res.json()["enquiry_id"]

    # 2. Schedule visit
    visit_res = await async_client.post(
        "/api/v1/visits",
        headers=headers,
        json={
            "enquiry_id": enq_id,
            "property_id": prop_id,
            "scheduled_at": "2026-11-25T14:00:00Z",
            "notes": "Client requested key at site",
        },
    )
    assert visit_res.status_code == 201
    visit_id = visit_res.json()["visit_id"]

    # 3. Close deal from visit
    close_from_visit = await async_client.post(
        f"/api/v1/visits/{visit_id}/close-deal",
        headers=headers,
        json={"final_remarks": "Deal closed on site after walkthrough"},
    )
    assert close_from_visit.status_code == 200
    deal_data = close_from_visit.json()
    assert deal_data["property_id"] == prop_id

    # 4. Check visit status is completed
    v_check = await async_client.get(f"/api/v1/visits/{visit_id}", headers=headers)
    assert v_check.status_code == 200
    assert v_check.json()["status"] == "completed"
    assert "Deal Closed" in v_check.json()["outcome"]


@pytest.mark.asyncio
async def test_archives_stats_and_queries(async_client: AsyncClient):
    token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    headers = {"Authorization": f"Bearer {token}"}

    stats_res = await async_client.get("/api/v1/archives/stats", headers=headers)
    assert stats_res.status_code == 200
    stats = stats_res.json()
    assert "closed_deals_count" in stats
    assert "aborted_enquiries_count" in stats
    assert "rented_properties_count" in stats
    assert "sold_properties_count" in stats

    props_res = await async_client.get("/api/v1/archives/properties", headers=headers)
    assert props_res.status_code == 200
    assert "items" in props_res.json()

    enqs_res = await async_client.get("/api/v1/archives/enquiries", headers=headers)
    assert enqs_res.status_code == 200
    assert "items" in enqs_res.json()

    deals_res = await async_client.get("/api/v1/archives/deals", headers=headers)
    assert deals_res.status_code == 200
    assert "items" in deals_res.json()


@pytest.mark.asyncio
async def test_restore_and_permanent_delete_lifecycle(async_client: AsyncClient):
    admin_token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    exec_token = await get_auth_token(async_client, "arch_exec@estateflow.com", "executive")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    exec_headers = {"Authorization": f"Bearer {exec_token}"}

    # 1. Create property and archive it
    prop_res = await async_client.post(
        "/api/v1/properties",
        headers=admin_headers,
        json={
            "transaction_type": "rent",
            "property_type": "Studio",
            "society_id": "SOC0001",
            "location": "Cyber City",
            "expected_amount": 25000,
            "currency": "INR",
            "negotiable": False,
            "owner_name": "Karan Johar",
            "owner_phone": "+91 93333 44455",
            "maintenance_mode": "included",
        },
    )
    prop_id = prop_res.json()["property_id"]

    await async_client.post(f"/api/v1/properties/{prop_id}/archive", headers=admin_headers)

    # 2. Restore property back to available
    restore_res = await async_client.post(f"/api/v1/archives/properties/{prop_id}/restore", headers=admin_headers)
    assert restore_res.status_code == 200
    assert restore_res.json()["restored_status"] == "available"

    # Verify property is available again
    p_check = await async_client.get(f"/api/v1/properties/{prop_id}", headers=admin_headers)
    assert p_check.json()["status"] == "available"

    # 3. Role-Negative test: Executive cannot permanently delete (403 Forbidden)
    exec_del = await async_client.request(
        "DELETE",
        f"/api/v1/archives/properties/{prop_id}/permanent",
        headers=exec_headers,
        json={"reason": "Attempt by executive"},
    )
    assert exec_del.status_code == 403

    # 4. Super Admin exceptional permanent delete with mandatory reason
    sa_del = await async_client.request(
        "DELETE",
        f"/api/v1/archives/properties/{prop_id}/permanent",
        headers=admin_headers,
        json={"reason": "Audit-authorized expungement of test unit"},
    )
    assert sa_del.status_code == 200
    assert "permanently deleted" in sa_del.json()["detail"]

    # 5. Verify property physically no longer exists in DB
    p_deleted = await async_client.get(f"/api/v1/properties/{prop_id}", headers=admin_headers)
    assert p_deleted.status_code == 404
