# Auth & User Management — Analysis and Implementation Plan

Status: Phase 1 implemented (login + roles + admin-provisioned users).
Source material: `docs/auth_authz/` (reference scaffold), `docs/01_requirements_and_objectives/MVP_objective.docx` Epic 2.

## 1. What the reference scaffold gets right, and where it doesn't fit

`docs/auth_authz/files/` is a generic FastAPI+React RBAC starter, useful for its *shape*
(users → user_roles → roles → role_permissions → permissions; JWT access+refresh pair in
httpOnly cookies; refresh-once-on-401 on the client) but not directly usable — none of its
scaffolding matches how this codebase is actually built:

| Reference assumes | cinqflow actually is |
|---|---|
| SQLAlchemy ORM + Alembic migrations | raw psycopg3 SQL; idempotent `CREATE TABLE IF NOT EXISTS` DDL installed by `cinqflow install` (`workflow/ddl.py`'s own pattern) |
| Vite SPA + `react-router-dom`, a `<ProtectedRoute>` component | Next.js 15 App Router — server components, middleware, Server Actions |
| Router mounted on a bare FastAPI app | routers are `build_router(settings, get_conn)` factories composed in `api/app.py` |
| `passlib` + `python-jose` | not in `pyproject.toml`; this plan adds `bcrypt` + `PyJWT` directly instead — smaller surface, matches `db.py`'s own stated preference ("Deliberately not an ORM: every statement is visible SQL") |
| Cookies set by the API itself | frontend and backend are separately deployed Railway services on different origins (`lib/api.ts`'s `CINQFLOW_API` vs `NEXT_PUBLIC_CINQFLOW_API` split documents this already) — a cross-origin httpOnly cookie would need `SameSite=None; Secure`, which breaks plain-http local dev for no benefit |

So the scaffold was mined for ideas, not copied. Everything below is written against cinqflow's
actual conventions.

## 2. Roles (MVP_objective.docx, Epic 2 — "User, Role and Security Management")

Seeded idempotently at `cinqflow install` time:

| Role | slug |
|---|---|
| Business Analyst | `business_analyst` |
| Data Steward | `data_steward` |
| Data Engineer | `data_engineer` |
| Operations | `operations` |
| Approver | `approver` |
| Administrator | `administrator` |
| Read-Only User | `read_only` |

Epic 2 also asks for source/feed/domain/environment-level and row/column-level permissions, PHI
masking gated by permission, and a fine-grained `permissions` table. That is real scope but not
buildable sanely without a resource model this MVP hasn't designed yet — Phase 1 ships **role
membership only** (`require_role("administrator")`, same shape the reference scaffold uses), and
defers a `permissions`/`role_permissions` table to Phase 2 (§5) rather than freezing a schema
around a guess.

## 3. Key decisions

- **No self-registration.** Matches the ask directly, and Epic 2's governance posture — accounts
  are created by an administrator (email + a password they set), never requested.
- **Password auth now, Entra ID SSO later.** Epic 2 leads with SSO; the immediate ask is
  email+password. `auth.user.hashed_password` is nullable so an SSO identity can be layered on
  later without a breaking migration.
- **Stateless JWT, access (15 min) + refresh (7 day) pair** — same numbers as the reference
  scaffold, adjustable via settings.
- **Backend never touches cookies.** FastAPI issues/validates tokens over JSON and
  `Authorization: Bearer` only. Next.js Server Actions call it server-to-server (the established
  pattern — see `app/actions.ts`) and Next.js is the *only* thing that ever holds a bearer token,
  stored as its own httpOnly, `SameSite=Lax` cookie on its own origin. The browser never talks to
  the API directly for anything authenticated.
- **A DB round-trip on every authenticated request**, not signature-only trust — `get_current_user`
  re-reads the user row so a deactivated account (Epic 2: "user activation and deactivation")
  loses access immediately instead of riding out a 15-minute access token. Same call the reference
  scaffold makes.
- **A new `auth` schema**, not reusing `workflow` — mirrors the existing separation
  (workflow/jobq/silver/bronze each own a schema for a reason); auth data has a different
  lifecycle and blast radius than pipeline data.
- **Deactivate, never delete** a user — consistent with this codebase's append-only stance
  elsewhere (Bronze's append-only guard, upload delete's `preserved_batches`).

## 4. What was built (Phase 1)

Backend (`backend/src/cinqflow/auth/`):
- `ddl.py` — `auth.role`, `auth.user`, `auth.user_role`, installed idempotently from
  `cinqflow install`; seeds the 7 roles above.
- `security.py` — `hash_password`/`verify_password` (bcrypt), `create_access_token`/
  `create_refresh_token`/`decode_token` (PyJWT, HS256).
- `models.py` — `CurrentUser`, `UserOut`, `Role`.
- `store.py` — `AuthStore`: `create_user`, `get_user_by_email`, `get_user_by_id`,
  `list_users`, `list_roles`, `set_active`.

API (`backend/src/cinqflow/api/routers/`):
- `auth.py` — `POST /api/auth/login`, `POST /api/auth/refresh`, `GET /api/auth/me`.
- `users.py` — `GET /api/roles`, `POST /api/users`, `GET /api/users`,
  `PATCH /api/users/{id}` (all administrator-only).
- `deps.py` — `get_current_user` (Bearer), `require_role(name)`.

Frontend:
- `middleware.ts` — session-cookie presence gate on every route except `/login` (UX-level; the
  real boundary is the API and each server component's own `requireUser`/`requireRole` call).
- `lib/auth.ts` — server-only: `getCurrentUser`, `requireUser`, `requireRole`, `authFetch`
  (attaches the bearer token, refreshes once on 401 — the same retry-once shape as the reference
  scaffold's `AuthContext`).
- `app/login/` — page, form, and the `login` Server Action that sets the session cookie.
- `app/admin/users/` — list (via the existing `DataTable`) + a route-driven "create user" modal
  (same pattern as `AddIngestionModal`), administrator-only.
- `TopBar`/`Sidebar`/`layout.tsx` — real signed-in user, working sign-out, and an Admin nav
  section, replacing the "no auth on this build" placeholders that were already flagged in
  `appConfig.ts` and `navigation.ts`.

## 5. Explicitly out of scope for Phase 1 (flagged, not silently dropped)

1. **The existing routers stay open.** Uploads, batches, mappings, worklist, etc. are not yet
   behind `require_user`. A login-gated frontend does not mean a login-gated API — this is the
   single biggest follow-up and should land before this is exposed outside the team.
2. Fine-grained `permissions`/`role_permissions` table (source/feed/domain/environment/row/
   column-level; PHI masking gated by permission instead of universally applied).
3. Session/token revocation — logout only clears the frontend cookie; a leaked access token is
   valid until it expires (≤15 min by default).
4. Entra ID / SSO.
5. Audit log of user actions, periodic access review/certification, emergency access with
   enhanced auditing.
6. Self-service "forgot password" (no email sending in this build).

## 6. TODOs

**Phase 1 — done this session**
1. [x] Backend settings + deps (`bcrypt`, `PyJWT`)
2. [x] `auth/ddl.py`, `security.py`, `models.py`, `store.py`
3. [x] `routers/auth.py`, `routers/users.py`; `deps.py`; wired into `api/app.py`
4. [x] `cli.py` — `install` seeds roles + an optional bootstrap admin from env vars
5. [x] Backend tests — login, bad password, deactivated user, admin-only enforcement
6. [x] Frontend `lib/auth.ts`, `middleware.ts`
7. [x] `/login` page + form + action
8. [x] `/admin/users` list + create-user modal + action
9. [x] TopBar/Sidebar/layout wiring
10. [x] `compose/.env.example` + README note for the new env vars

**Phase 2 — next**
11. [ ] `require_user`/`require_role` on every existing router, per the role list in §2
12. [ ] `permissions`/`role_permissions` table + `require_permission`, replacing role-name checks
13. [ ] PHI masking gated by permission (ties into the `phi_masked` fields uploads/batches
    already return)
14. [ ] Refresh-token rotation with server-side revocation (a `auth.revoked_token` table)

**Phase 3 — later**
15. [ ] Entra ID SSO
16. [ ] Audit log, periodic access review/certification, emergency access
