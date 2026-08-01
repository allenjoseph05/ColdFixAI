# CLAUDE.md

An agentic system that finds software performance problems **by running experiments**, not by reading code. It proposes fixes, verifies them against tests it wrote itself, and subjects them to an adversarial agent before a human sees anything.

Python 3.12+. Django + Postgres is the first target framework.

---

## Start of every work session

1. Read `docs/00-BRIEF.md` — scope, refusals, authority map.
2. Read the current epic's section in `docs/10-BACKLOG.md`.
3. State which story you are working on before writing code.

Read on demand, not every session:

| Need | File |
|---|---|
| What a primitive is and how it works | `docs/01-primitives.md` |
| Artifact schemas, layer contracts | `docs/02-architecture.md` |
| Agent configs, tools, prompts | `docs/03-agents.md` |
| Model routing, caching, budgets | `docs/04-cost.md` |
| Citations for a design decision | `docs/05-research.md` |
| Why a limit exists | `docs/06-validation.md` |
| Customer-facing capability claims | `docs/07-use-cases.md` |
| **Corrections to 02 and 03** | `docs/08-audit.md` |

`docs/08-audit.md` supersedes parts of `02` and `03`. When they disagree, the audit wins.

---

## Commands

```bash
uv sync                      # install
uv run pytest                # all tests
uv run pytest -m "not slow"  # fast subset
uv run ruff check --fix .    # lint
uv run ruff format .         # format
uv run mypy .                # types, strict
```

Run lint, format, types, and the fast test subset before saying a story is done.

---

## Working agreement

**One story at a time.** Do not start the next until every acceptance criterion of the current one is demonstrably true.

**A story is done when its AC are provable, not when the code looks right.** If an AC says "a test proves X is impossible," write a test that actively attempts X and asserts failure.

**Read the `Notes:` line on every story.** Each one exists because a design pass found a failure mode. A story implemented without its note will look correct and be silently wrong.

**Ask before deviating.** If a story's AC seem wrong or a dependency is missing, say so rather than improvising a different design.

**One story per branch, one concern per commit.** Commit messages state the story ID.

**Record decisions.** Anything not specified in the docs that you have to decide goes in an ADR under `docs/adr/`.

---

## Non-negotiables

These are project invariants. Violating one is a bug even if tests pass.

- **No finding without a measurement.** Enforced by schema, not by prompt. A conclusion drawn from reading code is not a finding.
- **Ablation runs can never produce a patch.** Separate container, separate worktree, destroyed on exit. Structural, not conventional.
- **The experiment log is append-only.** Never reorder or re-summarize it mid-investigation — that invalidates prompt caching and multiplies cost by ~20×.
- **Guard counters on every metric.** Queries down while rows explode is not an improvement.
- **Exclusions carry their preconditions.** "Not the database" is only true under the fixtures, scales, and platform tested.
- **Null results are valid output.** "Screened 9 workloads, nothing found" ships as an answer. Never manufacture a finding.
- **The Adversary never sees the Surgeon's reasoning.** Enforced by constructing a fresh message list, not by instructing the model to ignore it.
- **Never cascade to a cheap model on hypothesis generation or attack design.** No deterministic validator exists for those.

---

## Refusals

The system declines these categories. They are not gaps to be closed later.

- **Concurrency and locking fixes** — output equivalence cannot detect an introduced race. Diagnose and report only.
- **Hard real-time systems** — measurement-based analysis is insufficient for WCET, and a caching optimization would improve every metric we measure while degrading worst-case timing. Detect and decline.
- **Third-party dependency code** — report the cause, never patch it.
- **Production environments** — test only, enforced by a database-URL pattern check that refuses to start.

---

## Code style

- Type hints everywhere. `mypy` strict. No `Any` without a comment explaining why.
- Pydantic models for every artifact that crosses a node boundary.
- Prefer explicit over clever. This code will be read by someone verifying a safety property.
- No speculative abstraction. The primitive registry is the one designed extension point; everything else stays concrete until a second case exists.
- Errors are typed and specific. Never swallow an exception to keep a run going.
- Comments explain *why*, never *what*. Delete any comment that restates the line below it.

---

## Testing

- Unit tests for the lab bench run against synthetic programs with known complexity.
- `tests/fixtures/` holds a repository with deliberately planted defects — an N+1, a quadratic loop, an over-fetch, a slow import. Grow it whenever a real repo surprises you.
- Agent logic is tested against a mock LLM client replaying recorded responses. No test hits a real API.
- Safety properties get adversarial tests: the test attempts the violation and asserts it fails.

---

## Do not

- Do not add a model call where a function would do. Counting, curve fitting, stack grouping, and byte comparison are code.
- Do not let an agent report a measurement. Agents reason about measurements the harness took.
- Do not build the Surgeon before the finding audit exists (E9 precedes E10).
- Do not implement MCP before a second adapter exists in another language. Until then it is overhead.
- Do not write a fuzzer or an evolution engine. Wrap existing ones.
- Do not add caching, retries, or connection reuse to the tool's own hot paths without noting it — that is the exact class of change the system flags in other people's code.

---

## Hard enforcement

This file is context, not configuration. For rules that must hold regardless of what any agent decides, the enforcement lives in code:

| Rule | Enforced by |
|---|---|
| Cannot modify tests or harness | `apply_patch` rejection (S-2.4) |
| Cannot run against production | startup URL check (S-2.5) |
| Diagnostic diffs cannot ship | worktree separation (S-2.3) |
| Real-time systems refused | pre-grounding detection (S-2.8) |
| Budget cannot be exceeded | halt-and-checkpoint (S-5.4) |

If you find yourself relying on this file to prevent something dangerous, that rule needs code instead.
