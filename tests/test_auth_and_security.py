import pytest
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient
from backend.app.core.database import get_database
from backend.app.core.security import get_password_hash, hash_token, generate_secure_token


@pytest.mark.asyncio
async def test_no_public_registration_route(async_client: AsyncClient):
    """PRD REQ-AUTH-01: No public registration endpoint may exist."""
    res = await async_client.post("/api/v1/auth/register", json={"email": "hacker@test.com"})
    assert res.status_code in [404, 405]

    res2 = await async_client.post("/api/v1/register", json={"email": "hacker@test.com"})
    assert res2.status_code in [404, 405]


@pytest.mark.asyncio
async def test_super_admin_login(async_client: AsyncClient):
    """PRD REQ-AUTH-08: Authorized Super Admin can log in and receive session."""
    res = await async_client.post(
        "/api/v1/auth/login",
        json={
            "email": "code.mathub@gmail.com",
            "password": "Admin@123456",
            "device_id": "test-sa-device",
            "platform": "web",
            "device_name": "Test Console",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["user"]["role"] == "super_admin"
    assert data["user"]["email"] == "code.mathub@gmail.com"


@pytest.mark.asyncio
async def test_super_admin_invites_admin(async_client: AsyncClient):
    """PRD REQ-AUTH-02: Super Admin can invite Admin accounts via single-use token."""
    # 1. Login as Super Admin
    login_res = await async_client.post(
        "/api/v1/auth/login",
        json={
            "email": "code.mathub@gmail.com",
            "password": "Admin@123456",
            "device_id": "test-sa-device",
        },
    )
    token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Clean up test admin if present
    db = get_database()
    await db.users.delete_many({"email_normalized": "test.admin@estateflow.com"})

    # 2. Invite Admin
    invite_res = await async_client.post(
        "/api/v1/users/admin",
        headers=headers,
        json={
            "full_name": "Test Company Admin",
            "email": "test.admin@estateflow.com",
            "mobile": "+91 99999 11111",
            "role": "admin",
        },
    )
    assert invite_res.status_code == 200
    data = invite_res.json()
    assert data["email"] == "test.admin@estateflow.com"
    assert data["role"] == "admin"


@pytest.mark.asyncio
async def test_executive_forbidden_from_inviting_users(async_client: AsyncClient):
    """PRD REQ-AUTH-03 & Security Matrix: Executive cannot invite users."""
    db = get_database()
    await db.users.delete_many({"email_normalized": "exec.user@test.com"})

    # Create an executive account
    exec_doc = {
        "full_name": "Exec User",
        "email": "exec.user@test.com",
        "email_normalized": "exec.user@test.com",
        "mobile": "+91 99999 22222",
        "role": "executive",
        "account_status": "active",
        "password_hash": get_password_hash("Exec@123456"),
        "created_at": datetime.now(timezone.utc),
    }
    insert_res = await db.users.insert_one(exec_doc)

    # Login as Executive
    login_res = await async_client.post(
        "/api/v1/auth/login",
        json={
            "email": "exec.user@test.com",
            "password": "Exec@123456",
            "device_id": "test-exec-device",
        },
    )
    exec_token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {exec_token}"}

    # Attempt to invite Admin -> must return 403 Forbidden
    res = await async_client.post(
        "/api/v1/users/admin",
        headers=headers,
        json={
            "full_name": "Hacked Admin",
            "email": "hacked@test.com",
            "mobile": "+91 99999 33333",
            "role": "admin",
        },
    )
    assert res.status_code == 403

    # Attempt to invite Executive -> must return 403 Forbidden
    res2 = await async_client.post(
        "/api/v1/users/executive",
        headers=headers,
        json={
            "full_name": "Hacked Exec",
            "email": "hacked2@test.com",
            "mobile": "+91 99999 44444",
            "role": "executive",
        },
    )
    assert res2.status_code == 403


@pytest.mark.asyncio
async def test_admin_cannot_deactivate_users(async_client: AsyncClient):
    """PRD Section 3.1 & 12.2: Only Super Admin can deactivate accounts."""
    db = get_database()
    # Create an admin account
    await db.users.delete_many({"email_normalized": "simple.admin@test.com"})
    admin_doc = {
        "full_name": "Simple Admin",
        "email": "simple.admin@test.com",
        "email_normalized": "simple.admin@test.com",
        "mobile": "+91 99999 55555",
        "role": "admin",
        "account_status": "active",
        "password_hash": get_password_hash("Admin@123456"),
        "created_at": datetime.now(timezone.utc),
    }
    await db.users.insert_one(admin_doc)

    login_res = await async_client.post(
        "/api/v1/auth/login",
        json={
            "email": "simple.admin@test.com",
            "password": "Admin@123456",
            "device_id": "test-admin-device",
        },
    )
    admin_token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {admin_token}"}

    # Admin attempts to deactivate another Admin account -> must return 403 Forbidden
    peer_admin = {
        "full_name": "Target Admin",
        "email": "target.admin@test.com",
        "email_normalized": "target.admin@test.com",
        "mobile": "+91 99999 66666",
        "role": "admin",
        "account_status": "active",
        "password_hash": get_password_hash("Admin@123456"),
        "created_at": datetime.now(timezone.utc),
    }
    await db.users.delete_many({"email_normalized": "target.admin@test.com"})
    peer_res = await db.users.insert_one(peer_admin)

    res = await async_client.post(f"/api/v1/users/{peer_res.inserted_id}/deactivate", headers=headers)
    assert res.status_code == 403
    assert "Admin accounts can only deactivate executive accounts" in res.json()["detail"]


@pytest.mark.asyncio
async def test_invitation_single_use_activation(async_client: AsyncClient):
    """PRD REQ-AUTH-04 & 05: Invitation token is single-use and invalid after first activation."""
    db = get_database()
    raw_token = generate_secure_token()
    token_h = hash_token(raw_token)

    # Insert invited user and token
    await db.users.delete_many({"email_normalized": "activate.test@estateflow.com"})
    user_res = await db.users.insert_one({
        "full_name": "Activation Candidate",
        "email": "activate.test@estateflow.com",
        "email_normalized": "activate.test@estateflow.com",
        "role": "executive",
        "account_status": "invited",
        "password_hash": None,
        "created_at": datetime.now(timezone.utc),
    })
    await db.invitations.insert_one({
        "user_id": user_res.inserted_id,
        "token_hash": token_h,
        "is_used": False,
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=48),
        "created_at": datetime.now(timezone.utc),
    })

    # Validate token endpoint
    val_res = await async_client.get(f"/api/v1/auth/invitation/{raw_token}")
    assert val_res.status_code == 200
    assert val_res.json()["valid"] is True

    # First activation -> must succeed
    act_res = await async_client.post(
        "/api/v1/auth/activate",
        json={"token": raw_token, "password": "SecurePassword123!"},
    )
    assert act_res.status_code == 200

    # User should now be active
    updated_user = await db.users.find_one({"_id": user_res.inserted_id})
    assert updated_user["account_status"] == "active"

    # Second activation with same token -> must FAIL (single-use enforced)
    act_res_2 = await async_client.post(
        "/api/v1/auth/activate",
        json={"token": raw_token, "password": "SecurePassword123!"},
    )
    assert act_res_2.status_code == 400


@pytest.mark.asyncio
async def test_authoritative_7_day_inactivity_rule(async_client: AsyncClient):
    """PRD REQ-AUTH-08: Inactive sessions > 7 days are strictly expired server-side."""
    # Login to obtain a session
    login_res = await async_client.post(
        "/api/v1/auth/login",
        json={
            "email": "code.mathub@gmail.com",
            "password": "Admin@123456",
            "device_id": "inactivity-test-device",
        },
    )
    access_token = login_res.json()["access_token"]
    refresh_token = login_res.json()["refresh_token"]

    db = get_database()
    t_hash = hash_token(refresh_token)

    # Manually backdate session's last_activity_at by 8 days
    eight_days_ago = datetime.now(timezone.utc) - timedelta(days=8)
    await db.sessions.update_one(
        {"refresh_token_hash": t_hash},
        {"$set": {"last_activity_at": eight_days_ago}}
    )

    # 1. Protected API call with access token -> must fail with 401
    headers = {"Authorization": f"Bearer {access_token}"}
    me_res = await async_client.get("/api/v1/auth/me", headers=headers)
    assert me_res.status_code == 401
    assert "7 days of inactivity" in me_res.json()["detail"]

    # 2. Refresh token attempt -> must fail with 401
    refresh_res = await async_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token, "device_id": "inactivity-test-device"},
    )
    assert refresh_res.status_code == 401


@pytest.mark.asyncio
async def test_remote_session_revocation(async_client: AsyncClient):
    """PRD REQ-AUTH-09: Revoking a session blocks subsequent requests immediately."""
    # Login
    login_res = await async_client.post(
        "/api/v1/auth/login",
        json={
            "email": "code.mathub@gmail.com",
            "password": "Admin@123456",
            "device_id": "revoke-test-device",
        },
    )
    token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Verify /me works
    me_res = await async_client.get("/api/v1/auth/me", headers=headers)
    assert me_res.status_code == 200

    # Logout / Revoke session
    logout_res = await async_client.post("/api/v1/auth/logout", headers=headers)
    assert logout_res.status_code == 200

    # Next call with same token -> must return 401
    me_res2 = await async_client.get("/api/v1/auth/me", headers=headers)
    assert me_res2.status_code == 401


@pytest.mark.asyncio
async def test_deactivated_account_rejected(async_client: AsyncClient):
    """PRD REQ-AUTH-07: Deactivated accounts cannot access protected routes even with valid token."""
    db = get_database()
    await db.users.delete_many({"email_normalized": "deactivate.test@estateflow.com"})

    user_res = await db.users.insert_one({
        "full_name": "Deactivate Candidate",
        "email": "deactivate.test@estateflow.com",
        "email_normalized": "deactivate.test@estateflow.com",
        "role": "executive",
        "account_status": "active",
        "password_hash": get_password_hash("Pass@123456"),
        "created_at": datetime.now(timezone.utc),
    })

    login_res = await async_client.post(
        "/api/v1/auth/login",
        json={
            "email": "deactivate.test@estateflow.com",
            "password": "Pass@123456",
            "device_id": "deactivate-device",
        },
    )
    token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Manually deactivate user
    await db.users.update_one(
        {"_id": user_res.inserted_id},
        {"$set": {"account_status": "deactivated"}}
    )

    # API request must now be rejected with 403 Forbidden
    res = await async_client.get("/api/v1/auth/me", headers=headers)
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_direct_user_creation_and_login_without_invite_link(async_client: AsyncClient):
    """Admin/Super Admin creates active user directly with credentials."""
    # 1. Login as Super Admin
    sa_res = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "code.mathub@gmail.com", "password": "Admin@123456", "device_id": "sa-dev"},
    )
    sa_token = sa_res.json()["access_token"]
    sa_headers = {"Authorization": f"Bearer {sa_token}"}

    # 2. Clean up if test email exists
    db = get_database()
    await db.users.delete_many({"email_normalized": "direct.exec@estateflow.com"})

    # 3. Create Executive directly with initial password
    create_res = await async_client.post(
        "/api/v1/users/create",
        headers=sa_headers,
        json={
            "full_name": "Direct Executive",
            "email": "direct.exec@estateflow.com",
            "role": "executive",
            "password": "ExecDirect@123",
            "mobile": "9811122233",
        },
    )
    assert create_res.status_code == 201
    assert create_res.json()["account_status"] == "active"
    user_id = create_res.json()["id"]

    # 4. Dummy password attempt must be strictly rejected (401)
    bad_login = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "direct.exec@estateflow.com", "password": "WrongPassword", "device_id": "mob-1"},
    )
    assert bad_login.status_code == 401

    # 5. Correct credentials immediately succeed without any invitation link
    good_login = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "direct.exec@estateflow.com", "password": "ExecDirect@123", "device_id": "mob-1"},
    )
    assert good_login.status_code == 200
    assert "access_token" in good_login.json()

    # 6. Super Admin resets password directly
    reset_res = await async_client.post(
        f"/api/v1/users/{user_id}/reset-password",
        headers=sa_headers,
        json={"new_password": "NewSecretPass@123"},
    )
    assert reset_res.status_code == 200

    # 7. Old password must now fail
    old_login = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "direct.exec@estateflow.com", "password": "ExecDirect@123", "device_id": "mob-1"},
    )
    assert old_login.status_code == 401

    # 8. New password succeeds
    new_login = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "direct.exec@estateflow.com", "password": "NewSecretPass@123", "device_id": "mob-1"},
    )
    assert new_login.status_code == 200


@pytest.mark.asyncio
async def test_admin_cannot_create_admin_only_executive(async_client: AsyncClient):
    """
    Strict User Provisioning RBAC:
    - Admin can ONLY create Executive accounts.
    - Admin CANNOT create Admin accounts (403 Forbidden).
    - Super Admin can create Admin and Executive accounts.
    - Super Admin cannot be created via user provisioning.
    """
    db = get_database()
    await db.users.delete_many({"email_normalized": {"$in": [
        "branch.admin.test@estateflow.com",
        "illegal.admin@estateflow.com",
        "legit.exec@estateflow.com",
        "sa.created.admin@estateflow.com",
    ]}})

    # 1. Setup a valid Admin account
    admin_doc = {
        "full_name": "Branch Admin",
        "email": "branch.admin.test@estateflow.com",
        "email_normalized": "branch.admin.test@estateflow.com",
        "mobile": "+91 98888 11111",
        "role": "admin",
        "account_status": "active",
        "password_hash": get_password_hash("AdminPass@123"),
        "created_at": datetime.now(timezone.utc),
    }
    await db.users.insert_one(admin_doc)

    # 2. Login as Admin
    admin_login = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "branch.admin.test@estateflow.com", "password": "AdminPass@123", "device_id": "adm-dev-1"},
    )
    assert admin_login.status_code == 200
    admin_token = admin_login.json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 3. Admin attempts to create another Admin -> MUST BE 403 FORBIDDEN
    blocked_admin_create = await async_client.post(
        "/api/v1/users/create",
        headers=admin_headers,
        json={
            "full_name": "Illegal Admin",
            "email": "illegal.admin@estateflow.com",
            "role": "admin",
            "password": "Secret@123456",
            "mobile": "9812345678",
        },
    )
    assert blocked_admin_create.status_code == 403
    assert "Only Super Admin can create Admin accounts" in blocked_admin_create.json()["detail"]

    # 4. Admin creates an Executive -> MUST SUCCEED (201 Created)
    legit_exec_create = await async_client.post(
        "/api/v1/users/create",
        headers=admin_headers,
        json={
            "full_name": "Legit Field Exec",
            "email": "legit.exec@estateflow.com",
            "role": "executive",
            "password": "Secret@123456",
            "mobile": "9812345679",
        },
    )
    assert legit_exec_create.status_code == 201
    assert legit_exec_create.json()["role"] == "executive"

    # 5. Admin attempts to create Super Admin -> MUST BE 403 FORBIDDEN
    blocked_sa_create = await async_client.post(
        "/api/v1/users/create",
        headers=admin_headers,
        json={
            "full_name": "Illegal SA",
            "email": "illegal.sa@estateflow.com",
            "role": "super_admin",
            "password": "Secret@123456",
            "mobile": "9812345670",
        },
    )
    assert blocked_sa_create.status_code == 403

    # 6. Super Admin can create Admin
    sa_login = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "code.mathub@gmail.com", "password": "Admin@123456", "device_id": "sa-dev-1"},
    )
    assert sa_login.status_code == 200
    sa_token = sa_login.json()["access_token"]
    sa_headers = {"Authorization": f"Bearer {sa_token}"}

    sa_create_admin = await async_client.post(
        "/api/v1/users/create",
        headers=sa_headers,
        json={
            "full_name": "SA Created Admin",
            "email": "sa.created.admin@estateflow.com",
            "role": "admin",
            "password": "Secret@123456",
            "mobile": "9812345671",
        },
    )
    assert sa_create_admin.status_code == 201
    assert sa_create_admin.json()["role"] == "admin"


@pytest.mark.asyncio
async def test_admin_deactivation_permissions(async_client: AsyncClient):
    """
    PRD REQ-AUTH-03 & Security Rule:
    Admin can deactivate executive accounts, but is strictly 403 Forbidden from deactivating Admin or Super Admin.
    """
    db = get_database()
    await db.users.delete_many({"email_normalized": {"$in": [
        "admin.actor@estateflow.com",
        "peer.admin@estateflow.com",
        "subordinate.exec@estateflow.com",
    ]}})

    # 1. Setup Admin Actor
    admin_actor = {
        "full_name": "Admin Actor",
        "email": "admin.actor@estateflow.com",
        "email_normalized": "admin.actor@estateflow.com",
        "mobile": "+91 97777 11111",
        "role": "admin",
        "account_status": "active",
        "password_hash": get_password_hash("AdminPass@123"),
        "created_at": datetime.now(timezone.utc),
    }
    await db.users.insert_one(admin_actor)

    # 2. Setup Peer Admin
    peer_admin = {
        "full_name": "Peer Admin",
        "email": "peer.admin@estateflow.com",
        "email_normalized": "peer.admin@estateflow.com",
        "mobile": "+91 97777 22222",
        "role": "admin",
        "account_status": "active",
        "password_hash": get_password_hash("AdminPass@123"),
        "created_at": datetime.now(timezone.utc),
    }
    peer_res = await db.users.insert_one(peer_admin)
    peer_admin_id = str(peer_res.inserted_id)

    # 3. Setup Subordinate Exec
    sub_exec = {
        "full_name": "Sub Exec",
        "email": "subordinate.exec@estateflow.com",
        "email_normalized": "subordinate.exec@estateflow.com",
        "mobile": "+91 97777 33333",
        "role": "executive",
        "account_status": "active",
        "password_hash": get_password_hash("ExecPass@123"),
        "created_at": datetime.now(timezone.utc),
    }
    exec_res = await db.users.insert_one(sub_exec)
    exec_id = str(exec_res.inserted_id)

    # Login as Admin Actor
    login_res = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "admin.actor@estateflow.com", "password": "AdminPass@123", "device_id": "adm-actor-dev"},
    )
    assert login_res.status_code == 200
    admin_token = login_res.json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 4. Admin tries to deactivate Peer Admin -> 403 Forbidden
    deact_peer = await async_client.post(
        f"/api/v1/users/{peer_admin_id}/deactivate",
        headers=admin_headers,
    )
    assert deact_peer.status_code == 403
    assert "Admin accounts can only deactivate executive accounts" in deact_peer.json()["detail"]

    # 5. Admin deactivates Subordinate Exec -> 200 OK
    deact_exec = await async_client.post(
        f"/api/v1/users/{exec_id}/deactivate",
        headers=admin_headers,
    )
    assert deact_exec.status_code == 200

    # Verify status in database
    updated_exec = await db.users.find_one({"_id": exec_res.inserted_id})
    assert updated_exec["account_status"] == "deactivated"
