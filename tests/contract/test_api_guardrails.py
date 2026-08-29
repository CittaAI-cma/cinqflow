"""CF-V0-E2-01 — the refusals, made at the server, with a row to prove it.

    "Given a Read-Only user crafts a direct link to an edit screen, when they
     open it, then the request is DENIED AT THE SERVER (not just hidden in the
     menu), and the attempt is recorded."

Written before the edit route existed. A guardrail nobody tries is a comment,
not a control — so every test here MAKES THE ATTEMPT and asserts two things:
the refusal, and the audit row.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.api import create_app
from cinqflow.api.deps import declared_action
from cinqflow.core.model.governed import ObjectType
from cinqflow.core.model.vocabulary import ActorType, StatusWord
from cinqflow.core.security import Action
from cinqflow.ports.authn import Principal, Role, Scopes

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

ENGINEER = "dev-engineer@cinqcare.test"
READ_ONLY = "dev-analyst@cinqcare.test"
ADMIN = "dev-admin@cinqcare.test"
NO_GROUP = "dev-nogroup@cinqcare.test"

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Authenticated, but with no permission beyond being signed in. Both exist so
#: a user in NO group reaches a clear answer instead of a broken application.
UNPERMISSIONED = frozenset({"/api/me", "/api/navigation"})

FEED_ID = "fidelis-downstate-roster"

FEED_BODY: dict[str, Any] = {
    "feed_id": FEED_ID,
    "domain": "membership",
    "source_system": "fidelis",
    "file_format": "xlsx",
    "landing_path": "landing/fidelis/roster",
    "file_pattern": r"^_CINQDOWNSTATE_Member_Roster_\d{8}\.xlsx$",
    "schedule_cron": "0 6 * * 1",
    "sample_filename": "_CINQDOWNSTATE_Member_Roster_20260801.xlsx",
}


@pytest.fixture
def store() -> MemMetadataDb:
    return MemMetadataDb()


@pytest.fixture
def app(store: MemMetadataDb) -> FastAPI:
    return create_app(authn=StaticAuthn(), metadata_db=store)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _as(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


def _audit(store: MemMetadataDb, **kw: Any) -> list[Any]:
    return list(store.read_audit(**kw))


# ── nobody anonymous ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/me"),
        ("GET", "/api/feeds"),
        ("GET", "/api/feeds/anything"),
        ("POST", "/api/feeds"),
        ("PUT", "/api/feeds/anything"),
        ("GET", "/api/audit"),
        ("GET", "/api/users"),
        ("GET", "/api/execution-plane/contracts"),
    ],
)
def test_no_route_serves_an_anonymous_caller(client: TestClient, method: str, path: str) -> None:
    assert client.request(method, path, json=FEED_BODY).status_code == 401


def test_an_unknown_token_is_refused_never_defaulted(client: TestClient) -> None:
    response = client.get("/api/feeds", headers=_as("someone-who-does-not-exist"))
    assert response.status_code == 401
    assert "anonymously" in response.json()["detail"]


def test_healthz_needs_no_token_and_reveals_nothing_about_the_estate(
    client: TestClient,
) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ── THE guardrail ────────────────────────────────────────────────────────────


def test_read_only_crafting_an_edit_url_is_denied_at_the_server_and_recorded(
    client: TestClient, store: MemMetadataDb
) -> None:
    """The story's headline negative. Not hidden in a menu — refused on the wire."""
    response = client.put(f"/api/feeds/{FEED_ID}", json=FEED_BODY, headers=_as(READ_ONLY))

    assert response.status_code == 403
    assert "not permitted" in response.json()["detail"]

    trail = _audit(store)
    assert [e.action for e in trail] == ["denied:edit_feed"]
    assert trail[0].actor.subject == READ_ONLY
    assert trail[0].actor_type is ActorType.HUMAN, (
        "the audit entry states whether the actor was human, system or AI — never inferred"
    )
    assert trail[0].detail, "a denial with no reason is a row nobody can review"


def test_read_only_cannot_create_a_feed_and_the_attempt_is_recorded(
    client: TestClient, store: MemMetadataDb
) -> None:
    assert client.post("/api/feeds", json=FEED_BODY, headers=_as(READ_ONLY)).status_code == 403
    assert [e.action for e in _audit(store)] == ["denied:create_feed"]


def test_read_only_may_read_everything(client: TestClient) -> None:
    """Read-Only means full visibility and no buttons — not a reduced view."""
    for path in ("/api/feeds", "/api/audit", "/api/execution-plane/contracts"):
        assert client.get(path, headers=_as(READ_ONLY)).status_code == 200


def test_an_administrator_cannot_create_a_feed(client: TestClient, store: MemMetadataDb) -> None:
    """Segregation of duties. The person who grants permissions being able to
    use them all is how segregation dies."""
    assert client.post("/api/feeds", json=FEED_BODY, headers=_as(ADMIN)).status_code == 403
    assert [e.action for e in _audit(store)] == ["denied:create_feed"]


def test_an_engineer_cannot_manage_users(client: TestClient, store: MemMetadataDb) -> None:
    assert client.get("/api/users", headers=_as(ENGINEER)).status_code == 403
    assert [e.action for e in _audit(store)] == ["denied:manage_users"]


# ── the user in no group ─────────────────────────────────────────────────────


def test_a_user_in_no_group_reaches_a_clear_answer_not_a_broken_app(
    client: TestClient,
) -> None:
    response = client.get("/api/me", headers=_as(NO_GROUP))
    assert response.status_code == 200
    body = response.json()
    assert body["has_access"] is False
    assert body["permitted_actions"] == []
    assert body["display_name"], "they are a real person; name them"


def test_a_user_in_no_group_is_refused_elsewhere_with_a_reason_they_can_act_on(
    client: TestClient, store: MemMetadataDb
) -> None:
    response = client.get("/api/feeds", headers=_as(NO_GROUP))
    assert response.status_code == 403
    assert "contact your administrator" in response.json()["detail"]
    assert [e.action for e in _audit(store)] == ["denied:view"]


# ── scope is not an oracle ───────────────────────────────────────────────────


def test_an_out_of_scope_feed_is_indistinguishable_from_one_that_does_not_exist(
    store: MemMetadataDb,
) -> None:
    """A 403 saying "out of scope" confirms the feed exists.

    Ask for every plausible id and the difference between 403 and 404 tells you
    which feeds are real — so both answers are the same sentence, and only the
    ledger knows which it was.
    """
    narrow = Principal(
        subject="narrow@cinqcare.test",
        display_name="Narrow Scope",
        roles=frozenset({Role.ENGINEER}),
        scopes=Scopes(feeds=frozenset({"some-other-feed"})),
    )
    app = create_app(
        authn=StaticAuthn({"narrow": narrow}),
        metadata_db=store,
    )
    with TestClient(app) as client:
        real = client.get(f"/api/feeds/{FEED_ID}", headers=_as("narrow"))
        imaginary = client.get("/api/feeds/no-such-feed-at-all", headers=_as("narrow"))

    assert real.status_code == imaginary.status_code == 404
    assert real.json() == imaginary.json()
    assert "out of scope" not in real.text

    # The ledger, however, knows exactly what happened.
    denials = [e for e in _audit(store) if e.action.startswith("denied:")]
    assert any("out of scope" in e.detail for e in denials)


def test_lists_are_filtered_where_they_are_built(store: MemMetadataDb) -> None:
    app = create_app(authn=StaticAuthn(), metadata_db=store)
    with TestClient(app) as client:
        client.post("/api/feeds", json=FEED_BODY, headers=_as(ENGINEER))

    narrow = Principal(
        subject="narrow@cinqcare.test",
        display_name="Narrow Scope",
        roles=frozenset({Role.ENGINEER}),
        scopes=Scopes(feeds=frozenset({"a-different-feed"})),
    )
    scoped = create_app(authn=StaticAuthn({"narrow": narrow}), metadata_db=store)
    with TestClient(scoped) as client:
        assert client.get("/api/feeds", headers=_as("narrow")).json() == []


# ── what an engineer may do, and what it leaves behind ───────────────────────


def test_creating_a_feed_writes_a_draft_and_an_audit_row(
    client: TestClient, store: MemMetadataDb
) -> None:
    response = client.post("/api/feeds", json=FEED_BODY, headers=_as(ENGINEER))
    assert response.status_code == 201
    body = response.json()
    assert body["version"] == 1
    assert body["lifecycle_state"] == "draft", "nothing arrives Published"
    assert body["citation_id"] == "feed:fidelis-downstate-roster@v1"
    assert body["route"].startswith("/data/intake/feed/")

    trail = _audit(store, object_id=FEED_ID)
    assert [e.action for e in trail] == ["create"]
    assert trail[0].actor.subject == ENGINEER


def test_editing_creates_a_new_version_and_leaves_the_previous_one_intact(
    client: TestClient, store: MemMetadataDb
) -> None:
    client.post("/api/feeds", json=FEED_BODY, headers=_as(ENGINEER))
    amended = dict(FEED_BODY, schedule_cron="0 7 * * 1")

    response = client.put(f"/api/feeds/{FEED_ID}", json=amended, headers=_as(ENGINEER))
    assert response.status_code == 200
    assert response.json()["version"] == 2

    versions = list(store.history(ObjectType.FEED, FEED_ID))
    assert [v.version for v in versions] == [1, 2]
    assert versions[0].body["schedule_cron"] == "0 6 * * 1", (
        "an edit is a new version; the previous one stays exactly as it was"
    )


def test_a_pinned_version_in_the_query_returns_that_version_not_the_latest(
    client: TestClient,
) -> None:
    """A `feed:<id>@v1` citation must open v1 — not silently show v2 under a
    v1 label, which is what a route that ignores `?version=` does."""
    client.post("/api/feeds", json=FEED_BODY, headers=_as(ENGINEER))
    amended = dict(FEED_BODY, schedule_cron="0 7 * * 1")
    client.put(f"/api/feeds/{FEED_ID}", json=amended, headers=_as(ENGINEER))

    pinned = client.get(f"/api/feeds/{FEED_ID}?version=1", headers=_as(ENGINEER))
    assert pinned.status_code == 200
    assert pinned.json()["version"] == 1
    assert pinned.json()["schedule_cron"] == "0 6 * * 1"
    assert pinned.json()["citation_id"] == f"feed:{FEED_ID}@v1"

    latest = client.get(f"/api/feeds/{FEED_ID}", headers=_as(ENGINEER))
    assert latest.json()["version"] == 2


def test_an_edit_may_not_rename_the_thing_it_edits(client: TestClient) -> None:
    client.post("/api/feeds", json=FEED_BODY, headers=_as(ENGINEER))
    renamed = dict(FEED_BODY, feed_id="something-else")
    response = client.put(f"/api/feeds/{FEED_ID}", json=renamed, headers=_as(ENGINEER))
    assert response.status_code == 400
    assert "create in disguise" in response.json()["detail"]


def test_a_pattern_that_does_not_match_its_sample_is_refused_before_save(
    client: TestClient, store: MemMetadataDb
) -> None:
    """Incident #1: a leading underscore nobody could see in a regex."""
    broken = dict(FEED_BODY, file_pattern=r"^CINQDOWNSTATE_Member_Roster_\d{8}\.xlsx$")
    response = client.post("/api/feeds", json=broken, headers=_as(ENGINEER))
    assert response.status_code == 422
    assert "_CINQDOWNSTATE_Member_Roster_20260801.xlsx" in response.json()["detail"]
    assert store.list(ObjectType.FEED) == ()


def test_every_feed_status_is_one_of_the_seven_words(client: TestClient) -> None:
    client.post("/api/feeds", json=FEED_BODY, headers=_as(ENGINEER))
    words = {feed["status"] for feed in client.get("/api/feeds", headers=_as(ENGINEER)).json()}
    assert words <= {w.value for w in StatusWord}
    assert len(StatusWord) == 7


# ── the ledger ───────────────────────────────────────────────────────────────


def test_the_api_offers_no_way_to_change_or_remove_an_audit_row(app: FastAPI) -> None:
    """Absent, not guarded.

        "audit is append-only; no deletion path exists for anyone"

    A permission check could be misconfigured. A route that does not exist
    cannot be.
    """
    offenders = [
        f"{method} {route.path}"
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
        if "audit" in route.path and method in MUTATING_METHODS
    ]
    assert offenders == []


def test_audit_is_readable_by_anyone_who_may_view(client: TestClient, store: MemMetadataDb) -> None:
    client.post("/api/feeds", json=FEED_BODY, headers=_as(ENGINEER))
    rows = client.get("/api/audit", headers=_as(READ_ONLY)).json()
    assert [row["action"] for row in rows] == ["create"]
    assert rows[0]["actor_type"] == ActorType.HUMAN.value


# ── the catalogue guarantees ─────────────────────────────────────────────────


def _permissions_on(route: APIRoute) -> set[Action]:
    found: set[Action] = set()
    stack = list(route.dependant.dependencies)
    while stack:
        dependency = stack.pop()
        action = declared_action(dependency.call)
        if action is not None:
            found.add(action)
        stack.extend(dependency.dependencies)
    return found


def _api_routes(app: FastAPI) -> list[APIRoute]:
    return [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api")
    ]


def test_every_api_route_states_the_permission_it_requires(app: FastAPI) -> None:
    """Not "every route we remembered". Every route, asserted over the catalogue.

    `/api/me` and `/api/navigation` are the deliberate exceptions: a user in no
    group must be able to learn that they are in no group, and must get an
    EMPTY nav rather than a broken shell. Both are authenticated — they just
    have no permission to require beyond having signed in.
    """
    unguarded = [
        route.path
        for route in _api_routes(app)
        if not _permissions_on(route) and route.path not in UNPERMISSIONED
    ]
    assert unguarded == []


#: Routes that use a mutating METHOD without being a mutation. `/api/ask` is a
#: POST only because a question travels in a body — it runs an R0 agent whose
#: whitelist contains read tools only, asserted in
#: tests/contract/test_pipeline_insight_agent.py. Listed explicitly, and short:
#: an exception nobody has to justify is a rule that erodes.
READ_ONLY_POSTS = frozenset({"/api/ask"})


def test_the_read_only_post_exceptions_stay_read_only(app: FastAPI) -> None:
    """Each exception must require a permission that does NOT change things.

    That is what keeps the list honest: adding a route here cannot smuggle in a
    write, because the permission it requires would have to be a write
    permission and this assertion would fail.
    """
    for route in _api_routes(app):
        if route.path not in READ_ONLY_POSTS:
            continue
        actions = _permissions_on(route)
        assert actions, f"{route.path} states no permission"
        assert not any(a.changes_things for a in actions), route.path


def test_every_mutating_route_requires_a_permission_that_changes_things(
    app: FastAPI,
) -> None:
    for route in _api_routes(app):
        if not route.methods & MUTATING_METHODS or route.path in READ_ONLY_POSTS:
            continue
        actions = _permissions_on(route)
        assert actions, f"{route.path} mutates and states no permission"
        assert all(a.changes_things for a in actions), (
            f"{route.path} mutates but only requires {sorted(a.value for a in actions)}"
        )


def test_the_unpermissioned_routes_still_refuse_an_anonymous_caller(
    client: TestClient,
) -> None:
    """ "No permission required" is not "no identity required"."""
    for path in sorted(UNPERMISSIONED):
        assert client.get(path).status_code == 401


def test_a_user_in_no_group_gets_an_empty_nav_not_a_broken_shell(
    client: TestClient,
) -> None:
    body = client.get("/api/navigation", headers=_as(NO_GROUP)).json()
    assert body["destinations"] == []
    assert body["active_wave"] == 0


def test_wave_one_destinations_are_absent_not_disabled(client: TestClient) -> None:
    """A greyed-out menu item is a promise the build cannot keep."""
    keys = {
        d["key"]
        for d in client.get("/api/navigation", headers=_as(ENGINEER)).json()["destinations"]
    }
    assert {"mapping", "quality", "work-queue", "lineage"} & keys == set()
    assert "intake" in keys and "control" in keys


def test_persona_ranks_but_never_gates(client: TestClient) -> None:
    """Everyone with the permission sees the same destinations in the same words."""
    engineer = client.get("/api/navigation", headers=_as(ENGINEER)).json()["destinations"]
    read_only = client.get("/api/navigation", headers=_as(READ_ONLY)).json()["destinations"]

    assert {d["key"] for d in engineer} == {d["key"] for d in read_only}
    labels = {d["key"]: d["label"] for d in engineer}
    assert all(labels[d["key"]] == d["label"] for d in read_only), "same words for everyone"
    assert [d["key"] for d in engineer] != [d["key"] for d in read_only], "different ranking"


def test_only_an_administrator_sees_users_and_roles(client: TestClient) -> None:
    admin = {
        d["key"] for d in client.get("/api/navigation", headers=_as(ADMIN)).json()["destinations"]
    }
    engineer = {
        d["key"]
        for d in client.get("/api/navigation", headers=_as(ENGINEER)).json()["destinations"]
    }
    assert "users" in admin
    assert "users" not in engineer
