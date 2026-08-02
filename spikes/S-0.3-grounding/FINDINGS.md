# S-0.3 — Can we ground real repositories?

**Status:** **complete — 3 of 3 grounded, verdict is proceed**
**Run by:** Claude Opus 5, driving a Linux container (see *Where these runs happened*)
**Dates:** candidates selected 2026-08-02; grounding runs 2026-08-02
**Timebox:** ~1 day

---

## Where these runs happened

All three runs are done from `python:3.12-slim` on the spike's Docker network
(the `workbench` service), not from the Windows host the spike was driven from.

This was a deliberate choice and it bounds every number below. `requirements.txt`
for candidate A pins `pycurl` and `mysqlclient`, neither of which has a Windows
wheel; grounding natively would have produced two compiler obstacles on the
first repo. Those are real friction for a human on Windows, but they are not
friction the Explorer will ever meet — E2 hands it a Linux container — so they
would have entered the recurrence matrix looking like recurrent obstacles while
actually being facts about one laptop.

The image is `-slim` rather than a fat build image for the opposite reason: a
container that already has `gcc`, `libpq` and friends would silently absorb the
missing-system-dependency obstacle class, which is one of the classes worth
counting. Obstacle A-1 below is exactly that class, and a fatter base image
would have hidden it.

**The workbench is recreated between repositories.** A container still carrying
repo A's `apt` packages grounds repo B partly for free, and the spike would
under-count the thing it exists to measure. Provisioning the workbench itself is
spike infrastructure and is excluded from the per-repo wall clock.

---

## What this spike decides

Whether an agent can plausibly take an unfamiliar Django repository and turn it
into something runnable, seeded, and driveable — by finding out how hard it is
*by hand* first.

**Decision rule, from the backlog:** if two of three repositories fail,
reconsider the target framework or the workload-discovery approach **before**
building E7.

**The output that matters is the recurring-obstacle list**, not the pass count.
An obstacle hit in two of three repos becomes a playbook entry (S-13.1) or a
required Explorer tool. One-off obstacles are noise. Separating those is the
whole point of doing this manually.

---

## Recording discipline

Read this before starting, because the data is destroyed by doing it wrong.

- **Log obstacles as they happen, not afterwards.** Reconstructed friction is
  systematically under-reported — you forget the twenty minutes lost to a
  missing environment variable once you have found it.
- **Start a stopwatch and record wall-clock, including the dead ends.** Time
  spent on a wrong path is the measurement, not an error in it.
- **Do not fix the repository.** If it needs a code change to run, that is an
  obstacle to record, not a task to complete.
- **Do not consult a previous repo's notes while working the next one.** That
  contaminates the recurrence data — you want to discover the same obstacle
  three times independently, which is what makes it recurrent.
- **Record failure honestly.** A repo you could not ground is the single most
  valuable data point here. Never quietly swap it for an easier one.

---

## Candidate selection

Record why each was chosen, and choose before you start — not after seeing
which ones cooperate.

Selected 2026-08-02, before any grounding was attempted. Metadata read from the
GitHub API on that date.

| # | Repository | URL | Stars | Last commit | Why chosen |
|---|---|---|---|---|---|
| A | `healthchecks` | https://github.com/healthchecks/healthchecks | 10.2k | 2026-07-31 | The favourable end of the range: standalone project, `manage.py` at the root, 124 migrations, real models and a documented REST API. Postgres sits behind `DB=postgres` plus separate `DB_HOST`/`DB_USER`/`DB_NAME` vars rather than `DATABASE_URL`, so it exercises the "database config externalized?" question without being hardcoded. **If this one cannot be grounded, the assumption is in trouble regardless of B and C.** |
| B | `django-helpdesk` | https://github.com/django-helpdesk/django-helpdesk | 1.7k | 2026-07-31 | Reusable-app-plus-demo-project shape, which is very common in the Django ecosystem and is a *different* grounding problem: the runnable project is `demodesk/`, not the repo root, and nothing at the top level says so. 40 migrations, a shipped `emailtemplate.json` fixture and a `demodesk/fixtures/` directory — so it tests fixture *discovery* (S-7.5) rather than synthesis (S-7.6). Middle of the range on size. |
| C | `netbox` | https://github.com/netbox-community/netbox | 21.2k | 2026-07-31 | Mandates Postgres with no SQLite fallback *and* requires Redis, so it tests the "requires an external service" row against a spike environment that deliberately provides only Postgres. Large DCIM model layer and a full REST API — the closest of the three to a repo someone would actually point this tool at, and therefore the strongest S-0.6 candidate. See the caveat below. |

**Caveat on C.** NetBox ships `CLAUDE.md` and `AGENTS.md` at its repo root.
Irrelevant to a manual grounding, but it means any conclusion drawn from this
repo about E7 is *optimistic* — the Explorer would receive orientation that a
typical repository does not provide. Record it in the cross-repo analysis rather
than letting it silently inflate the result.

**Selection constraints:** not a tutorial, not a boilerplate template, not a
project whose README is a deployment guide for a hosted service. It needs real
models, real endpoints, and migrations in the tree.

**Known bias in this sample.** All three were committed to within three days of
selection. Nothing here represents an abandoned repository with stale pinned
dependencies against a Python version that no longer builds — a realistic case,
and probably a harder one. Treat the verdict as bounded to maintained projects.

**Rejected candidates and why** (keep this — it documents the sampling bias):

- **`cookiecutter/cookiecutter-django`** (13.6k) — boilerplate generator, excluded by the constraints.
- **`mdn/django-locallibrary-tutorial`** (1.7k), **`codingforentrepreneurs/Try-Django`** (1.3k), **`HaddyYang/django2.0-course`** (1.1k) — tutorials.
- **`Shopify/shopify_django_app`** (507), **`nickjj/docker-django-example`** (1.5k) — starter templates with no real domain models.
- **`encode/django-rest-framework`**, **`jazzband/django-taggit`**, **`carltongibson/django-filter`**, **`django-commons/django-debug-toolbar`**, and the rest of the library tier — libraries, not runnable projects. These dominate a naive GitHub search for "django" and are the main reason candidate selection took real effort.
- **`mozilla/addons-server`** (899) — real production Django and genuinely attractive, but a 2.6 GB checkout requiring Elasticsearch, RabbitMQ and Celery. Rejected on **timebox, not on suitability**; it would have consumed the whole day on one repo. Worth revisiting for S-17.3.
- **`saleor/saleor`** (23.2k) — real, but its API surface is GraphQL-only, so "find a candidate endpoint" would not be representative of the Django view/DRF shape the first adapter targets.
- **`django-oscar/django-oscar`** (6.6k) — same reusable-app-plus-sandbox shape as B. Picking both would spend two of three slots on one shape.
- **`DefectDojo/django-DefectDojo`** (4.9k) — 384 MB and predominantly HTML by line count; the Django surface is a minority of the tree.

**Reserves**, in order, if one of A/B/C has to be abandoned. Recorded now so the
replacement is not chosen after seeing which repos cooperate:

1. **`taigaio/taiga-back`** (844) — configured by copying `config.py.dev.example` rather than by environment variables, so it would exercise the "database config not externalized" row that none of A/B/C hits.
2. **`liangliangyy/DjangoBlog`** (7.4k) — real standalone project; primary documentation is in Chinese, which is itself an obstacle category worth measuring.
3. **`django/djangoproject.com`** (2.0k) — real production site with a shipped `docker-compose.yml`.

---

## Per-repository record

One block per repository, pre-filled with the selected names.

### Repository A — `healthchecks`

Pinned at `5086d28` ("Fix ruff warnings", committed 2026-07-31; shallow clone of
`master` taken 2026-08-02).

**Wall clock:** start `07:20:54Z` → end `07:28:52Z` = **8 min**
**Outcome:** **grounded**
**Where it stopped** (if not grounded): n/a

#### Stage log

Mark each stage and the time it took. `n/a` is a finding too.

| Stage | Time | Notes |
|---|---|---|
| Clone and read the README | 4 s clone | README states the stack and the dev setup in full. Postgres config is `DB=postgres` plus `DB_HOST`/`DB_PORT`/`DB_NAME`/`DB_USER`/`DB_PASSWORD`, read at `hc/settings.py:207-235`, SQLite by default. |
| Resolve dependencies / build environment | 23 s | `pip install -r requirements.txt`, everything from wheels, **zero `apt` packages**. The README's `apt install gcc python3-dev libpq-dev libcurl4-openssl-dev libssl-dev` step was not needed — `pycurl==7.47.0` had a manylinux wheel. See finding A-i. |
| Configure settings (env vars, secrets, `settings.py`) | ~0 | Five env vars, no `SECRET_KEY` needed — `settings.py` defaults it. No `local_settings.py` required. Nothing blocked. |
| Connect to Postgres | 30 s | **Obstacle A-1.** Failed on missing `libpq`. |
| Run migrations | 25 s | 124 migrations, clean on an empty database, 23 tables. No failures. |
| Create a user / resolve auth | (in seed) | `createsuperuser` never needed — the REST API authenticates on a per-project `X-Api-Key`, so a plain `User` + `Profile` + `Project` is enough. Discovering *that* is obstacle A-3. |
| Seed data | 4 s | Synthesized: 1 user, 1 project, 50 checks, 1000 pings. Correct on the first attempt. |
| Find a candidate endpoint | ~1 min | `hc/api/urls.py` lists routes plainly; `/api/v3/checks/` was the obvious list endpoint. |
| Get real data out of that endpoint | 1 min | **Obstacle A-2** (readiness), then HTTP 200 with seeded rows. |

#### Fixtures — did they already exist?

This determines whether S-7.5 (fixture *discovery*) or S-7.6 (fixture
*synthesis*) carries the weight. Synthesis is far more expensive to build.

- [ ] `factory_boy` factories present
- [x] pytest fixtures that create model instances — **only in this weakened sense:** `hc/test.py::BaseTestCase.setUp` is a Django `TestCase`, not a pytest fixture, and nothing outside the test suite can call it. It is not loadable, but it is *readable*, and it was the source of the seed recipe.
- [ ] a seed / demo-data management command — 15 management commands exist, all operational (`pruneusers`, `sendalerts`, `sendreports`, …). None seeds.
- [ ] Django fixture files (`.json` / `.yaml`) — no `fixtures/` directory anywhere in the tree.
- [ ] a `docker-compose` with a seeded database — `docker/docker-compose.yml` exists but builds the app against an empty database.
- [x] **none — data had to be synthesized by hand**

Notes: this is the finding that matters most from repo A, and it splits S-7.5
from S-7.6 in an unexpected place. Nothing here is *loadable* — there is no
`loaddata` target and no factory to call — so on the S-7.5/S-7.6 boundary as
written, this repo is pure synthesis. But the object graph was not guessed
either. `hc/test.py` states that a usable account is `User` + `Profile` +
`Project`, that the API key is a plain 32-char column on `Project`, and that the
key used throughout the suite is `"X" * 32`. Without reading it, the two
non-obvious facts — that a `Profile` row is required, and that auth is a project
column rather than a user token — would have cost far more than the four minutes
this repo's seeding took.

So the useful primitive is not "find a fixture file." It is **"find the place
the test suite constructs a minimal valid object graph, and read it as a
recipe."** That is a *third* thing, cheaper than synthesis-from-models and more
widely available than `loaddata` fixtures. Whether it generalizes is a question
for B and C, not something to conclude from one repo.

#### Evidence the endpoint did real work

The AC says "an endpoint that returns real data." Prove it rather than assert
it — this is the same discipline S-7.8 later enforces on the Explorer, where
`work_verified` is computed by the harness because the agent is incentivized to
claim success.

| | value |
|---|---|
| Endpoint | `GET /api/v3/checks/`, header `X-Api-Key: XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX` |
| Rows seeded | 1 user, 1 project, 50 checks, 1000 pings |
| Response size (bytes) | 51 352 |
| Response time | 58.2 ms (warm; measured after one discarded request) |
| Did the response contain seeded values? | **Yes** — HTTP 200, `checks[]` length 50, and the literal `seeded-check-000` with its synthesized `desc` and `n_pings: 20` present in the body |

#### Obstacles

One row per distinct obstacle. Be specific — "config" is not an obstacle,
"`SECRET_KEY` read from an env var with no `.env.example`" is.

| # | Obstacle | Category | Time lost | How resolved | Discoverable from the repo alone? |
|---|---|---|---|---|---|
| A-1 | `requirements.txt` pins `psycopg==3.3.4` — the source distribution, not `psycopg[binary]` — so it imports only if `libpq` is already on the system. Django catches the `ImportError` and re-raises it as `ImproperlyConfigured("Error loading psycopg2 or psycopg module")`, which names two Python modules that are both installed and does not mention `libpq`. The real message, `libpq library not found`, is only visible by importing `psycopg` directly, outside Django. | Missing system library, **masked by a misleading error** | ~30 s | `apt-get install libpq5` in the container. Note `libpq5`, the runtime library — the README's `libpq-dev` is the header package, needed only to *compile*, which nothing here did. | **Partly, and the gap is the interesting part.** The README's Debian block does list `libpq-dev`, so the fact is in the repo. But the error text points away from it, and the connection between "Error loading psycopg module" and an `apt` line in a *development-setup* section is not one the failure output supports. Recovering it needed a diagnostic step (import the module outside Django) that no error message suggested. |
| A-2 | `manage.py runserver` takes >6 s to bind port 8000. A request at 6 s got `ECONNREFUSED`, and the server log at that moment contained only system-check warnings with no "Starting development server" line — so the log could not distinguish *still booting* from *died during startup*. The process was in fact alive and bound moments later. | Readiness not observable | ~1 min | Polled the port with `connect_ex` instead of trusting a fixed sleep or the log contents. | Yes, but only by polling. No file, log line, or health endpoint in the repo reports readiness. |
| A-3 | No fixture, factory, or seed command exists (see above). The object graph also has two non-obvious requirements: a `Profile` row must exist for the owner, and API auth is a 32-char `api_key` column on `Project` rather than a user-level token — so a correctly-created `User` alone authenticates nothing. | No fixtures / non-obvious object graph | ~4 min | Read `hc/test.py::BaseTestCase.setUp` and transcribed its object graph into `seeds/seed_a.py`. Worked first attempt. | **Yes** — but only from the *test suite*, not from the models, the README, or the admin. An Explorer that reads only application code would have had to infer the `Profile` requirement from a runtime failure. |
| A-4 | The repository root has no `.env.example`. One exists at `docker/.env.example`, i.e. inside a subdirectory documenting a *different* deployment path, and nothing at the root points to it. | Config discoverability | ~0 (found while reading `docker/`) | Read `hc/settings.py` directly instead. | Yes — `settings.py` is authoritative and readable. Cost nothing here, but only because the settings file is unusually clean. |

That last column is the one that matters for E7. If a human could only resolve
it by searching the web, reading a mailing list, or guessing, then an agent with
`shell` and `read_file` cannot resolve it either — and the design needs to
account for that.

**Nothing here required leaving the repository.** No web search, no mailing
list, no guessing. That is the single most encouraging result of run A, and it
is the claim to test hardest against B and C.

#### Non-obstacles worth recording

Absences are data. These were expected to cost time and did not:

- **The documented build-dependency step was unnecessary.** The README's `apt`
  line is written for a world where `pycurl` and `psycopg` compile from source.
  Both now ship wheels, and following the README would have installed a compiler
  toolchain nothing used. *Documented setup steps over-state what is required* —
  which cuts against an Explorer that treats a README as a checklist, and means
  attempting the naive path first is the cheaper strategy.
- **No `SECRET_KEY` obstacle.** `settings.py` defaults it. A very common Django
  grounding blocker, absent here.
- **Migrations were clean on an empty database.** 124 of them, no manual
  ordering, no data migration that assumed existing rows.

#### Consequence for S-0.6 — A is a poor development target

`GET /api/v3/checks/` serves 50 checks in **3 queries**: one for the project,
one for the checks, one prefetch for channels. It is already correctly
optimized, and a spot check found no N+1 to plant a story against.

That makes `healthchecks` a *bad* fit for the S-0.6 development target, which
needs "at least one known performance defect documented with its expected
measurement signature." It makes it a good **holdout** — a repository where the
correct answer is a null result. Given that "never manufacture a finding" is a
project invariant, a holdout that *should* produce nothing is worth more than
another repo with a planted defect. Recorded here; the decision belongs to S-0.6.

---

### Repository B — `django-helpdesk`

Pinned at `3a22901` (merge of PR #1362, committed 2026-07-31; shallow clone of
`main` taken 2026-08-02).

**Wall clock:** start `07:35:58Z` → end `07:40:54Z` = **5 min**
**Outcome:** **grounded**
**Where it stopped** (if not grounded): n/a

#### Stage log

| Stage | Time | Notes |
|---|---|---|
| Clone and read the README | ~1 min | **The selection-time assumption about this repo was wrong** — see the correction below. |
| Resolve dependencies / build environment | 39 s | `pip install -e .`. All dependencies float (`django>=4.2`, the rest unpinned) and resolved to Django 6.0.7, the newest release. Nothing broke, but nothing was pinned either — see finding B-ii. |
| Configure settings (env vars, secrets, `settings.py`) | ~2 min | **Obstacle B-1**, the significant one on this repo. |
| Connect to Postgres | ~30 s | **Obstacle B-2** — no driver is declared anywhere. |
| Run migrations | 10 s | 39 helpdesk migrations plus contrib, clean on an empty database. |
| Create a user / resolve auth | ~1 min | Not created — the fixture ships a superuser. Recovering its password is **obstacle B-3**. |
| Seed data | 3 s | `loaddata demodesk/fixtures/demo.json`, 18 objects, **first attempt, no edits**. |
| Find a candidate endpoint | ~1 min | DRF router at `src/helpdesk/urls.py:225-235`, gated on `HELPDESK_API_ENABLED`, which defaults to `True` (`src/helpdesk/settings.py:534`). `/api/tickets/` was the obvious list route. |
| Get real data out of that endpoint | ~30 s | HTTP 200 with fixture rows, first attempt. |

#### Correction to the selection note

The candidate table above claims "the runnable project is `demodesk/`, not the
repo root, and nothing at the top level says so." **That is wrong, and it was
wrong when written.** The repo root has a `manage.py` whose second line reads
`os.environ.setdefault("DJANGO_SETTINGS_MODULE", "demodesk.config.settings")`.
The project structure announces itself in the most conventional place possible.

Recorded rather than quietly fixed, because the error is itself a data point:
the shape of a repository was mis-predicted from its *file listing* without
opening the entry point. That is precisely the failure mode an Explorer running
on a directory tree would have, and it argues for reading `manage.py` first as a
cheap, high-information move rather than inferring layout from directory names.

#### Fixtures — did they already exist?

- [ ] `factory_boy` factories present — **declared but not what it looks like.** `factory-boy` is in the `test` dependency group and `tests/utils.py` does `import factory`, but every use is `factory.Faker(...).evaluate(...)` for random text. There is no `DjangoModelFactory` anywhere in the tree, so nothing constructs a model. A dependency-manifest scan would report factories here and be wrong.
- [ ] pytest fixtures that create model instances
- [ ] a seed / demo-data management command — five management commands exist (`create_queue_permissions`, `escalate_tickets`, `get_email`, …), all operational, none seeding.
- [x] **Django fixture files (`.json` / `.yaml`)** — `demodesk/fixtures/demo.json` (18 objects) and `src/helpdesk/fixtures/emailtemplate.json`.
- [ ] a `docker-compose` with a seeded database
- [ ] none — data had to be synthesized by hand

Notes: **this is the S-7.5 path working exactly as designed, and it was the
cheapest stage of the whole run** — 3 seconds, one command, zero edits, versus
the ~4 minutes repo A cost to synthesize an equivalent graph by hand.

But the volume is the catch. `demo.json` is 3 tickets, 4 followups, 2 queues,
5.9 KB. That is enough to prove an endpoint *works* and nowhere near enough to
make it *slow*. **Fixture discovery gives correctness, not scale**, and those are
different needs: S-7.5 can ground a workload, but a performance investigation
still needs volume from somewhere. On the evidence here, discovery and synthesis
are not alternatives at all — discovery gets you a valid object graph to imitate,
and synthesis then multiplies it. That is a cheaper framing than either story
currently assumes, and it is worth checking against C before acting on it.

#### Evidence the endpoint did real work

| | value |
|---|---|
| Endpoint | `GET /api/tickets/`, HTTP Basic as `admin:Pa33w0rd` |
| Rows seeded | 18 objects — 3 tickets, 4 followups, 3 KB items, 2 queues, 2 attachments, 1 user |
| Response size (bytes) | 2 198 |
| Response time | 322 ms (warm) — **suspiciously slow for 3 rows; see below** |
| Did the response contain seeded values? | **Yes** — HTTP 200, 3 ticket objects, the fixture's literal `"Some django-helpdesk Problem"` and its nested `followup_set` present in the body |

#### Obstacles

| # | Obstacle | Category | Time lost | How resolved | Discoverable from the repo alone? |
|---|---|---|---|---|---|
| B-1 | `demodesk/config/settings.py:140-145` hardcodes `ENGINE: sqlite3` and a `BASE_DIR`-relative path. There is no `DATABASE_URL`, no `DB_*` variable, and no `local_settings` import hook — the file's only two `os.getenv` calls are for the teams feature flag. There is no supported way to point the demo project at Postgres. | **Database config not externalized** | ~2 min | Wrote `seeds/spike_settings_b.py`: a module outside the checkout that does `from demodesk.config.settings import *` and then overrides `DATABASES`, selected via `PYTHONPATH=/seeds` and `DJANGO_SETTINGS_MODULE`. The checkout stays byte-identical. | **The problem is trivially discoverable; the solution is not offered.** The hardcoding is plain in a file the README tells you to read. But the repo documents no override path, so resolving it means knowing the settings-overlay trick independently. This is the row I predicted *none* of A/B/C would hit — see the cross-repo section. |
| B-2 | No database driver is declared. `pyproject.toml` lists `django>=4.2` and 16 other dependencies; `psycopg`, `psycopg2`, and `psycopg2-binary` are all absent. A clean `pip install -e .` produces a project that cannot connect to Postgres at all, and the omission is invisible until connect time because SQLite (B-1) needs no driver. | Missing driver, hidden by the SQLite default | ~30 s | `pip install "psycopg[binary]"`. Choice of distribution was mine, since the repo expresses no preference. | **No — only by attempting it.** Nothing in the manifest, README, or docs says which Postgres driver this project expects, because as configured it never uses one. |
| B-3 | The demo superuser's password is `Pa33w0rd`. It appears in exactly one place in the repository: a **comment inside the `demo` target of the root `Makefile`** (`# The password for the "admin" user is 'Pa33w0rd' for the demo project.`). It is not in `README.rst`, not in `demodesk/README.rst`, not in `docs/`, and not adjacent to the fixture that creates the user — `demo.json` carries only the PBKDF2 hash. | Credentials in an unsearchable place | ~1 min | Read the `Makefile`. | **Yes, but only barely.** It is in the tree, in plain text, and `grep -ri password` finds it. But every *structured* place a reader would look — README, docs, the fixture itself — omits it, and a Makefile comment is not a location any convention would predict. Had it not been there, the account would have been unusable without resetting the hash, i.e. modifying seeded state. |
| B-4 | The documented way to run the demo is `make rundemo`, which is unusable for an API workload. The `Makefile` hard-fails at *parse* time if `node` is absent (`REQUIRED_BINS := node` with `$(error ...)`), and the `demo` target runs `yarn install` plus a ~60-line static-vendor copy routine before it ever reaches `migrate`. Grounding a JSON endpoint requires none of it. | Documented entry point demands an irrelevant toolchain | 0 (bypassed) | Ignored the `Makefile` and called `manage.py migrate` / `loaddata` / `runserver` directly, which worked. | Yes — but only if you are willing to *disregard* the documented path. Reading the `Makefile` as a checklist leads to installing Node and yarn to serve JSON. Same lesson as finding A-i: the naive path was cheaper than the documented one. |

#### Consequence for S-0.6 — B is a strong development target

`GET /api/tickets/` takes **14 queries to serve 3 tickets**, and 322 ms to
return 2.2 KB. The breakdown is a textbook nested N+1:

| Count | Query |
|---|---|
| 1 | the ticket list |
| 3 | `helpdesk_followup` — **one per ticket** |
| 4 | `helpdesk_followupattachment` — **one per followup** |
| 4 | `helpdesk_customfield` — **one per serialized ticket** |
| 2 | session + auth user |

The measurement signature S-0.6 asks for is therefore explicit and cheap to
assert: **queries scale as `1 + T + F + T` where `T` = tickets and `F` = total
followups, instead of a constant.** Load-testing at fixture scale would miss it
entirely — 3 tickets is fast enough in absolute terms that nothing looks wrong,
which is itself a good argument for the guard-counter invariant.

This is an unplanted defect in a real, maintained, 1.7k-star repository, found
inside five minutes. It makes `django-helpdesk` the strongest S-0.6 development
target seen so far, and it pairs well with `healthchecks` as the null-result
holdout. Recorded here; the decision belongs to S-0.6.

#### Excluded — not an obstacle of this repository

`PYTHONPATH=/seeds` arrived inside the container as
`C:/Program Files/Git/seeds`: Git Bash on the Windows host rewrites
POSIX-looking arguments before Docker sees them, and `MSYS_NO_PATHCONV=1` fixes
it. Cost about a minute. It is a property of the shell driving the spike, not of
`django-helpdesk`, and it is recorded here only so the time is accounted for and
nobody re-derives it from the transcript as a repo obstacle.

---

### Repository C — `netbox`

Pinned at `4877d11` ("Correct release date for v4.6.7", committed 2026-07-31;
shallow clone of `main` taken 2026-08-02). Version `4.6.7`, 85 MB, 202 tables.

**Wall clock:** start `07:43:37Z` → end `08:02:33Z` = **19 min**
**Outcome:** **grounded**
**Where it stopped** (if not grounded): n/a

**On the `CLAUDE.md` caveat:** neither `CLAUDE.md` nor `AGENTS.md` was opened at
any point during this run, so the caveat recorded at selection time does not
apply to these results. It still applies to any *future* agent run against this
repo, and the note stays in the candidate table for that reason.

#### Stage log

| Stage | Time | Notes |
|---|---|---|
| Clone and read the README | ~1 min | 85 MB shallow. Install docs live in `docs/installation/`, and are the most complete of the three repos by a wide margin. |
| Resolve dependencies / build environment | 21 s fail + 31 s apt + 105 s build | **Obstacle C-1.** `psycopg[c,pool]` compiles from source. |
| Configure settings (env vars, secrets, `settings.py`) | ~3 min | **Obstacle C-2** (config file inside the tree) and **C-3** (Redis). `SECRET_KEY` generated with the repo's own `netbox/generate_secret_key.py`. |
| Connect to Postgres | ~0 | Worked immediately once configured. |
| Run migrations | **7 min 04 s** | Clean on an empty database, but by far the slowest stage in the whole spike — see finding C-i. |
| Create a user / resolve auth | ~4 min | **Obstacles C-5 and C-6**, the two that actually cost time on this repo. |
| Seed data | 12 s load + 69 s re-migrate | **Obstacle C-4** — no fixtures in the tree at all. Restored a 3.1 MB SQL dump from a *separate repository*. |
| Find a candidate endpoint | ~1 min | `/api/dcim/devices/`, `/api/dcim/interfaces/`. A browsable DRF root and `drf-spectacular` schema make this the easiest of the three. |
| Get real data out of that endpoint | ~2 min | **Obstacle C-7** (54 s startup), then HTTP 200. |

#### Fixtures — did they already exist?

- [ ] `factory_boy` factories present — no `DjangoModelFactory` anywhere in the tree.
- [ ] pytest fixtures that create model instances — `netbox/utilities/testing/` has helpers, but they build objects per-test, not a loadable dataset.
- [ ] a seed / demo-data management command — 20+ management commands, all operational (`housekeeping`, `reindex`, `trace_paths`, …). None seeds.
- [ ] Django fixture files (`.json` / `.yaml`) — **zero.** No `fixtures/` directory exists anywhere in the repository.
- [ ] a `docker-compose` with a seeded database
- [x] **none in the repository — the data exists, in a different repository**

Notes: this is a **fourth** case, distinct from both A and B. NetBox ships no
fixtures and no factories, but it is not synthesis-from-scratch either: a
complete, curated, version-matched dataset exists at
`netbox-community/netbox-demo-data`, and `docs/development/getting-started.md`
points to it by name. The data is excellent — 72 devices, 1586 interfaces, 24
sites, 180 IP addresses, with realistic names like `dmi01-akron-rtr01`. It is the
only one of the three datasets at a scale where a performance measurement would
mean anything.

**But the pointer to it is stale, and following it as written fails.** The
in-repo docs say the demo data "is provided in JSON format and loaded into an
empty database using Django's `loaddata` management command". The demo-data
repository says the opposite: JSON dumps stop at v3.6 and "we no longer generate
demo data in this format due to its limitations dealing with complex data
models." The current artifact is a PostgreSQL dump restored with `psql`, which
is a completely different operation — it replaces the database rather than
populating one, so it has to happen *before* migrations rather than after.

Taken with A and B, the fixture question has four answers across three repos,
and "does the repo have fixtures" turns out to be the wrong question. See the
cross-repository section.

#### Evidence the endpoint did real work

| | value |
|---|---|
| Endpoint | `GET /api/dcim/devices/?limit=50` (and `/api/dcim/interfaces/?limit=100`), header `Authorization: Bearer nbt_<key>.<token>` |
| Rows seeded | 72 devices, 1586 interfaces, 24 sites, 180 IP addresses, 6 users — 202 tables restored |
| Response size (bytes) | 101 121 (devices) / 146 296 (interfaces) |
| Response time | 687 ms (devices, warm) / 649 ms (interfaces, warm) |
| Did the response contain seeded values? | **Yes** — HTTP 200, `count: 72` and `count: 1586` matching the row counts queried directly in Postgres, with demo hostnames (`dmi01-akron-pdu01`, `GigabitEthernet0/0/0`) in the body |

#### Obstacles

| # | Obstacle | Category | Time lost | How resolved | Discoverable from the repo alone? |
|---|---|---|---|---|---|
| C-1 | `requirements.txt` pins `psycopg[c,pool]==3.3.4`. The `c` extra builds `psycopg_c` from source, which needs `pg_config` and a compiler; the bare image had neither and `pip install` failed at metadata generation. | Missing build toolchain | ~2 min | `apt-get install gcc python3-dev libpq-dev`, then reinstall (105 s of compiling). | **Yes, clearly.** `docs/installation/3-netbox.md:13-14` lists `build-essential`, `python3-dev` and `libpq-dev` explicitly, and the error message names the missing binary (`couldn't run 'pg_config'`) instead of hiding it. Contrast obstacle A-1, which is the same missing library reported uselessly. |
| C-2 | Configuration is a Python file *inside the package* — the documented step is `cp netbox/netbox/configuration_example.py netbox/netbox/configuration.py` and then editing it. Doing that modifies the checkout, which the spike forbids. Four settings are mandatory: `ALLOWED_HOSTS`, `DATABASES`, `REDIS`, `SECRET_KEY`. | Config lives inside the tree | ~3 min | Used the `NETBOX_CONFIGURATION` environment variable, which names any importable module, with `PYTHONPATH=/seeds`. Checkout untouched. | **Yes** — documented at `docs/configuration/index.md:10`. Worth contrasting with B-1, which is the same obstacle *shape* with no supported escape hatch; there the overlay was an improvisation, here it is a feature. |
| C-3 | Redis is mandatory. `django-redis`, `django-rq` and `redis` are pinned, `configuration_example.py` requires **both** a `tasks` and a `caching` section, and NetBox validates their presence at startup. The spike environment deliberately shipped Postgres only. | **Requires an external service** | ~2 min | Added a `redis:7-alpine` service to the spike compose file, under a profile so the default environment still lacks it. | **Yes, unmissably** — it is in `requirements.txt`, in the config example, and in the install docs. This obstacle is about *provisioning*, not discovery: knowing you need Redis is free, having a Redis is not. That distinction is the one that matters for E7. |
| C-4 | No fixtures exist in the repository. The demo dataset lives in a **separate GitHub repository**, and the in-repo instructions for loading it are out of date — they describe a JSON/`loaddata` workflow that the data repository discontinued after v3.6. | **Fixtures exist only outside the repository, behind stale instructions** | ~4 min | Cloned `netbox-community/netbox-demo-data`, matched `netbox-demo-v4.6.sql` to the checkout's `release.yaml` (`4.6.7`), dropped and recreated `spike_c`, restored via `psql`, then re-ran `migrate` to bring the v4.6.0 schema up to 4.6.7 (69 s, 24 further migrations). | **No.** This is the clearest "not resolvable from the repository alone" result in the spike. It needs network access to a *different* repository, and then it needs the reader to *disbelieve* the in-repo documentation and follow the external README instead. An agent that trusts the docs it was given fails here — and fails quietly, since `loaddata` on a v3.6 JSON file against a 4.6 schema would produce a plausible-looking error rather than an obvious "you are following stale instructions" one. |
| C-5 | `configuration_example.py` ships `API_TOKEN_PEPPERS = {}`. NetBox starts fine with it and emits only a `UserWarning` — which appeared buried in seven minutes of migration output. Creating an API token then fails hard with `ValueError: API_TOKEN_PEPPERS is not defined`. | **Warning at startup, hard failure at use** | ~2 min | Added a pepper (minimum 50 characters) to the configuration module. | Yes — the requirement is commented in `configuration_example.py:71-79`. But the *timing* is the obstacle: the system reports the problem at a moment when nothing is broken, then breaks much later at the one step that converts a seeded database into a driveable API. A readiness check that only asserts "the server started" would pass here and still be unusable. |
| C-6 | The generated token defaults to **v2**, whose auth header is `Bearer nbt_<key>.<plaintext>`, not the `Token <key>` form that NetBox used for years and that most existing documentation and client code shows. Presenting it as `Token …` returns `403 {"detail":"Invalid v1 token"}` — an error that names a token version the caller never asked for. | Auth scheme changed; error message misleads | ~2 min | Read `users/models/tokens.py:203-212` and used the model's own `get_auth_header_prefix()` to construct the header. | **Yes, from the source.** `get_auth_header_prefix()` exists precisely to answer this and is the authoritative answer. Not obviously discoverable from the *docs* or from the error text, which points at v1 when the token is v2. |
| C-7 | `manage.py runserver` took **54 seconds** to bind — against 3 s for repo B and under 10 s for repo A. Nothing reports readiness in the meantime. | Readiness not observable, extreme case | ~1 min | Polled the port with `connect_ex` in a loop. | Yes, by polling. Recorded mainly because it sets the *scale* any timeout has to tolerate: a 30-second readiness timeout would have declared this repo ungroundable. |
| C-8 | NetBox uses a custom user model (`users.User`); `auth_user` does not exist. A query written against `auth_user` fails with `relation "auth_user" does not exist`. | Custom user model | ~1 min | Queried `users_user` / imported `users.models.User`. | Yes — `AUTH_USER_MODEL` is set in settings, and the error is unambiguous. Cheap here, but it is a real fork in any generic "create a user" routine. |

#### A note on one wrong turn

Roughly a minute went to assigning `Token.key` directly, which raised
`value too long for type character varying(12)`. That was my error, not the
repository's — but the field name is worth recording: on a v2 token, `key` is a
12-character *public prefix*, while the secret is `token`, and the model exposes
both. A generic "set the API key" routine that assumes `key` holds the credential
gets a database-level truncation error rather than anything explanatory.

#### Consequence for S-0.6 — C is realistic but not a planted-defect target

`/api/dcim/devices/` and `/api/dcim/interfaces/` are neither clean like repo A
nor obviously broken like repo B. Query counts against page size:

| `limit` | interfaces | devices |
|---|---|---|
| 10 | 39 | 16 |
| 25 | 35 | 16 |
| 50 | 35 | 17 |
| 100 | 41 | 23 |
| 200 | 53 | 23 |

The `limit=10` row is inflated by cold caches — it was the first request in the
loop — so the honest reading is a **fixed floor of roughly 35 queries for
interfaces and 16 for devices, plus mild sublinear growth**. That is not an N+1;
NetBox prefetches aggressively. It is a large constant per-request cost,
independent of how many rows you asked for.

Two consequences worth carrying forward. First, this is the measurement shape a
real mature application actually has, and it is the one the guard-counter
invariant exists for — an "optimization" that cut the floor from 35 to 30 while
doubling rows returned would look like a win on every metric measured here.
Second, **the loop above is itself a warning about S-0.4**: consecutive
identical requests differed by 4 queries purely from cache warmth. Ablation
deltas smaller than that are noise, and S-0.4 should interleave and discard a
warm-up rather than trusting a first measurement.

#### Consequence for the lab bench — `DEBUG` cannot be toggled at runtime

Setting `settings.DEBUG = True` after startup to capture queries **crashes
NetBox**: its URLconf appends `debug_toolbar.urls` conditionally on `DEBUG` at
import time, so flipping it later imports an app that is not in `INSTALLED_APPS`
and raises `RuntimeError: Model class debug_toolbar.models.HistoryEntry doesn't
declare an explicit app_label`. The measurements above were taken with
`connection.force_debug_cursor = True` instead, which captures queries without
touching settings.

This is a direct constraint on E1: **the query-counting primitive must use
`force_debug_cursor`, never `settings.DEBUG`.** The naive approach works on two
of the three repositories here and destroys the third, which is exactly the kind
of thing that would otherwise be discovered late and blamed on the target repo.

---

## Cross-repository analysis

### Recurrence matrix

Categories are discovered, not imposed — rename, split, and merge these to fit
what actually happened.

Rows below the rule were not in the template. They are the ones the runs found.

| Obstacle | A | B | C | Recurrent? |
|---|---|---|---|---|
| Python version mismatch | — | — | — | no — all three ran on 3.12 untouched |
| Dependency resolution failure | — | — | **C-1** | **no** — 1 of 3 |
| Missing/undocumented env vars | — | — | — | no |
| Database config not externalized | — | **B-1** | **C-2** | **yes — 2 of 3** |
| Migrations fail on a clean database | — | — | — | **no — 0 of 3.** All three migrated clean first attempt |
| Auth blocks every route | — | — | — | no |
| Custom user model | — | — | **C-8** | no — 1 of 3 |
| No fixtures or factories | **A-3** | — | **C-4** | **yes — 2 of 3**, but see below: the category is wrong |
| No obvious data-bearing endpoint | — | — | — | **no — 0 of 3.** All three had a discoverable REST list endpoint |
| Requires an external service (S3, Redis, mail, OAuth) | — | — | **C-3** | no — 1 of 3 |
| — *discovered rows follow* — | | | | |
| **Missing system library for the Postgres driver** | **A-1** (`libpq5`) | — | **C-1** (`libpq-dev` + gcc) | **yes — 2 of 3** |
| **Readiness not observable; had to poll** | **A-2** (>6 s) | — | **C-7** (54 s) | **yes — 2 of 3.** B bound in 3 s and never exposed it |
| **Credentials/config in an unpredictable place** | **A-3** (test base class) | **B-3** (Makefile comment) | **C-5** (config example comment) | **yes — 3 of 3** |
| **Documented setup path over-states or misstates what is needed** | **A-i** (needless `apt` step) | **B-4** (`make` needs Node to serve JSON) | **C-4** (stale `loaddata` instructions) | **yes — 3 of 3** |
| **Database driver not declared, or declared in a form that needs work** | **A-1** (source dist) | **B-2** (none at all) | **C-1** (`[c]` extra) | **yes — 3 of 3** |

### Summary

| | A `healthchecks` | B `django-helpdesk` | C `netbox` |
|---|---|---|---|
| Grounded? | **yes** | **yes** | **yes** |
| Wall clock (min) | **8** | **5** | **19** |
| Distinct obstacles | 4 | 4 | 8 |
| Fixtures existed? | no — synthesized, from a recipe read out of the test suite | **yes** — `loaddata`, 3 s, first try, but only 18 objects | no — in a *different repository*, behind stale instructions |
| Rows at the endpoint | 50 checks / 1000 pings (synthesized) | 3 tickets | 72 devices / 1586 interfaces |
| Queries at the endpoint | 3 (clean) | **14 for 3 rows (N+1)** | ~35 floor, sublinear |

Total: **42 minutes of wall clock for three repositories**, none of which was
known to the runner beforehand.

### Findings

**Recurrent obstacles** — these become playbook entries or Explorer tools:

1. **The Postgres driver is never simply installable — 3 of 3.** Every repo
   needed driver work, and each in a different way: A pinned the source
   distribution and lacked `libpq` at runtime; B declared no driver at all; C
   pinned the `[c]` extra and needed a compiler and headers. **Playbook entry:**
   before anything else, ensure `libpq` runtime *and* dev headers *and* a
   compiler are present, and be prepared to supply a driver the project never
   names. This is cheap to do unconditionally and expensive to diagnose.
2. **The thing you need is documented somewhere no convention would predict —
   3 of 3.** A's object graph was in `hc/test.py`; B's demo password was in a
   Makefile comment; C's mandatory pepper was a comment in the config example.
   In all three the information *was* in the repository, and in none of the three
   was it where a reader would look. **This argues the Explorer needs
   full-text search over the whole tree — including comments, Makefiles, and
   test files — not a structured reader that visits README, settings, and
   models.** The structured reader would have failed all three times.
3. **The documented setup path is wrong or wasteful — 3 of 3.** A's `apt` line
   installs a toolchain nothing uses; B's `make rundemo` demands Node and yarn to
   serve JSON; C's docs describe a `loaddata` workflow the data provider
   abandoned three minor versions ago. **In all three, the naive path was
   cheaper than the documented one.** Playbook entry: attempt the direct path
   first, treat documentation as a hint rather than a checklist, and never let a
   documented prerequisite block an attempt that has not yet been tried.
4. **Database configuration is not externalized — 2 of 3.** B hardcodes SQLite
   with no override; C requires a Python file inside the package. Both were
   solved by the same manoeuvre — a settings module outside the tree, imported
   over the project's own — but C *documents* that hatch and B does not.
   **The Explorer needs "settings overlay" as a first-class tool**, since it is
   the general answer and it preserves the read-only-checkout guarantee.
5. **Readiness is not observable — 2 of 3, and the spread is enormous.** 3 s for
   B, 54 s for C: an 18× range. Any fixed sleep is either wrong or wasteful, and
   logs did not distinguish "still booting" from "died" in repo A. **Poll the
   port; set the timeout at minutes, not seconds.**

**One-off obstacles** — noise, record but do not design around:

1. Custom user model (C only) — cheap to hit, unambiguous error.
2. Redis required (C only) — a provisioning cost, not a discovery problem.
3. `API_TOKEN_PEPPERS` warning-then-failure (C only) — but the *shape*
   (a warning at startup that becomes fatal later) is worth remembering even
   though it recurred nowhere.
4. NetBox's v2 `Bearer` token format (C only).

**Obstacles not resolvable from the repository alone** — these bound what any
agent can do, and belong in the honest-limitations page (S-17.2):

1. **C-4 — the fixtures are in another repository, and the in-repo instructions
   for using them are stale.** This is the only obstacle in the whole spike that
   could not be resolved from the checkout. Resolving it required fetching an
   unrelated repository *and* preferring its README over the target's own
   documentation. An agent that trusts the repository it was pointed at fails
   here, and fails in the worst way — by following clear, confident, wrong
   instructions to an error that looks like a data problem.
2. **C-3 — Redis.** Knowing it is needed is free; *having* one is not. Nothing
   an agent reads can provision a service. E2 must supply dependent services or
   the investigation stops.

### The fixture question was the wrong question

Three repositories produced four distinct answers, and none of them is the
binary S-7.5/S-7.6 split the backlog assumes:

| | Where a valid object graph came from | Cost |
|---|---|---|
| A | The **test suite** — `BaseTestCase.setUp` read as a recipe, then scaled up by hand | ~4 min |
| B | A **shipped `loaddata` fixture** — trivial to load, far too small to measure anything | 3 s to load; unusable for volume |
| C | A **separate repository**, version-matched, restored as SQL | ~4 min, needs network |
| — | Pure synthesis from models alone | never happened |

Two things follow.

**Discovery and synthesis are not alternatives — they compose.** In every case
the expensive part was learning the *shape* of a valid object graph (which
models, which required relations, which non-obvious columns like A's `Profile`
or `Project.api_key`). Once known, producing volume was trivial — repo A went
from recipe to 1000 rows in one script. So S-7.5 should be reframed from "find
loadable fixtures" to **"find any authoritative example of a valid object
graph"** — a fixture, a test base class, a demo dump — and S-7.6 becomes
"multiply it", which is much cheaper than synthesizing from model introspection.

**Fixture presence does not predict fixture usefulness.** B was the only repo
with real Django fixtures and the only one whose grounded database was too small
to measure anything — 3 tickets. C had no fixtures at all and produced the only
dataset at realistic scale. **Volume and validity are separate problems, and
S-7.5 as written only solves validity.**

### What this says about repository *shape*

The three repos were chosen to be three different shapes, and shape turned out
to predict almost nothing:

- **Size did not predict difficulty.** C is 85 MB and 202 tables against B's
  small tree, and C took 4× longer — but almost all of that was two mechanical
  waits (105 s compiling, 7 min migrating), not two hours of confusion.
- **Documentation quality did not predict difficulty either.** C has by far the
  best install documentation of the three and produced twice the obstacles.
  Thorough docs correlate with a complex system, not an easy one.
- **The reusable-app-plus-demo shape (B) was the *easiest*.** The prediction at
  selection time was that it would be the awkward one. It was grounded fastest,
  and the layout was announced in `manage.py` all along.

**The honest summary is that per-repo variance came from specific, local
accidents** — a driver extra, a stale doc link, a pepper setting — **not from
repository archetype.** That is mildly good news for E7: there is no taxonomy to
build, just a list of concrete things to check.

---

## Verdict

**Repositories grounded:** **3 of 3**

- [x] **3 of 3** — assumption holds. Proceed. Recurrent obstacles seed the
      initial playbook.
- [ ] **2 of 3** — assumption holds weakly. Proceed, but the failing repo's
      blocker is a known limitation and goes in S-17.2.
- [ ] **≤1 of 3** — **stop.** Reconsider the target framework or the
      workload-discovery approach before building E7.

**Decision: proceed to E7.** Three unfamiliar, maintained Django repositories
were each taken from `git clone` to an authenticated REST endpoint returning
real seeded rows, in 8, 5 and 19 minutes. Nothing needed a code change to the
repository under test. Exactly one obstacle out of sixteen could not be resolved
from the checkout.

**Read the verdict with its bounds, though.** Two of them matter:

- **The sample is biased toward maintained projects.** All three were committed
  to within three days of selection, and all three ran on Python 3.12 with no
  version friction whatsoever — the "Python version mismatch" and "dependency
  resolution failure" rows are empty largely *because of how the sample was
  drawn*. An abandoned repo pinning `psycopg2==2.7` against a Python that no
  longer builds it is a realistic and much harder case, and this spike says
  nothing about it. This was flagged before the runs started; it held up.
- **The runner had strong Django priors.** Recognizing that `hc/test.py` would
  contain the object graph, that a settings overlay solves a hardcoded
  `DATABASES`, and that `force_debug_cursor` substitutes for `DEBUG` are not
  things the repositories taught — they were brought in. The Explorer will need
  those as *tools or playbook entries*, which is precisely what finding 1-5
  above specify. **The 42-minute figure is a floor, not an estimate.**

**Consequences for the build** — what changes in E7, and which stories gain or
lose scope:

| Change | Story | Why |
|---|---|---|
| **Add a settings-overlay tool** — construct a settings module outside the checkout that imports the project's own and overrides it | E7 / S-7.2 | The general answer to hardcoded database config (2 of 3), and it preserves the read-only-checkout guarantee that S-2.3 depends on |
| **Full-text search across the whole tree must be a first-class tool**, including comments, Makefiles and test files | E7 / S-7.2 | 3 of 3 — every repo hid something essential outside the structured locations. A reader that visits README/settings/models would have failed all three times |
| **Provision the Postgres client stack unconditionally** — `libpq5`, `libpq-dev`, `gcc`, `python3-dev` — before attempting install | E2 / S-13.1 | 3 of 3 needed driver work. Seconds to do blindly, minutes to diagnose |
| **Readiness = poll the port, timeout in minutes** | E7 / S-7.2 | 18× spread (3 s → 54 s); logs did not distinguish booting from dead |
| **S-7.5 reframed** from "find fixture files" to "find any authoritative valid object graph — fixture, test base class, or external dump" | S-7.5 | The 4-way split above. The current framing describes 1 of 3 repos |
| **S-7.6 narrows** from "synthesize data" to "multiply a known-valid graph" | S-7.6 | Much cheaper. Synthesis-from-models-alone never happened once |
| **Query counting must use `force_debug_cursor`, never `settings.DEBUG`** | E1 | Toggling `DEBUG` at runtime crashes NetBox outright |
| **S-0.4 must interleave and discard a warm-up** | S-0.4 | Consecutive identical requests on C differed by 4 queries from cache warmth alone |
| **Dependent services are E2's problem, not the agent's** | E2 / S-17.2 | Redis cannot be provisioned by reading. Goes in honest limitations |

Nothing here removes scope from E7. The design survives the spike intact; it
gains five concrete tools and loses one wrong assumption about fixtures.

**Consequences for S-0.6** — is either repo a candidate for the development
target or the holdout?

**Yes — and conveniently, they are different repos.**

- **Development target: `django-helpdesk` (B).** `GET /api/tickets/` runs 14
  queries for 3 tickets, scaling as `1 + T + F + T`. This satisfies S-0.6's "at
  least one known performance defect documented with its expected measurement
  signature" with a **real, unplanted defect in a maintained repository** —
  strictly better than a defect we plant ourselves, which risks encoding the
  detector's assumptions into the subject. Caveat: the shipped fixture is 3
  tickets, so S-0.6 must multiply the object graph before the defect is
  measurable in wall-clock terms.
- **Holdout: `healthchecks` (A).** Its endpoint is already correctly prefetched
  at 3 queries and no defect was found. Given that *"null results are valid
  output"* and *"never manufacture a finding"* are project invariants, a holdout
  where **the correct answer is "nothing found"** is more valuable than a second
  defective repo — it tests the failure mode the invariants exist to prevent.
- **`netbox` (C) is the S-17.3 candidate**, not an S-0.6 one. Its ~35-query
  floor with sublinear growth is what a mature system actually looks like:
  nothing to "fix", and a perfect subject for the guard-counter invariant, since
  a change that lowered the floor while inflating rows returned would look like
  a win on every metric measured here.

Both S-0.6 repos are already grounded, reproducibly, by the scripts in `seeds/`.

---

## Follow-on

- **S-0.4 (ablation deltas) and S-0.5 (reset reliability): use
  `django-helpdesk`.** It grounded most cleanly (5 min, fixture loads in 3 s,
  resets trivially) *and* it has the N+1 that makes an ablation delta worth
  measuring. Note the two constraints this spike already found for S-0.4: the
  object graph must be multiplied past 3 tickets first, and consecutive
  identical requests varied by 4 queries from cache warmth alone — so interleave
  conditions and discard a warm-up rather than trusting a first measurement.
- **S-0.5 gets a free extra case**: repo C was reset mid-run by dropping and
  recreating `spike_c` under a live application, which worked. Sequence
  counters after a SQL-dump restore are worth checking specifically — the dump
  ends in `setval` calls, so a restore-based reset and a rollback-based reset
  have different failure modes.
- **For `tests/fixtures/` (S-0.7)**, the surprises worth planting are the ones
  that would silently break a naive harness rather than the ones that error
  loudly:
  - an endpoint whose query count has a **high fixed floor with sublinear
    growth** (netbox-shaped), to prove the detector does not call that an N+1;
  - a project whose `DATABASES` is hardcoded with no override, to prove the
    settings-overlay tool works and the checkout stays read-only;
  - a required setting that **warns at startup and only fails at use** (the
    `API_TOKEN_PEPPERS` shape), to prove readiness checking is not satisfied by
    "the process started".
- **`docs/adr/` owes one decision**: the query-counting primitive must use
  `connection.force_debug_cursor`, never `settings.DEBUG`, because toggling
  `DEBUG` after startup crashes at least one real target. That is a design
  constraint discovered here and not recorded in `02` or `03`.
