# The orchestration

Three environments, one `.env`, two Postgres planes.

```
make up ENV=local     the twin, bind-mounted, reloading      — a laptop
make up ENV=dev       the twin, built and closed down        — a shared box
make up ENV=prod      the app tier only                      — the client's tenant
```

`make help` lists every target. Never invoke `docker compose` directly: the
Makefile supplies `--project-directory .`, and without it compose looks for
`.env` in the wrong place and every `${VAR}` resolves empty.

## Why two databases

The platform is a chip. It has to be seatable in a tenant whose warehouse it
did not create, so the socket it keeps its **own** state in and the socket the
**client's** data lives in are two different things:

| plane | holds | pins fitted to it |
|---|---|---|
| `postgres-platform` | registry rows, control tables, the queue, the vector store | `metadata_db`, `control_tables`, `queue`, `vector`, `orchestration` |
| `postgres-data` | bronze, silver, gold | `catalog`, `sql_query`, and the medallion `LayerReader` |

The profile decides. A profile with no `data_plane:` section means *"they are
the same database"* — which is exactly rung 0.5, and why `profiles/local.yaml`
keeps working unchanged against the plane that already holds 568,867 Bronze
rows. `profiles/dev.yaml` and `profiles/prod.yaml` declare one, so
`api.local:build` opens a second connection and fits the medallion pins there.

Pointing production at Azure Database for PostgreSQL, Databricks or a client's
existing warehouse is one line in `profiles/prod.yaml` and one value in `.env`.
No code below the profile knows the difference.

## Where configuration lives

```
.env                      EVERY environment variable. The only one.
  CINQFLOW_SECRET_*         resolved by the `secrets` pin from `secret://…`
  everything else           read by compose to stand the environment up

profiles/{local,dev,prod}.yaml
                          WHICH adapter is fitted to which pin, per rung.

compose/docker-compose.yml            the app tier — true everywhere
compose/docker-compose.{env}.yml      what that environment brings with it
compose/secrets/*                     projected from .env by bootstrap-env.sh
```

`.env` holds **host-facing** DSNs, so running the backend straight on the
laptop still works. The local and dev overlays override those same two
variables with service-name DSNs for the containerized backend. One source of
truth, two vantage points onto the same databases.

Passwords reach containers as **files** (`/run/secrets/…`), never as
`environment:` values — an environment value is visible to `docker inspect` and
to every process in the container. `compose/bootstrap-env.sh` projects them
from `.env`; `make up` runs it for you.

## Prod brings nothing

`docker-compose.prod.yml` has no database, no object store, no identity
provider and no collector. That absence is the deliverable. Everything is a
managed endpoint supplied through `.env`, the images are built by CI and
pinned to an immutable tag (`CINQFLOW_IMAGE_TAG` is required, and `latest`
is not a tag), and both services bind to loopback so the tenant's own edge is
what faces the internet.

## First run

```bash
cp .env.example .env      # fill in what you have
make up ENV=local
make install ENV=local    # creates the schemas; reads no data
make ps
```

`make down` stops an environment and keeps its volumes. `make nuke` does not.
The platform volume is pinned by name (`cinqflow-twin_twin-pg`) precisely so a
project rename or an overlay switch cannot quietly create an empty one beside
it and make the data look gone.
