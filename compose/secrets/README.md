# compose/secrets

Docker secrets for the rung-1 twin, as FILES rather than inline environment
values — the same discipline the connection profile enforces with
`secret://name`. A password written into `docker-compose.yml` is a password in
git.

These files are gitignored, and they are no longer written by hand. `.env` is
the single source of truth for every environment variable in the project, so
the passwords live there and `compose/bootstrap-env.sh` PROJECTS them into this
directory in the shape a container wants:

```bash
make env          # or: ./compose/bootstrap-env.sh
```

`make up` runs it for you. Nothing here is generated — a value missing from
`.env` is an error rather than a fresh random password, because a generated
default is a password nobody knows.

The files written are `pg_platform_password`, `pg_data_password`,
`keycloak_password`, `minio_password` and `grafana_password`. Each is written
with **no trailing newline**: Postgres and MinIO read the file verbatim, and a
stray byte presents as bad credentials rather than as a stray byte.

The twin holds **no PHI** — development holds zero PHI by constraint
(ADR-0016), and every file it processes comes from the simulator.
