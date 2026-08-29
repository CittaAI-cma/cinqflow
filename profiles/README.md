# Connection profiles — where ALL environment difference lives

> "all environment difference lives in the connection profile, nowhere else"
> — `docs/architecture/INVARIANTS.md`, chip discipline

> "climbing a socket rung changes ONLY the profile; a forced core change is a
> ports-discipline defect"
> — `docs/architecture/plates/05-socket-ladder.md`

One file per socket. Climbing the ladder is editing which file you pass to
`--profile`, and running the conformance kit. Nothing else moves.

| Profile | Rung | Socket | Cost | Proves |
|---|---|---|---|---|
| `mock.yaml` | 0 | mock | $0 | the core's logic in CI, in seconds |
| `local.yaml` | 0.5 | Postgres plane | $0 | **the default dev plane** — real loads, real reconciliation |
| `ci.yaml` | 0.5 | Postgres plane | $0 | as `local`, with credential-free lanes and cost caps asserted |
| `twin.yaml` | 1 | local twin | $0 | the whole platform end to end — the demo |
| `dbx-free.yaml` | 2 | Databricks Free | $0 | the Databricks pins against real APIs, nightly |
| `client-dev.yaml` | 3 | client tenant | client | Entra, Key Vault, Foundry, private networking |
| `client-prod.yaml` | 4 | client tenant | client | nothing new, by design |

## Two rules that keep this honest

**Secrets are references, never values.** A profile carries `secret://name`.
Resolution is the secrets adapter's job — dotenv at rungs 0.5–1, Key Vault at
rung 3 — and the reference format never changes. `conformance/` fails a profile
containing anything credential-shaped.

**`mode` is a first-class field.** `full | propose_only | observe_only`.
A partial permission is a mode, not a failure: the conformance kit's AMBER
verdict sets `propose_only`, and every feature must behave correctly in it.
