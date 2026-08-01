# S-0.3 — Can we ground real repositories?

**Status:** not started
**Run by:**
**Dates:**
**Timebox:** ~1 day

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

| # | Repository | URL | Stars | Last commit | Why chosen |
|---|---|---|---|---|---|
| A | | | | | |
| B | | | | | |
| C | | | | | |

**Selection constraints:** not a tutorial, not a boilerplate template, not a
project whose README is a deployment guide for a hosted service. It needs real
models, real endpoints, and migrations in the tree.

**Rejected candidates and why** (keep this — it documents the sampling bias):

-

---

## Per-repository record

Copy this block for each of A, B, C.

### Repository A — `<name>`

**Wall clock:** start `__:__` → end `__:__` = **__ min**
**Outcome:** grounded / partially grounded / failed
**Where it stopped** (if not grounded):

#### Stage log

Mark each stage and the time it took. `n/a` is a finding too.

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

This determines whether S-7.5 (fixture *discovery*) or S-7.6 (fixture
*synthesis*) carries the weight. Synthesis is far more expensive to build.

- [ ] `factory_boy` factories present
- [ ] pytest fixtures that create model instances
- [ ] a seed / demo-data management command
- [ ] Django fixture files (`.json` / `.yaml`)
- [ ] a `docker-compose` with a seeded database
- [ ] **none — data had to be synthesized by hand**

Notes:

#### Evidence the endpoint did real work

The AC says "an endpoint that returns real data." Prove it rather than assert
it — this is the same discipline S-7.8 later enforces on the Explorer, where
`work_verified` is computed by the harness because the agent is incentivized to
claim success.

| | value |
|---|---|
| Endpoint | |
| Rows seeded | |
| Response size (bytes) | |
| Response time | |
| Did the response contain seeded values? | |

#### Obstacles

One row per distinct obstacle. Be specific — "config" is not an obstacle,
"`SECRET_KEY` read from an env var with no `.env.example`" is.

| # | Obstacle | Category | Time lost | How resolved | Discoverable from the repo alone? |
|---|---|---|---|---|---|
| 1 | | | | | |
| 2 | | | | | |

That last column is the one that matters for E7. If a human could only resolve
it by searching the web, reading a mailing list, or guessing, then an agent with
`shell` and `read_file` cannot resolve it either — and the design needs to
account for that.

---

### Repository B — `<name>`

*(copy the block above)*

---

### Repository C — `<name>`

*(copy the block above)*

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
