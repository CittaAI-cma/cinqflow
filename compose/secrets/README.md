# compose/secrets

Docker secrets for the rung-1 twin, as FILES rather than inline environment
values — the same discipline the connection profile enforces with
`secret://name`. A password written into `docker-compose.yml` is a password in
git.

These files are gitignored. Create them before `docker compose up`:

```bash
for name in pg_password keycloak_password minio_password grafana_password; do
  openssl rand -base64 24 > "compose/secrets/$name"
done
```

The twin holds **no PHI** — development holds zero PHI by constraint
(ADR-0016), and every file it processes comes from the simulator.
