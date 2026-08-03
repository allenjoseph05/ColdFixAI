# S-0.5 — State reset spike

Is state reset reliable? Results in [`FINDINGS.md`](FINDINGS.md).

**Short answer: yes — but not by transaction rollback alone, and not verifiable
by the row-count check the story specifies.** Plain rollback failed all ten
cycles while passing every row count. Rollback plus an explicit sequence restore
is correct and 8.5× cheaper than a template copy.

Subject: `django-helpdesk` at `3a22901`, same dataset as S-0.4 so the two spikes'
numbers are directly comparable.

---

## Run it

```bash
cd spikes/S-0.5-reset
docker compose up -d

git clone https://github.com/django-helpdesk/django-helpdesk.git repo/django-helpdesk
git -C repo/django-helpdesk checkout 3a2290172ced5bcae9c211ad6ec23cfbc48dcc4e
```

The workbench needs a **version-matched** Postgres client. The Debian image ships
the v17 client and the server here is v16; `pg_dump` 17 emits
`SET transaction_timeout`, which v16 rejects, and `pg_restore` then reports
"errors ignored on restore". A reset that succeeds with ignored errors is not a
reset, so the client is pinned:

```bash
docker compose exec workbench bash -c '
  apt-get update -qq && apt-get install -y -qq curl ca-certificates gnupg
  install -d /usr/share/postgresql-common/pgdg
  curl -sS -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    https://www.postgresql.org/media/keys/ACCC4CF8.asc
  echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] \
https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
    > /etc/apt/sources.list.d/pgdg.list
  apt-get update -qq && apt-get install -y -qq postgresql-client-16'

docker compose exec workbench bash -c \
  'cd /repo/django-helpdesk && pip install -e . "psycopg[binary]"'
```

Then build the subject database:

```bash
export E='-e PYTHONPATH=/seeds:/harness -e DJANGO_SETTINGS_MODULE=spike_settings'
docker compose exec postgres psql -U coldfix_test -d postgres -c 'CREATE DATABASE spike_reset;'
docker compose exec $E workbench bash -c 'cd /repo/django-helpdesk && python manage.py migrate'
docker compose exec $E workbench bash -c 'cd /repo/django-helpdesk && python manage.py loaddata demodesk/fixtures/demo.json'
docker compose exec $E workbench bash -c 'cd /repo/django-helpdesk && python manage.py shell < /seeds/scale_helpdesk.py'
```

And run the experiment:

```bash
docker compose exec $E workbench bash -c 'cd /repo/django-helpdesk && python /harness/reset_experiment.py'
```

**On Windows Git Bash**, prefix `docker compose exec` with `MSYS_NO_PATHCONV=1`.

---

## What it does

Four reset strategies, ten cycles each. Every cycle: run a workload that writes,
reset, then fingerprint the database and compare against the baseline.

| Strategy | Mechanism |
|---|---|
| `rollback` | workload in a transaction, then `ROLLBACK` |
| `rollback+setval` | the same, then restore every sequence |
| `template` | `DROP DATABASE` + `CREATE DATABASE ... TEMPLATE snapshot` |
| `dump_restore` | `pg_restore --clean` from a `pg_dump` archive |

The **fingerprint** is the load-bearing part. It captures four things, not one:

- row count per table
- **`md5` of the whole table**, ordered by id — a row count cannot see an `UPDATE`
- `max(id)` per table
- `last_value` per sequence

The workload inserts, updates *and* deletes on purpose. A workload that only
inserted would let a reset which merely truncates look correct.

Three **leak probes** check the survivors the story's note names, each directly
rather than by inference.

---

## Three things that will bite anyone extending this

**Row counts cannot detect the failure this spike exists to find.** Plain
rollback keeps every row count, content hash and maximum id identical while
leaving sequences permanently advanced. Any verification built on counts alone
declares a broken reset healthy.

**Time the reset, not the workload.** The first version of this harness wrapped
`transaction.atomic()` in the timer for rollback while the other strategies
timed only their reset step, reporting 166.9 ms where the true figure is 0.4 ms.
That single inconsistency reversed the conclusion about whether repairing
rollback was worth it.

**Some state is not in the database.** An evaluated Django `QuerySet` holds its
rows in Python. No database-side strategy here touches it, and all four leave it
stale. A reset primitive promising a clean starting state has to cover the
process too.

---

## Scope

Spike scaffolding, not product code. Postgres-specific throughout — sequence
non-transactionality and `SET` transactionality are both Postgres behaviours and
neither transfers to MySQL. E2 builds the real `reset()`, inheriting the
four-part verification and the sequence-restore step from here.
