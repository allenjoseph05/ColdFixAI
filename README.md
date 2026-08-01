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

Pre-alpha. Epic 0 — foundations and viability. Nothing here works yet.

The three viability spikes (`S-0.3`, `S-0.4`, `S-0.5`) have not been run, and any
of them can still invalidate the design.

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
uv run pytest -m "not slow"  # fast subset
uv run ruff check --fix .    # lint
uv run ruff format .         # format
uv run mypy .                # types, strict
```

Lint, format, types, and the fast test subset must all pass before a story is
done.

---

## Layout

| Path | Contents |
|---|---|
| `src/coldfix/bench/` | the lab bench — five deterministic operations that decide nothing |
| `src/coldfix/primitives/` | experiment types the Diagnostician composes |
| `src/coldfix/agents/` | Explorer, Diagnostician, Surgeon, Adversary |
| `src/coldfix/orchestrator/` | graph, state, checkpointing, budgets |
| `src/coldfix/adapters/` | framework-specific layer (Django first) |
| `src/coldfix/eval/` | benchmark runners, agreement harness, cost reporting |
| `docs/` | design documents — start with `docs/00-BRIEF.md` |
| `docs/adr/` | architecture decision records |
| `spikes/` | timeboxed experiments that produce a finding, not shippable code |

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
