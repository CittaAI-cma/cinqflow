# ADR-0023 · Delivery is a pin, not a route — `storage` stays write-free

**Status:** Accepted · **Date:** 2026-08-30 · **Governs:** ingestion, ports, CF-V1-E3-05

## Context

Plate 09 has named seven connectors since Wave 0 — `sftp-poller`, `api-puller`, `fhir-puller`,
`storage-event`, `db-extractor`, `upload-endpoint`, `stream-batcher`. Plate 04 gave none of them a
pin. The `storage` port has `list`, `fingerprint` and `move`, and deliberately **no write verb and
no delete verb**, so the platform could read a landing zone it had no way to fill.

The consequence was not theoretical. CF-V1-E4-01's onboarding wizard opens with *"1. Upload
sample"* — a step behind which no route existed. Every landing outcome, every fingerprint check
and every registry row was exercised only by files the simulator had already placed on disk. Of 65
Wave-1 backlog stories, none asked for inbound delivery: the gap was in the join between two
plates, which is exactly where nobody looks.

The cheap fix was three lines — add `write` to `storage` and post to it from the API.

## Decision

**Delivery gets its own pin.** `connector` is the twenty-first: `connect · list_available · fetch ·
deliver`, one Protocol, three fitted adapters (`mock`, `upload`, `folder-drop`), **one** contract
suite all three pass. `storage` gains nothing.

The reason is the separation itself. Adding `write` to `storage` would hand the profiler, the
pipeline runner and the explorer the ability to put files in the landing zone, and ADR-0011's
*"there is no second door"* would become a convention instead of a property. With the verb on its
own pin, only a connector delivers, only `DeliveryWorker` holds a connector, and that worker runs
`classify` on everything it lands. A caller cannot reorder those steps because a caller cannot
reach them.

**Push and pull are one port.** `upload-endpoint` is pushed at and lists nothing; `folder-drop`
polls and refuses to be pushed at. `deliver_available()` walks a pull connector's listing and lands
each file by the identical path an uploaded one takes, so a poller can be pointed at any connector
without asking which kind it is. `sftp-poller` later is replacing `os.scandir` with a client.

**Almost nothing is refused at the door.** An empty file, an unknown name and an oversized delivery
all *land*: registered, classified, parked, with a named check. Refusing early would delete the
evidence the control plane exists to keep. Two exceptions, both about the request rather than the
data: a filename that is a *path* — a caller choosing where the platform writes — and a checksum
that disagrees with the bytes, verified **before** the write because the storage pin has no delete
verb and damaged bytes could not be taken back out.

**The receipt is a landing decision, not "upload succeeded".** The bytes almost always arrive. The
receipt carries `key` (where the connector put it, always `incoming/`) *and* `landed_key` (where it
is, after landing moved it) — naming only the first told a person their accepted file was in
`incoming/` while it was in `processed/`.

**The insight on upload is arithmetic.** An accepted file is profiled by CF-V1-E5-01's
deterministic profiler — no model is called. That is what makes every fact on the next screen
citable, and what the schema-inference agent grounds on. A delivery needed no new citation kind: a
delivery **is** a file, and `CitationKind.FILE` has meant that since Wave 0.

**The browser may advise; it never gates.** The upload form reads the feed's pattern and says what
will happen, then sends anyway. A file stopped in the browser leaves no registry row, no parked
copy and no reason anybody can read — worse than a second door, not better.

## Consequences

- `core/delivery` is pure and owns the key layout, the filename rule and the checksum, so seven
  connectors cannot invent seven answers to "where does this go".
- `profiles/*.yaml` gain a `connector` pin; a deployment without one refuses at **503** naming the
  key to fit, rather than succeeding silently into nowhere.
- The conformance kit certifies 21 pins. `deliver_a_file_into_the_landing_zone` is asserted against
  every fitted adapter.
- Integration day stays a profile change: a real payer means pointing an adapter and re-running
  connector conformance — `connect, list, fetch, checksum_match, move, retry_etiquette`.
