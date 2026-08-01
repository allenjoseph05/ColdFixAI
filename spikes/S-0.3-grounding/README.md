# S-0.3 — Grounding spike

Scratch environment for the highest-risk assumption in the design: that an
unfamiliar Django repository can be made runnable, seeded, and driveable.

Record everything in [`FINDINGS.md`](FINDINGS.md). Read its *Recording
discipline* section before you start — the data is destroyed by logging
obstacles from memory afterwards.

---

## Start the database

```bash
cd spikes/S-0.3-grounding
docker compose up -d
docker compose ps          # wait for state: healthy
```

Three empty databases are created on first start — one per candidate repo:

| Repo | Database | URL |
|---|---|---|
| A | `spike_a` | `postgres://coldfix_test:coldfix_test@localhost:55432/spike_a` |
| B | `spike_b` | `postgres://coldfix_test:coldfix_test@localhost:55432/spike_b` |
| C | `spike_c` | `postgres://coldfix_test:coldfix_test@localhost:55432/spike_c` |

Port **55432**, not 5432. A local Postgres already on the default port would
give you a connection that succeeds against the wrong database — a much worse
failure than one that is refused.

## Point a repo at it

Most Django projects read `DATABASE_URL`, or take the components separately:

```bash
export DATABASE_URL="postgres://coldfix_test:coldfix_test@localhost:55432/spike_a"
# or
export POSTGRES_HOST=localhost POSTGRES_PORT=55432 \
       POSTGRES_DB=spike_a POSTGRES_USER=coldfix_test POSTGRES_PASSWORD=coldfix_test
```

If a project hardcodes its database settings with no environment override, **do
not patch it** — that is an obstacle to record. It is also a direct finding
about E7: the Explorer will hit the same wall, and something in the design has
to handle it.

## Reset one database

```bash
docker compose exec postgres psql -U coldfix_test -d coldfix_test \
  -c 'DROP DATABASE spike_a;' -c 'CREATE DATABASE spike_a;'
```

## Tear down

```bash
docker compose down        # keeps data
docker compose down -v     # wipes the volume; also required to re-run init-databases.sh
```

The init hook is skipped whenever the data volume already exists, so editing
`init-databases.sh` without `-v` silently does nothing.

---

## Scope

This is spike scaffolding, not product code. It is not sandboxed, has no
resource limits, and is exempt from the production guard that S-2.5 will add —
none of which is acceptable once E2 exists.

The credentials are deliberately obvious placeholders. When S-2.5 lands, its
test-pattern check must accept this URL shape, or the spike environment stops
working.
