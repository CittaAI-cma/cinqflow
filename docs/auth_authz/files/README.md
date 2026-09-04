# Auth system — RBAC on top of your existing Postgres

JWT access+refresh tokens in httpOnly cookies, roles and granular
permissions layered onto your existing `users` table with **one
additive migration** — nothing existing gets altered or moved.

## How it fits together

```
users (yours, untouched)
  └─ user_roles ──> roles ──> role_permissions ──> permissions
```

A user can hold multiple roles; a role bundles permissions. Checks
happen against the flattened permission set, not the role name
directly — so `require_permission("candidates:write")` stays valid
even if you reshuffle which roles grant it later.

## Wire it into your existing FastAPI app

1. Drop `backend/core`, `backend/db/models.py`, `backend/schemas`,
   `backend/api` into your app (or the equivalent paths you already use).
2. **Delete `db/existing_models.py`** and point the two imports in
   `api/auth.py` and `api/deps.py` at your real `User` model.
   It only needs `id`, `email`, `hashed_password`, `is_active`.
3. In your existing `main.py`:
   ```python
   from api.auth import router as auth_router
   app.include_router(auth_router)
   ```
4. Set env vars: `JWT_SECRET`, `DATABASE_URL` (reuse what you already have).
5. Copy `migrations/versions/0001_add_rbac.py` into your alembic
   `versions/`, set `down_revision` to your current head, run
   `alembic upgrade head`.

If `users.id` isn't a UUID, change `USER_ID_TYPE` in `db/models.py`
and the FK type in the migration to match (e.g. `Integer`).

## Seed a first role

```sql
INSERT INTO roles (id, name) VALUES (gen_random_uuid(), 'admin');
INSERT INTO permissions (id, code) VALUES (gen_random_uuid(), 'users:write');
INSERT INTO role_permissions (role_id, permission_id)
  SELECT r.id, p.id FROM roles r, permissions p
  WHERE r.name = 'admin' AND p.code = 'users:write';
INSERT INTO user_roles (id, user_id, role_id)
  SELECT gen_random_uuid(), '<existing-user-id>', r.id
  FROM roles r WHERE r.name = 'admin';
```

Keep permission codes as `resource:action` (`candidates:read`,
`billing:write`) — it reads clearly in `require_permission(...)`
calls and scales without a schema change as you add resources.

## Gate an endpoint

```python
from api.deps import require_permission

@router.post("/candidates")
def create_candidate(user = Depends(require_permission("candidates:write"))):
    ...
```

## Wire it into your React app

1. Wrap your app: `<AuthProvider><App /></AuthProvider>` (needs `react-router-dom`).
2. Set `VITE_API_BASE` if your API isn't proxied at `/api`.
3. Gate routes:
   ```tsx
   <Route path="/admin" element={
     <ProtectedRoute permission="users:write"><AdminPage /></ProtectedRoute>
   } />
   ```
4. Anywhere in the tree: `const { user, hasPermission } = useAuth()`.

`LoginForm` and the Tailwind classes on it are scaffolding, not a
design system — swap in your existing components/styles.

## Notes on the choices made

- **Cookies, not localStorage**, for tokens — httpOnly means an XSS
  can't read them. `secure=True` requires HTTPS; drop it for local
  http dev.
- **Access token 15 min / refresh 7 days** — adjust in `core/config.py`.
  The frontend `api()` helper auto-refreshes once on a 401 and retries.
- Nothing here touches your existing `users` table's columns except
  the optional commented-out `hashed_password` line in the migration —
  uncomment only if that column doesn't already exist.
