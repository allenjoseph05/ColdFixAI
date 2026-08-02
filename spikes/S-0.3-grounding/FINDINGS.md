# S-0.3 — Can we ground real repositories?

**Status:** A and B grounded — C not started
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

**Wall clock:** start `__:__` → end `__:__` = **__ min**
**Outcome:** grounded / partially grounded / failed
**Where it stopped** (if not grounded):

#### Stage log

| Stage | Time | Notes |
|---|---|---|
| Clone and read the README | | |
| Resolve dependencies / build environment | | |
| Configure settings (env vars, secrets, `settings.py`) | | |
| Connect to Postgres | | |
| Run migrations | | |
| Create a user / resolve auth | | |
| Seed data | | |
| Find a candidate endpoint | | |
| Get real data out of that endpoint | | |

#### Fixtures — did they already exist?

- [ ] `factory_boy` factories present
- [ ] pytest fixtures that create model instances
- [ ] a seed / demo-data management command
- [ ] Django fixture files (`.json` / `.yaml`)
- [ ] a `docker-compose` with a seeded database
- [ ] **none — data had to be synthesized by hand**

Notes:

#### Evidence the endpoint did real work

| | value |
|---|---|
| Endpoint | |
| Rows seeded | |
| Response size (bytes) | |
| Response time | |
| Did the response contain seeded values? | |

#### Obstacles

| # | Obstacle | Category | Time lost | How resolved | Discoverable from the repo alone? |
|---|---|---|---|---|---|
| 1 | | | | | |
| 2 | | | | | |

---

## Cross-repository analysis

Fill this in only after all three are done.

### Recurrence matrix

Categories are discovered, not imposed — rename, split, and merge these to fit
what actually happened.

| Obstacle | A | B | C | Recurrent? |
|---|---|---|---|---|
| Python version mismatch | | | | |
| Dependency resolution failure | | | | |
| Missing/undocumented env vars | | | | |
| Database config not externalized | | | | |
| Migrations fail on a clean database | | | | |
| Auth blocks every route | | | | |
| Custom user model | | | | |
| No fixtures or factories | | | | |
| No obvious data-bearing endpoint | | | | |
| Requires an external service (S3, Redis, mail, OAuth) | | | | |
| | | | | |

### Summary

| | A | B | C |
|---|---|---|---|
| Grounded? | | | |
| Wall clock (min) | | | |
| Distinct obstacles | | | |
| Fixtures existed? | | | |

### Findings

**Recurrent obstacles** — these become playbook entries or Explorer tools:

1.

**One-off obstacles** — noise, record but do not design around:

1.

**Obstacles not resolvable from the repository alone** — these bound what any
agent can do, and belong in the honest-limitations page (S-17.2):

1.

---

## Verdict

**Repositories grounded:** _ of 3

- [ ] **3 of 3** — assumption holds. Proceed. Recurrent obstacles seed the
      initial playbook.
- [ ] **2 of 3** — assumption holds weakly. Proceed, but the failing repo's
      blocker is a known limitation and goes in S-17.2.
- [ ] **≤1 of 3** — **stop.** Reconsider the target framework or the
      workload-discovery approach before building E7.

**Decision:**

**Consequences for the build** — what changes in E7, and which stories gain or
lose scope:

**Consequences for S-0.6** — is either repo a candidate for the development
target or the holdout?

---

## Follow-on

- S-0.4 (ablation deltas) and S-0.5 (reset reliability) both need a *grounded*
  repo. Whichever repo grounded most cleanly is the natural subject for both.
- Anything surprising here belongs in `tests/fixtures/` per S-0.7 — the planted
  defect repo should grow whenever a real repo surprises you.
