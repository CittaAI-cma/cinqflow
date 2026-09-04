"""Login, refresh, /me, and admin-only user provisioning."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cinqflow.api.app import create_app
from cinqflow.auth.security import hash_password
from cinqflow.auth.store import AuthStore
from tests.conftest import requires_db

pytestmark = requires_db


@pytest.fixture
def client(conn, settings) -> TestClient:  # conn creates/drops the schemas
    return TestClient(create_app(settings))


def _create_user(conn, settings, *, email, password, roles, display_name="Test User"):
    # Committed explicitly: `client` (TestClient(create_app(settings))) serves
    # each request off its own connection (api/deps.py's `get_conn`), separate
    # from this fixture's `conn` - an uncommitted insert here is invisible to it.
    user = AuthStore(conn, settings).create_user(
        email=email,
        hashed_password=hash_password(password),
        display_name=display_name,
        role_names=roles,
        created_by="test",
    )
    conn.commit()
    return user


def _login(client, email, password) -> dict:
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_login_succeeds_with_correct_credentials(client, conn, settings):
    _create_user(
        conn,
        settings,
        email="ba@cinqcare.com",
        password="correct-horse-1",
        roles=["business_analyst"],
    )
    body = _login(client, "ba@cinqcare.com", "correct-horse-1")
    assert body["user"]["email"] == "ba@cinqcare.com"
    assert body["user"]["roles"] == ["business_analyst"]
    assert body["access_token"] and body["refresh_token"]


def test_login_rejects_wrong_password(client, conn, settings):
    _create_user(conn, settings, email="ba2@cinqcare.com", password="correct-horse-1", roles=[])
    resp = client.post("/api/auth/login", json={"email": "ba2@cinqcare.com", "password": "wrong"})
    assert resp.status_code == 401


def test_login_rejects_unknown_email(client):
    resp = client.post("/api/auth/login", json={"email": "nope@cinqcare.com", "password": "x"})
    assert resp.status_code == 401


def test_deactivated_user_cannot_login(client, conn, settings):
    user = _create_user(
        conn, settings, email="gone@cinqcare.com", password="correct-horse-1", roles=[]
    )
    AuthStore(conn, settings).set_active(str(user.id), False)
    conn.commit()
    resp = client.post(
        "/api/auth/login", json={"email": "gone@cinqcare.com", "password": "correct-horse-1"}
    )
    assert resp.status_code == 401


def test_me_returns_the_authenticated_user(client, conn, settings):
    _create_user(
        conn, settings, email="me@cinqcare.com", password="correct-horse-1", roles=["operations"]
    )
    tokens = _login(client, "me@cinqcare.com", "correct-horse-1")
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert resp.status_code == 200
    assert resp.json()["roles"] == ["operations"]


def test_me_without_a_token_is_401(client):
    assert client.get("/api/auth/me").status_code == 401


def test_refresh_issues_a_new_pair(client, conn, settings):
    _create_user(conn, settings, email="r@cinqcare.com", password="correct-horse-1", roles=[])
    tokens = _login(client, "r@cinqcare.com", "correct-horse-1")
    resp = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 200
    assert resp.json()["access_token"] != tokens["access_token"]


def test_refresh_rejects_an_access_token(client, conn, settings):
    _create_user(conn, settings, email="r2@cinqcare.com", password="correct-horse-1", roles=[])
    tokens = _login(client, "r2@cinqcare.com", "correct-horse-1")
    resp = client.post("/api/auth/refresh", json={"refresh_token": tokens["access_token"]})
    assert resp.status_code == 401


def test_non_admin_cannot_create_users(client, conn, settings):
    _create_user(
        conn,
        settings,
        email="analyst@cinqcare.com",
        password="correct-horse-1",
        roles=["business_analyst"],
    )
    tokens = _login(client, "analyst@cinqcare.com", "correct-horse-1")
    resp = client.post(
        "/api/users",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
        json={
            "email": "new@cinqcare.com",
            "password": "correct-horse-1",
            "display_name": "New",
            "roles": [],
        },
    )
    assert resp.status_code == 403


def test_admin_can_create_a_user(client, conn, settings):
    _create_user(
        conn,
        settings,
        email="admin@cinqcare.com",
        password="correct-horse-1",
        roles=["administrator"],
    )
    tokens = _login(client, "admin@cinqcare.com", "correct-horse-1")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    resp = client.post(
        "/api/users",
        headers=headers,
        json={
            "email": "new-analyst@cinqcare.com",
            "password": "correct-horse-1",
            "display_name": "New Analyst",
            "roles": ["business_analyst", "read_only"],
        },
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["email"] == "new-analyst@cinqcare.com"
    assert sorted(created["roles"]) == ["business_analyst", "read_only"]
    assert created["is_active"] is True

    # The new user can now log in with the password the admin set.
    _login(client, "new-analyst@cinqcare.com", "correct-horse-1")

    listed = client.get("/api/users", headers=headers)
    assert listed.status_code == 200
    assert any(u["email"] == "new-analyst@cinqcare.com" for u in listed.json())


def test_admin_create_user_rejects_duplicate_email(client, conn, settings):
    _create_user(
        conn,
        settings,
        email="dup-admin@cinqcare.com",
        password="correct-horse-1",
        roles=["administrator"],
    )
    tokens = _login(client, "dup-admin@cinqcare.com", "correct-horse-1")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    body = {
        "email": "dup-admin@cinqcare.com",
        "password": "correct-horse-1",
        "display_name": "Dup",
        "roles": [],
    }
    resp = client.post("/api/users", headers=headers, json=body)
    assert resp.status_code == 409


def test_admin_create_user_rejects_unknown_role(client, conn, settings):
    _create_user(
        conn,
        settings,
        email="admin3@cinqcare.com",
        password="correct-horse-1",
        roles=["administrator"],
    )
    tokens = _login(client, "admin3@cinqcare.com", "correct-horse-1")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    body = {
        "email": "x@cinqcare.com",
        "password": "correct-horse-1",
        "display_name": "X",
        "roles": ["not_a_real_role"],
    }
    resp = client.post("/api/users", headers=headers, json=body)
    assert resp.status_code == 400


def test_admin_can_deactivate_a_user(client, conn, settings):
    _create_user(
        conn,
        settings,
        email="admin4@cinqcare.com",
        password="correct-horse-1",
        roles=["administrator"],
    )
    target = _create_user(
        conn, settings, email="target@cinqcare.com", password="correct-horse-1", roles=[]
    )
    tokens = _login(client, "admin4@cinqcare.com", "correct-horse-1")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    resp = client.patch(f"/api/users/{target.id}", headers=headers, json={"is_active": False})
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    denied = client.post(
        "/api/auth/login", json={"email": "target@cinqcare.com", "password": "correct-horse-1"}
    )
    assert denied.status_code == 401


def test_roles_endpoint_lists_the_mvp_roles(client, conn, settings):
    _create_user(
        conn,
        settings,
        email="admin5@cinqcare.com",
        password="correct-horse-1",
        roles=["administrator"],
    )
    tokens = _login(client, "admin5@cinqcare.com", "correct-horse-1")
    resp = client.get("/api/roles", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert resp.status_code == 200
    names = {r["name"] for r in resp.json()}
    assert names == {
        "business_analyst",
        "data_steward",
        "data_engineer",
        "operations",
        "approver",
        "administrator",
        "read_only",
    }
