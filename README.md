# ColdFix

> We automate the selection and sequencing of established performance-analysis
> methods, using an agent to decide which experiment to run next based on what
> the previous one revealed.

An agentic system that finds software performance problems **by running
experiments**, not by reading code. It proposes fixes, verifies them against
tests it wrote itself, and subjects them to an adversarial agent before a human
sees anything.

Python 3.12+. Django + Postgres is the first target framework.

---

## Status

**Pre-alpha, and the honest summary is that the machinery is built and has never
been run against a real subject for real.** 149 of 154 stories are done, across
171 ADRs. The fast test subset is ~3,300 tests.

The three viability spikes ran on 2026-08-02 and none of them invalidated the
design — though `S-0.4` and `S-0.5` both changed it, which is what they were for.

**What works, verified end to end.** A campaign assembles, binds a workload
artifact to a live Django subject, drives it at three scales, fits growth per
metric and reaches a decision. The Epic 17 composition check does exactly that
against a project with a planted N+1 and finds it.

**What has never happened.** No run against the holdout repository, and no live
model call outside one spike. That needs an API key and money, and it is
`S-17.1`.

**Reachable, not achieved.** Prompt caching was designed and inert — `Session.run`
rendered the cacheable blocks and nothing sent them — and `S-17.16` fixed that.
Requests now carry a breakpoint on the playbook, the source and the log, and four
agents stopped rendering the source and the log into their questions *as well as*
into the blocks, which was sending the dominant cost variable twice. The system
prompt is deliberately not among them: the investigate loop runs three steps on
one session, so its system string is not every step's prompt, and shaping it into
the request would have handed two of the three the wrong instructions. What still cannot be
stated is the hit rate — under a replaying client that figure comes from the
recording, so `docs/04-cost.md` §12.3 is marked reachable rather than quoted as
achieved. The first real number needs an API, and that is `S-17.1`.

There is no CLI and no configuration file. The entry point is `campaign_for(...)`
in `src/coldfix/orchestrator/assembly.py`, which takes twenty-five required
keyword arguments and five optional ones.

---

## Why an agent

The methods themselves are well-established and mechanizable. *Choosing which
one applies to a given program*, sequencing them, and interpreting the results
is documented in the fault-localization literature as requiring expert knowledge
of the specific program. That selection problem is the agent's job, and the
field named it as the bottleneck decades before LLMs existed.

**What this does not claim.** We do not understand codebases. We do not make
software fast in general. We do not replace performance engineering.

---

## Development

```bash
uv sync                      # install
uv run pytest                # all tests
uv run pytest -m "not slow and not timing"   # fast subset — the gate
uv run pytest -m "timing"                    # real-clock tests; run on a quiet machine
uv run ruff check --fix .    # lint
uv run ruff format .         # format
uv run mypy .                # types, strict
```

Lint, format, types, and the fast subset must all pass before a story is done.
The fast subset is the gate and currently collects 3,342 tests; a failure in it
is evidence, not noise. `timing` tests are separated because they read a real
clock — they are excluded from the gate so that a busy machine cannot turn a
scheduling delay into a red build, not because they are optional.

---

## Layout

| Path | Contents |
|---|---|
| `src/coldfix/bench/` | the lab bench — five deterministic operations that decide nothing |
| `src/coldfix/primitives/` | the thirteen registered experiment types the Diagnostician composes |
| `src/coldfix/sandbox/` | worktrees, mode separation, state reset, the production and real-time guards |
| `src/coldfix/screening/` | workloads, growth fitting, flagging, null results |
| `src/coldfix/explorer/` | the Explorer — grounding a subject to a runnable workload |
| `src/coldfix/diagnosis/` | the Diagnostician — hypotheses, experiment design, the append-only log |
| `src/coldfix/repair/` | the Surgeon — falsification tests, patches, retry discipline |
| `src/coldfix/audit/` | the finding audit and the Adversary |
| `src/coldfix/orchestrator/` | graph assembly, node binding, checkpointing, `campaign_for` |
| `src/coldfix/state/` | investigation state, the persistent store, the trust ledger |
| `src/coldfix/cost/` | routing, cascade, budgets, token accounting, context assembly |
| `src/coldfix/llm/` | the model client |
| `src/coldfix/adapters/` | framework-specific layer (Django first) |
| `src/coldfix/eval/` | benchmark runners, agreement harness, cost reporting |
| `src/coldfix/agents/` | the role index only — each agent's code lives in the four packages above |
| `docs/` | design documents — start with `docs/00-BRIEF.md` |
| `docs/adr/` | 171 architecture decision records |
| `spikes/` | timeboxed experiments that produce a finding, not shippable code |
| `tests/fixtures/` | a repository with deliberately planted defects |

`agents/` is named for the four agents and contains one module. That is not a
leftover. Which agent may see what is enforced structurally in six separate
places, each argued for on its own merits and none of them persuadable — and
somebody verifying the system has to find all six and know that six is all there
are. `roles.py` is that index. It declares and does not enforce, and the tests
beside it check the declaration against the code, so a withheld field that
quietly reappears on a handover type fails a test rather than a review.

---

## Documentation

Read `docs/00-BRIEF.md` first — it is the entry point and carries the authority
map. `docs/10-BACKLOG.md` is the execution plan. Where `docs/08-audit.md`
disagrees with `docs/02-architecture.md` or `docs/03-agents.md`, the audit wins.

---

## Refusals

These are not gaps to be closed later. They are categories where no verifier we
can build makes the change safe.

- **Concurrency and locking fixes** — output equivalence cannot detect an
  introduced race. Diagnose and report only.
- **Hard real-time systems** — measurement-based analysis is insufficient for
  WCET, and a caching optimization would improve every metric we measure while
  degrading worst-case timing. Detect and decline.
- **Third-party dependency code** — report the cause, never patch it.
- **Production environments** — test only, enforced by a database-URL pattern
  check that refuses to start.
