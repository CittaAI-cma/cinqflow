"""The API refuses a decision by *who* is asking: gates need `can_decide_gates`,
retry needs `can_rerun_steps`, and `/api/auth/me` carries persona + capabilities
so the UI never re-derives the mapping. Role assignment (`PATCH /roles`) is the
admin's way to make any of it true for an existing user."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cinqflow.api.app import create_app
from cinqflow.auth.security import hash_password
from cinqflow.auth.store import AuthStore
from tests.conftest import requires_db

pytestmark = requires_db

UNKNOWN_UPLOAD = "6f0c0000-0000-0000-0000-000000000000"


@pytest.fixture
def client(conn, settings) -> TestClient:  # conn creates/drops the schemas
    return TestClient(create_app(settings))


def _user(conn, settings, email: str, roles: list[str]):
    user = AuthStore(conn, settings).create_user(
        email=email,
        hashed_password=hash_password("correct-horse-1"),
        display_name=email.split("@")[0],
        role_names=roles,
        created_by="test",
    )
    conn.commit()
    return user


def _headers(client, email: str) -> dict[str, str]:
    res = client.post("/api/auth/login", json={"email": email, "password": "correct-horse-1"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


# ------------------------------------------------------------------ /me shape


def test_me_carries_persona_and_capabilities(client, conn, settings):
    _user(conn, settings, "steward@cinqcare.com", ["data_steward"])
    me = client.get("/api/auth/me", headers=_headers(client, "steward@cinqcare.com")).json()
    assert me["persona"] == "data_analyst"
    assert me["capabilities"] == {
        "can_decide_gates": False,
        "can_rerun_steps": False,
        "can_manage_users": False,
    }


def test_login_response_carries_persona_too(client, conn, settings):
    _user(conn, settings, "eng@cinqcare.com", ["data_engineer"])
    res = client.post(
        "/api/auth/login", json={"email": "eng@cinqcare.com", "password": "correct-horse-1"}
    )
    assert res.json()["user"]["persona"] == "data_platform"
    assert res.json()["user"]["capabilities"]["can_rerun_steps"] is True


# ------------------------------------------------------------ gate: G1 approve


def test_approve_without_a_session_is_401(client):
    assert client.post(f"/api/uploads/{UNKNOWN_UPLOAD}/approve", json={}).status_code == 401


def test_steward_cannot_decide_a_gate(client, conn, settings):
    _user(conn, settings, "steward@cinqcare.com", ["data_steward"])
    res = client.post(
        f"/api/uploads/{UNKNOWN_UPLOAD}/approve",
        json={},
        headers=_headers(client, "steward@cinqcare.com"),
    )
    assert res.status_code == 403
    assert res.json()["detail"] == "missing_capability:can_decide_gates"


def test_administrator_alone_cannot_decide_a_gate(client, conn, settings):
    _user(conn, settings, "admin@cinqcare.com", ["administrator"])
    res = client.post(
        f"/api/uploads/{UNKNOWN_UPLOAD}/reject",
        json={},
        headers=_headers(client, "admin@cinqcare.com"),
    )
    assert res.status_code == 403


def test_approver_passes_the_gate_check(client, conn, settings):
    # 404, not 403: the capability check passed and the handler's own logic ran.
    _user(conn, settings, "approver@cinqcare.com", ["approver"])
    res = client.post(
        f"/api/uploads/{UNKNOWN_UPLOAD}/approve",
        json={},
        headers=_headers(client, "approver@cinqcare.com"),
    )
    assert res.status_code == 404


def test_mapping_approve_is_gated_the_same_way(client, conn, settings):
    _user(conn, settings, "eng@cinqcare.com", ["data_engineer"])
    _user(conn, settings, "ba@cinqcare.com", ["business_analyst"])
    path = "/api/feeds/nofeed/mapping-versions/1/approve"
    assert client.post(path, json={}).status_code == 401
    assert (
        client.post(path, json={}, headers=_headers(client, "eng@cinqcare.com")).status_code == 403
    )
    assert (
        client.post(path, json={}, headers=_headers(client, "ba@cinqcare.com")).status_code == 404
    )


# ------------------------------------------------------------------- retry


def test_retry_needs_a_platform_role(client, conn, settings):
    _user(conn, settings, "approver@cinqcare.com", ["approver"])
    _user(conn, settings, "ops@cinqcare.com", ["operations"])
    path = f"/api/uploads/{UNKNOWN_UPLOAD}/retry"
    assert client.post(path).status_code == 401
    denied = client.post(path, headers=_headers(client, "approver@cinqcare.com"))
    assert denied.status_code == 403
    assert denied.json()["detail"] == "missing_capability:can_rerun_steps"
    # Through the gate, into the handler: unknown upload.
    assert client.post(path, headers=_headers(client, "ops@cinqcare.com")).status_code == 404


# ------------------------------------------------------- admin: set roles


def test_admin_replaces_a_users_roles_and_capabilities_follow(client, conn, settings):
    _user(conn, settings, "admin@cinqcare.com", ["administrator"])
    target = _user(conn, settings, "kranthi@cinqcare.com", ["administrator"])
    admin = _headers(client, "admin@cinqcare.com")

    res = client.patch(
        f"/api/users/{target.id}/roles",
        json={"roles": ["administrator", "approver"]},
        headers=admin,
    )
    assert res.status_code == 200, res.text
    assert sorted(res.json()["roles"]) == ["administrator", "approver"]

    # The change is live on the next request: an admin who is also an approver
    # may now decide a gate (404 = past the capability check).
    mine = _headers(client, "kranthi@cinqcare.com")
    assert client.get("/api/auth/me", headers=mine).json()["capabilities"]["can_decide_gates"]
    assert (
        client.post(f"/api/uploads/{UNKNOWN_UPLOAD}/approve", json={}, headers=mine).status_code
        == 404
    )


def test_set_roles_rejects_unknown_roles_and_unknown_users(client, conn, settings):
    _user(conn, settings, "admin@cinqcare.com", ["administrator"])
    target = _user(conn, settings, "x@cinqcare.com", [])
    admin = _headers(client, "admin@cinqcare.com")

    bad = client.patch(f"/api/users/{target.id}/roles", json={"roles": ["wizard"]}, headers=admin)
    assert bad.status_code == 400
    missing = client.patch(
        f"/api/users/{UNKNOWN_UPLOAD}/roles", json={"roles": ["approver"]}, headers=admin
    )
    assert missing.status_code == 404


def test_set_roles_is_admin_only(client, conn, settings):
    _user(conn, settings, "eng@cinqcare.com", ["data_engineer"])
    target = _user(conn, settings, "x@cinqcare.com", [])
    res = client.patch(
        f"/api/users/{target.id}/roles",
        json={"roles": ["approver"]},
        headers=_headers(client, "eng@cinqcare.com"),
    )
    assert res.status_code == 403
