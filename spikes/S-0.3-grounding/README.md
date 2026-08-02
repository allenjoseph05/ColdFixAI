# S-0.3 — Grounding spike

Scratch environment for the highest-risk assumption in the design: that an
unfamiliar Django repository can be made runnable, seeded, and driveable.

Record everything in [`FINDINGS.md`](FINDINGS.md). Read its *Recording
discipline* section before you start — the data is destroyed by logging
obstacles from memory afterwards.

---

## Start the environment

```bash
cd spikes/S-0.3-grounding
docker compose up -d
docker compose ps          # wait for postgres state: healthy
```

Two services come up: `postgres`, and `workbench` — a bare `python:3.12-slim`
box that the grounding is actually done from.

```bash
docker compose exec workbench bash
```

Clone candidate repositories into `repos/` on the host (gitignored); they appear
inside the workbench at `/repos`. Seed scripts live in `seeds/` and appear at
`/seeds`, read-only — they stay outside the checkout they seed, because
modifying the repository under test is the one thing this spike must not do.
That constraint is why `seeds/` holds two *settings* modules alongside the seed
script: both B and C hardcode configuration inside their trees, and overlaying
their settings from outside was the way to leave the checkouts untouched.

**If you drive this from Git Bash on Windows**, prefix `docker compose exec`
calls that pass POSIX paths with `MSYS_NO_PATHCONV=1`. Without it, `-e
PYTHONPATH=/seeds` arrives inside the container as
`C:/Program Files/Git/seeds`.

**Why a container and not the host.** Windows has no wheel for `pycurl` or
`mysqlclient`, so grounding natively invents compiler obstacles that the
Explorer — which E2 hands a Linux container — will never meet. Those obstacles
would land in the recurrence matrix looking real. **Why `-slim` and not a fat
image:** a base that already carries `gcc` and `libpq` would silently absorb the
missing-system-dependency obstacle class, which is one of the classes worth
counting. It is meant to be an inconvenient box.

### Redis, for candidate C only

```bash
docker compose --profile netbox up -d redis
```

Behind a profile deliberately. The default environment ships Postgres and
nothing else so that "this repo needs a service we do not have" gets *recorded*
rather than quietly accommodated — it is obstacle C-3, and it was written down
before this service existed.

### Reset the workbench between repositories

```bash
docker compose rm -sf workbench && docker compose up -d workbench
```

Do this every time. A container still holding repo A's `apt` packages grounds
repo B partly for free, and the spike then under-counts the exact thing it
exists to measure. Provisioning time is spike infrastructure — keep it out of
the per-repo wall clock.

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
