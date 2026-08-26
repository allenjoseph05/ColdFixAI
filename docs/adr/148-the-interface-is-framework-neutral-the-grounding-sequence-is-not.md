# 148 — The interface is framework-neutral; the grounding sequence is not

**Status:** accepted
**Date:** 2026-08-26

## Context

S-14.3 asks for a second adapter and for *core code unchanged*, asserted by a
test that runs both adapters through the same pipeline. `00-BRIEF.md` §5 step 15
states the acceptance as *runs on SQLAlchemy without core changes*.

A second adapter is the only thing that can check the first. An interface shaped
around Django satisfies a Django adapter by construction, and nobody finds out
until a second framework arrives.

The story has a scope fork in it, and it has to be named rather than resolved
silently, because the two readings produce very different work.

## Decisions

### 1. The pipeline is the interface's operations, not the grounding sequence

`FlaskAdapter` implements all eight operations against a real Flask application
with a real SQLAlchemy engine, and `tests/adapters/test_both.py` drives both
adapters through one function using core APIs only.

**What that establishes:** no file outside `adapters/` changed to add the second
adapter, and the core turns two entirely different mechanisms — a route table
asked of the framework versus decorators read off files, an `execute_wrapper`
versus an `Engine`-class event listener — into the same `Enumeration`, the same
`db.query` hook name, the same `Drive`, the same `PatchPolicy`.

**What it does not establish**, and the docstrings say so in both adapters:
grounding does not run on Flask. `explorer/compose.py` calls
`enumerate_entry_points` directly, `stages.PREDICATES` has one entry, and
`Framework.supported` is `self is Framework.DJANGO`. Those three are *correct
today* — grounding really does only support Django — and the honest thing is to
leave them saying so.

**Making `fingerprint` accept Flask without doing the rest would be worse than
leaving it.** The refusal would move from an accurate *not a supported framework*
to a `KeyError` on `PREDICATES[Framework.FLASK]` one call later. Partial is not a
midpoint here; it converts a clear refusal into a crash.

What the remaining work is: route `compose.py`'s enumeration and stage
predicates through the adapter, and replace `Framework.supported` with *an
adapter exists for it*. That last one needs an adapter registry, and the registry
cannot live in `explorer/` because adapters import it — the decision has to move
to the campaign, which is the only layer allowed to know both. Filed on S-14.5,
which is the story that already owns the boundary question.

### 2. The differences between the adapters are the evidence, not the noise

Three are load-bearing and each is tested:

**No synthesis.** `explorer/synthesis.py` introspects Django models. There is no
SQLAlchemy equivalent, so `FlaskAdapter` has no `target`, seeds only from a
supplied mechanism, and never claims `FIXTURE_SHAPING`. The consequence is
visible in the shared pipeline: a primitive requiring a chosen distribution is
**withheld with a reason naming the missing capability** rather than offered and
then failed at the point of seeding. That is the tri-state applicability design
(ADR 013's descendant) paying for itself across two adapters for the first time.

**Read, not resolved.** Flask routes are decorators and can be parsed;
`Enumeration.resolution` records that the framework was never asked, so a
blueprint registered behind a condition is *missing and known to be missing*.

**An event listener, not a wrapper.** `after_cursor_execute` fires on the
`Engine` class, so it catches engines created after the block opens — which is
precisely the limitation the Django hook has to document, because Django's
connections are thread-local objects that must exist before they can be wrapped.

Their `reset_state` implementations are **identical**, and that is a finding too:
resetting a Postgres database is a fact about Postgres, and a rollback knows
nothing about which ORM wrote the rows.

### 3. `ROW_COUNTING_VENDORS` moved into the interface

ADR 147 put it in the Django adapter. The second adapter needed the same answer
for a different ORM, which settles what kind of fact it is: a property of the
database, not of the framework. SQLAlchemy over Postgres and the Django ORM over
Postgres ask the same driver the same question.

### 4. `query_hook` takes `aliases`, and a test forced the question

ADR 008's consequences say the instrument is needed on *each connection under
measurement*. The first implementation wrapped every alias Django knows, which is
the right default — an uninstrumented alias is a silent undercount.

Writing the tests showed the missing half. **Django settings are process-global
and `override_settings` does not swap a connection**: it warns that overriding
`DATABASES` leads to unexpected behaviour and the handler goes on returning the
original vendor. Measured, after two fixtures in one module configured different
databases and the second silently got the first one's — which had passed
undetected because `-m postgres` deselected the fixture that ran first.

So both backends are configured at once and each test names the alias it is
measuring. The parameter is not test scaffolding: a project whose settings hold a
replica on a backend that cannot report rows would otherwise be un-instrumentable,
and narrowing is a decision somebody states rather than one the module infers. An
alias Django does not have is refused rather than skipped.

### 5. Two mechanical assertions carry AC 2

Prose cannot check *core code unchanged*, and both natural ways of faking it are
invisible in a diff that also adds a legitimate adapter.

`test_the_pipeline_names_neither_framework` reads `run_pipeline`'s own source and
fails if either framework's name appears in it, so making the shared pipeline
pass by branching *is* the failure.

`test_no_core_module_imports_an_adapter` walks `src/coldfix/` with `ast` — not a
grep, because a docstring naming the package is not an import — and asserts the
direction of the dependency. It is the invariant that breaks first when a
framework-specific problem is solved in a framework-agnostic file, and once
broken it is very hard to see: everything still works and the layering is gone.

## Consequences

**Flask and SQLAlchemy are dev dependencies now**, on the terms already recorded
three times for Django, DRF and factory-boy: nothing under `src/` imports them,
the drive program is source text run in the subject's own interpreter, and a fake
app with a fake engine would only demonstrate that the interface fits a fake.

**The second adapter is not a stub.** It drives a real Flask application through
its own test client, counts real SQLAlchemy statements, and the test seeds more
rows and asserts the query count *moves* — because a counter wired to a constant
passes every other assertion.

**A leaked container taught a fixture rule.** The Postgres fixture closed its
Django connection in a `finally` ahead of `docker rm`; the close raised, the
removal never ran, and the container held the port against every later run.
Cleanup that can itself fail goes in its own `try`, ahead of the cleanup that
must not be skipped.

**Sabotage: 7 properties, 7 caught** — a core module importing an adapter, the
second adapter claiming a capability it cannot supply, routes matched on the
object name so blueprints vanish, the drive program not counting, the row amount
replaced by a bare count, the listener removed only on the happy path, and the
shared pipeline branching on which adapter it holds. An eighth was caught before
the sabotage pass: the blueprint claim in the module docstring had no test behind
it, so a blueprint route was added to the fixture first.
