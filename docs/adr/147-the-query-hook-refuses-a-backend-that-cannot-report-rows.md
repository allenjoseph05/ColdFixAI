# 147 — The query hook refuses a backend that cannot report rows

**Status:** accepted
**Date:** 2026-08-26

## Context

S-14.2 asks for the Django adapter, and specifically for the query hook to go
through `execute_wrapper`. Seven of the eight operations were transcriptions of
code Epic 7 and Epic 2 already had. The hook was the only new mechanism, and it
turned out to have a decision in it that the AC does not mention.

`counters.CATALOGUE` defines `db.query`'s amount as *rows returned by that
statement*, because `db.rows` is the same attachment read as a total — the
guard-counter rule needs both numbers from one run, since queries falling while
rows explode is only visible if both were counted at once.

`execute_wrapper` can read `cursor.rowcount` after the statement. Whether that
means anything depends on the backend, which was measured rather than assumed:

| Statement | PostgreSQL | SQLite |
|---|---|---|
| `SELECT` returning three rows | `3` | `-1` |
| `SELECT` matching nothing | `0` | `-1` |
| `INSERT` of three rows | `3` | `3` |
| `CREATE TABLE` | `-1` | `-1` |

On Postgres the two cases are distinguishable: `0` is a real empty result and
`-1` is *this statement has no row count*. On SQLite every `SELECT` looks like a
statement that returned nothing.

## Decisions

### 1. A backend that cannot report rows is refused, not recorded as zero

The tempting implementation records `max(rowcount, 0)` and moves on. On SQLite
that makes `db.rows` read zero on every query, which is a **guard counter reading
flat while rows grow** — the failure `CLAUDE.md` names in its own words, and the
one the guard exists to catch.

The alternative of recording `1.0` per statement is worse in a quieter way:
`db.query.total` would then equal the query count, and `measure_once` already has
a comment about that exact number — *a plausible number and the wrong one*.

So `ROW_COUNTING_VENDORS` holds what has been measured, the hook refuses at
install with a message naming the vendor and what does work, and a vendor nobody
has measured is refused rather than guessed in either direction. The cost is that
a SQLite project loses this hook; it keeps `drive`'s count, which does not depend
on `rowcount` at all. Missing is the recoverable one.

### 2. `execute_wrapper` and `CaptureQueriesContext` are not competitors

ADR 008 chose `force_debug_cursor` for counting inside the subject's own
interpreter, and `explorer.work.drive` still does exactly that. Nothing here
changes it.

The in-process hook is a different path with a capability the other one does not
have: **it can produce per-event stacks.** `connection.queries` is a list read
after the fact, and a stack has to be walked at the moment the query is raised,
from inside the call. S-3.9 localizes by walking those stacks to their divergence
point, so without a callback-shaped hook there is no localization on Django at
all. That is the argument for AC 2, and it is stronger than "the documented API".

### 3. Nothing under `src/` imports Django at module level

`pyproject.toml` keeps Django in the dev group on the ground that *nothing under
`src/` imports it*, and an adapter is where that stops being free. The import
lives inside `query_hook`, and a test imports the module in a fresh interpreter
and asserts `django` did not arrive with it.

One mypy override was added for `django.*` alone — Django ships no `py.typed` —
rather than globally, because `ignore_missing_imports` everywhere would also
swallow a typo in one of our own module paths.

### 4. `suite_command` asks S-7.1 rather than answering again

The first draft chose the test command by calling `fingerprint()` and reading
`test_runner`, then falling back to `manage.py` and to `unittest` itself. It was
wrong in a way a test caught: `fingerprint` returns `Unsupported` for a
repository whose manifests do not name Django, so a project declaring
`[tool.pytest.ini_options]` and nothing else fell through to `manage.py test` —
running a different set of tests than the project's own configuration asks for.

`_identify_test_runner` already handled all three cases, including *Django ships
a runner and every Django project has one*. It now takes a plain `Framework`
instead of a `Detected[Framework]` (it only ever read `.value`) and is exposed as
`declared_test_runner`, which the adapter calls. What stays in the adapter is
`SUITE_COMMANDS` — how each runner is *invoked*, which is the only part that is
knowledge about the framework rather than about the repository.

The public name is `declared_test_runner` and not `test_runner` because pytest
collects on the `test_` prefix; `TestRunner` carries `__test__ = False` one line
above for the same reason.

### 5. `capabilities()` is a function of what the adapter was given

An adapter constructed without a seeder or a target cannot honestly claim
`FIXTURE_SEEDING`, and the consequence of claiming it is concrete: `Registry.select`
offers scaling and ablation, and they fail at the point of seeding instead of
being withheld with a reason a reader can act on.

`FIXTURE_SHAPING` needs a `target` specifically, not merely a seeder. Only
synthesis takes a distribution; a repository's own factory builds whatever shape
it was written to build, and recording that as a *chosen* distribution would be a
claim nobody made — which matters because S-3.3 proved the shape decides the
answer for any per-parent cost.

## Consequences

**AC 3 is half met, deliberately.** *Works on the target repo and the holdout
repo*: the target (and the reserve, as a second real repository) are exercised by
a test that enumerates their route tables. **The holdout is not touched.** S-0.6
designates it as never used during development and `tests/test_holdout_discipline.py`
enforces that; S-17.1 is the story that runs the pipeline against it once, for
evaluation, and that is where this half is earned. Using it here to satisfy an
AC would spend the only measurement of generalization this project has, to
produce a green checkbox.

**The adapter is per project, not per framework.** Its fields are what grounding
established — a seeder, a target, a fixture shape, a verified database. That is
S-7.2's convention rather than a shortcut, and it is why `capabilities()` can be
honest.

**Container restart is not among the reset candidates.** It needs the container's
name, its image and the seed SQL, none of which is a fact about Django.
`choose_reset` takes an iterable, so a campaign holding those facts appends its
own candidate after the two the adapter offers.

**Sabotage: 10 properties, 10 caught** — the row amount, the vendor refusal, the
removal on an exception, the module-level import, a constant `capabilities()`,
a Python-only `read_source`, a swapped suite mapping, and the three delegation
joins (the interpreter never passed to enumeration, a command derived and not
run, a write reported but never made). The eighth found a weak assertion first:
dropping the interpreter left `routes_are_complete` false either way, and
`Resolution.error` is the only place *not attempted* and *asked and refused* are
distinguishable.
