import pytest
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient
from backend.app.core.database import get_database
from backend.app.core.security import get_password_hash
from backend.app.services.sequence_service import generate_task_id


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
        json={"email": email, "password": pwd, "device_id": f"device-{role}-task"},
    )
    if res.status_code != 200:
        await db.users.update_one(
            {"email_normalized": email.lower()},
            {"$set": {"password_hash": get_password_hash(pwd), "account_status": "active"}}
        )
        res = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": pwd, "device_id": f"device-{role}-task"},
        )
    return res.json()["access_token"]


@pytest.mark.asyncio
async def test_sequential_task_id_generation(async_client: AsyncClient):
    db = get_database()
    task_id1 = await generate_task_id(db)
    task_id2 = await generate_task_id(db)

    assert task_id1.startswith("TSK")
    assert task_id2.startswith("TSK")
    num1 = int(task_id1.replace("TSK", ""))
    num2 = int(task_id2.replace("TSK", ""))
    assert num2 == num1 + 1


@pytest.mark.asyncio
async def test_create_task_and_sla(async_client: AsyncClient):
    admin_token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    exec_token = await get_auth_token(async_client, "task_exec@estateflow.com", "executive")
    headers = {"Authorization": f"Bearer {admin_token}"}

    # Dispatch Operational Directive with 30m SLA
    payload = {
        "title": "Lead Assignment: Commercial Studio",
        "task_type": "lead_assignment",
        "assigned_to": "task_exec@estateflow.com",
        "priority": "high",
        "sla_minutes": 30,
        "target_client_name": "Rohan Kapoor",
        "target_client_phone": "9812345678",
        "monthly_budget": "₹35,000 / mo",
        "asset_preference": "Commercial Studio • Sector 62, Gurgaon",
        "instructions": "Call client within SLA and schedule evening visit.",
    }

    res = await async_client.post("/api/v1/tasks", headers=headers, json=payload)
    assert res.status_code == 201, res.text
    data = res.json()

    assert data["task_id"].startswith("TSK")
    assert data["title"] == payload["title"]
    assert data["status"] == "pending"
    assert data["sla_minutes"] == 30
    assert data["target_client_name"] == "Rohan Kapoor"
    assert data["due_at"] is not None

    # Verify In-App Notification was generated for executive
    db = get_database()
    notif = await db.notifications.find_one({"entity_id": data["task_id"]})
    assert notif is not None
    assert "New Task" in notif["title"]


@pytest.mark.asyncio
async def test_executive_cannot_create_task(async_client: AsyncClient):
    exec_token = await get_auth_token(async_client, "task_exec@estateflow.com", "executive")
    headers = {"Authorization": f"Bearer {exec_token}"}

    payload = {
        "title": "Unauthorized Directive",
        "assigned_to": "task_exec@estateflow.com",
    }
    res = await async_client.post("/api/v1/tasks", headers=headers, json=payload)
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_acknowledge_and_complete_task(async_client: AsyncClient):
    admin_token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    exec_token = await get_auth_token(async_client, "task_exec@estateflow.com", "executive")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    exec_headers = {"Authorization": f"Bearer {exec_token}"}

    # 1. Admin creates key custody task
    create_res = await async_client.post(
        "/api/v1/tasks",
        headers=admin_headers,
        json={
            "title": "Verify Physical Keys for PR-2089",
            "task_type": "key_custody",
            "assigned_to": "task_exec@estateflow.com",
            "lockbox_tag": "LB-04B",
            "sla_minutes": 60,
            "instructions": "Ensure 2 sets of RFID keycards and manual master key are present in locker rack.",
        },
    )
    assert create_res.status_code == 201
    task_id = create_res.json()["task_id"]

    # 2. Executive acknowledges task
    ack_res = await async_client.post(f"/api/v1/tasks/{task_id}/acknowledge", headers=exec_headers)
    assert ack_res.status_code == 200, ack_res.text
    ack_data = ack_res.json()
    assert ack_data["status"] == "acknowledged"
    assert ack_data["acknowledged_at"] is not None

    # 3. Executive completes task with remarks
    comp_res = await async_client.post(
        f"/api/v1/tasks/{task_id}/complete",
        headers=exec_headers,
        json={"remarks": "Keys verified in lockbox #LB-04B. Both RFID tags tested."},
    )
    assert comp_res.status_code == 200, comp_res.text
    comp_data = comp_res.json()
    assert comp_data["status"] == "completed"
    assert comp_data["completed_at"] is not None
    assert "verified" in comp_data["completion_remarks"]


@pytest.mark.asyncio
async def test_cancel_task(async_client: AsyncClient):
    admin_token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    create_res = await async_client.post(
        "/api/v1/tasks",
        headers=admin_headers,
        json={
            "title": "Inspect Society Power Backup",
            "assigned_to": "task_exec@estateflow.com",
        },
    )
    assert create_res.status_code == 201
    task_id = create_res.json()["task_id"]

    cancel_res = await async_client.post(
        f"/api/v1/tasks/{task_id}/cancel",
        headers=admin_headers,
        json={"reason": "Inspection rescheduled by society facility manager"},
    )
    assert cancel_res.status_code == 200
    assert cancel_res.json()["status"] == "cancelled"
    assert "rescheduled" in cancel_res.json()["cancellation_reason"]


@pytest.mark.asyncio
async def test_announcements_broadcast_and_active(async_client: AsyncClient):
    admin_token = await get_auth_token(async_client, "code.mathub@gmail.com", "super_admin")
    exec_token = await get_auth_token(async_client, "task_exec@estateflow.com", "executive")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    exec_headers = {"Authorization": f"Bearer {exec_token}"}

    # Post branch notice
    notice_res = await async_client.post(
        "/api/v1/tasks/announcements/create",
        headers=admin_headers,
        json={
            "title": "Diwali Weekend Duty Roster Finalized",
            "body": "Check Sector 42 branch rota board before Friday 6 PM.",
            "audience": "all",
            "priority": "normal",
        },
    )
    assert notice_res.status_code == 201, notice_res.text
    notice_data = notice_res.json()
    assert notice_data["title"] == "Diwali Weekend Duty Roster Finalized"

    # Executive fetches active announcements
    active_res = await async_client.get("/api/v1/tasks/announcements/active", headers=exec_headers)
    assert active_res.status_code == 200
    active_list = active_res.json()
    assert len(active_list) >= 1
    titles = [a["title"] for a in active_list]
    assert "Diwali Weekend Duty Roster Finalized" in titles


@pytest.mark.asyncio
async def test_notifications_lifecycle_and_push_token(async_client: AsyncClient):
    exec_token = await get_auth_token(async_client, "task_exec@estateflow.com", "executive")
    exec_headers = {"Authorization": f"Bearer {exec_token}"}

    # 1. Register push token
    push_res = await async_client.post(
        "/api/v1/notifications/push-token",
        headers=exec_headers,
        json={"push_token": "ExponentPushToken[xxxxxxxxxxxx]"},
    )
    assert push_res.status_code == 200
    assert push_res.json()["detail"] == "Push token registered successfully"

    # 2. Fetch notifications
    list_res = await async_client.get("/api/v1/notifications", headers=exec_headers)
    assert list_res.status_code == 200
    notifs = list_res.json()["items"]

    if len(notifs) > 0:
        target_notif = notifs[0]
        # Mark single as read
        read_res = await async_client.post(f"/api/v1/notifications/{target_notif['id']}/read", headers=exec_headers)
        assert read_res.status_code == 200

    # 3. Mark all read
    all_read_res = await async_client.post("/api/v1/notifications/read-all", headers=exec_headers)
    assert all_read_res.status_code == 200
    assert "marked as read" in all_read_res.json()["detail"]


@pytest.mark.asyncio
async def test_request_admin_password_reset(async_client: AsyncClient):
    # Ensure executive user exists
    await get_auth_token(async_client, "task_exec@estateflow.com", "executive")

    # Request password reset
    res = await async_client.post(
        "/api/v1/auth/request-admin-password-reset",
        json={"email": "task_exec@estateflow.com"},
    )
    assert res.status_code == 200
    assert "Password reset request submitted" in res.json()["detail"]

    # Verify user record updated in DB
    db = get_database()
    u = await db.users.find_one({"email_normalized": "task_exec@estateflow.com"})
    assert u is not None
    assert u.get("password_reset_requested") is True
