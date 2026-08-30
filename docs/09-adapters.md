# 09 — WRITING AN ADAPTER

**For somebody outside this project who wants ColdFix to run on their framework.**

An adapter is the only place in the system allowed to know what a framework is
called. Everything above it — screening, the primitive registry, the evidence
chain, the repair path — is written against eight operations and four
declarations, and nothing above the adapter layer imports a framework or branches
on one. There is a test that enforces that direction (`test_both.py`), so it is a
property of the codebase rather than an aspiration.

Two adapters ship: Django + DRF + Postgres, and Flask + SQLAlchemy. Read both
before writing a third. They are deliberately different — one asks its framework
for a route table and the other reads decorators; one wraps a cursor and the
other listens for an event — and the differences are where the interface's shape
is visible.

---

## 1. What you implement

`coldfix.adapters.interface.FrameworkAdapter`, a `Protocol`. Implement it as any
class you like; there is no base to inherit and no registration to perform.
Because it is a Protocol, `mypy` checks your class against it the moment you
annotate a variable with it:

```python
from coldfix.adapters import FrameworkAdapter

adapter: FrameworkAdapter = MyAdapter()   # type-checked, not runtime-checked
```

It is deliberately **not** `@runtime_checkable`: `isinstance` against a Protocol
checks that eight attributes exist and nothing about their signatures, which
reads as a stronger statement than it is.

### 1.1 The two declarations

| Member | What it is |
|---|---|
| `framework` | The `Framework` this adapter is for. |
| `declarations` | A `Declarations`: ORM dialect, hook points, framework-internal frames, protected paths. |

**`orm`** is the dialect a query hook, a row counter and a reset mechanism are
all written against. Declared rather than detected, because an adapter *is* the
answer for the framework it implements.

**`hooks`** maps a counter name from `coldfix.primitives.counters.CATALOGUE` to a
`Hook`. The names are not free-form: registration refuses a name outside the
catalogue, a counter that is framework-free (this system installs those), and a
name that is a *reading* of another counter's hook — `db.rows` is `db.query`'s
total, so you register `db.query` and both are available.

A `Hook` takes a `Record` callback and returns a context manager that has the
instrumentation installed for its duration. The amount you record is defined by
the catalogue: for `db.query` it is **the rows that statement returned**, because
the guard-counter rule needs queries and rows from one attachment — queries
falling while rows explode is only visible if both were counted at once.

> **If your backend cannot report rows per statement, refuse rather than
> recording zero.** Postgres reports `rowcount` for a `SELECT`; SQLite returns
> `-1` for every one. Recording zero makes a guard counter read flat while rows
> grow, which is the one failure this project names in its own non-negotiables.
> `ROW_COUNTING_VENDORS` holds what has been measured. See ADR 147.

**`internal_frames`** are path fragments — `django/db/`, `sqlalchemy/`,
`site-packages` — dropped from captured stacks before localization. Fragments,
not globs, matched against the normalized path, so you never write a separator.
Without them a localization stops at the framework's deepest frame and names a
line nobody investigating their own project can change.

**`protected_paths`** are framework-specific paths a patch may not touch —
migrations, generated assets. They are **added** to `DEFAULT_PROTECTED_PATTERNS`
and cannot replace them; `patch_policy()` concatenates. Do not protect settings
modules: swapping a configuration value and re-measuring is the safest primitive
in the system, and protecting settings refuses the class of fix most likely to be
correct.

### 1.2 The eight operations

| Operation | Contract |
|---|---|
| `capabilities()` | What *this adapter* supplies, drawn from `ADAPTER_CAPABILITIES`. Never a constant if it depends on what the adapter was constructed with. |
| `discover_workloads(subject, timeout)` | A ranked `Enumeration`, with `resolution` stating whether the framework was asked or files were read. |
| `seed(subject, scale, timeout)` | `(FixtureRecipe, {model: rows})`. Refuse with a typed error if you have no mechanism; never invent rows. |
| `run_workload(subject, …)` | A `Drive`. See §3 — this is the one the conformance suite works hardest on. |
| `run_tests(session, selection, timeout)` | Run the subject's own suite in the session; hand back the `ExecutionResult` whole. |
| `read_source(session)` | Worktree-relative paths to text. Include templates: they decide what a view returns. |
| `apply_patch(session, diff)` | **Delegate to `session.apply_patch`.** See §4. |
| `reset_state(subject)` | The mechanisms that can restore the subject, *cheapest first*. A provider, not the act. |

**`capabilities()` is a promise the registry acts on.** Claim `FIXTURE_SEEDING`
and a scaling experiment will be offered; if you cannot actually seed, it fails
at the point of seeding instead of being withheld with a reason a reader can act
on. Claim nothing you cannot do, and claim nothing from
`HARNESS_CAPABILITIES` — a diagnostic worktree and an input-mutation engine are
this system's, whatever your framework is.

**`reset_state` returns a list rather than resetting.** A reset is not trusted
until it has been driven ten times against real row counts (`choose_reset`), and
a fallback is required because the cheapest strategy is not always correct — a
transaction rollback restored state on nine cycles out of ten in this project's
own measurements. Offer what you have, cheapest first, and let the harness
verify.

---

## 2. The subject and the session

`Subject` is the checkout and the interpreter that runs it, held together because
they must agree. It carries no measurement and no credential.

A `CandidateSession` is a checkout with the patch filter attached. Note the
asymmetry, which is a safety property rather than an inconsistency:

- `run_tests` takes a `Session` — either mode. Running the tests on the
  unpatched revision is how you show it was healthy before.
- `read_source` and `apply_patch` take a `CandidateSession` — the patched mode
  only. A diagnostic session may run any command and therefore write any file;
  giving it a reader would let a deliberately-broken diagnostic run emit a diff
  to disk and hand it out.

`DiagnosticSession` is not a subtype of `CandidateSession`, so that rule is a
type error rather than a convention.

---

## 3. Measurements: what the harness cannot check for you

Only your framework knows how to count its own queries, which means **the adapter
is the last place in the system where a measurement can be fabricated**. Nothing
above it can tell an invented number from a measured one, and every schema in the
system will accept it.

What the conformance suite *can* check is the set of relationships the caller
already knows:

- as many samples as `repeats` asked for — a driver that runs once and reports
  five is reporting one measurement five times;
- `seconds` is the median of `samples` — a median you did not compute from your
  own samples is a number nobody measured;
- `scale` and `created` come back as they went in;
- a warm-up happened, and is reported separately rather than folded in.

Beyond that it is on you. Write a test that seeds more rows and asserts your
query count *moves*: a counter wired to a constant passes every structural check
there is.

---

## 4. Applying a patch

Your `apply_patch` should be one line:

```python
def apply_patch(self, session: CandidateSession, diff: str) -> frozenset[str]:
    return session.apply_patch(diff)
```

plus whatever your framework needs *afterwards* — a rebuild for a compiled
language, nothing for Python. The session's own `apply_patch` is where the
protected-path filter runs, and it is the only sanctioned route from a diff to a
file. An adapter that writes the file itself bypasses the filter completely, and
**every other check in the conformance suite passes for it**. That is why there
is a check whose whole job is to hand you a diff touching a test file and require
a `ProtectedPathError`.

---

## 5. Checking your adapter

```python
from coldfix.adapters import Subject
from coldfix.adapters.conformance import Inputs, run_conformance

report = run_conformance(
    Inputs(
        adapter=MyAdapter(...),
        subject=Subject(root=Path("/path/to/repo"), python=["python"]),
        session=my_candidate_session,      # unlocks run_tests, read_source, apply_patch
        entry_point="/things",             # unlocks the measurement checks
        database=my_verified_database,     # unlocks reset reliability
        mutate=lambda: write_some_rows(),  #   …and needs a workload that writes
        events=lambda: hit_the_database(), # unlocks the overhead check
        event_count=500,
    )
)
print(report.describe())
assert report.attested
```

**Read `attested`, not `conforms`.** `conforms` means nothing failed;
an adapter run with no inputs conforms trivially, because most checks are skipped
and **a skipped check is not a passed one**. `attested` means nothing failed
*and* nothing was skipped. The rendered report says which checks did not run and
what to supply.

Two checks are weaker than they look, and knowing which is part of using the
suite honestly:

- **Protected paths.** `patch_policy()` concatenates onto the defaults, so an
  adapter that uses `Declarations` cannot narrow them. The check exists for the
  one remaining route — a subclass overriding the method.
- **Internal frames.** The stack is synthesized from *your own* first fragment,
  so the check catches an empty declaration and a broken hand-off. It cannot tell
  whether your fragments are the right ones for your framework. Write that test
  yourself, against a real stack from a real request.

Nothing in the suite raises; every check reports. A conformance report is a list,
and a suite that stopped at the first failure would tell you about one problem
per run.

---

## 6. Registering your adapter

Implementing the eight operations makes an adapter that **conforms**. Three more
lines make it one the campaign can actually select, and until S-14.6 they did not
exist — the grounding sequence knew Django by name in three places and an adapter
for a third framework was refused at fingerprinting however well it conformed.

**Supply what grounding needs.** ADR 009 asks nine questions of a repository, and
six of them are answered from a payload the subject is probed for — those are
`FRAMEWORK_NEUTRAL_PREDICATES` in `explorer/stages.py` and you inherit them. The
other three are yours: `CLONE` and `ENDPOINT` need your entry-point enumerator,
and `CONFIGURE` runs whatever your framework's own check command is. Register the
union at module scope:

```python
from coldfix.explorer.registry import Grounds, register
from coldfix.explorer.stages import FRAMEWORK_NEUTRAL_PREDICATES, Stage

MY_PREDICATES = {
    **FRAMEWORK_NEUTRAL_PREDICATES,
    Stage.CLONE: _clone,
    Stage.CONFIGURE: _configure,
    Stage.ENDPOINT: _endpoint,
}

register(
    Grounds(
        framework=Framework.MYFRAMEWORK,
        enumerate_entry_points=my_enumerator,
        predicates=MY_PREDICATES,
    )
)
```

`register` refuses a table missing any of the nine, because `evaluate` measures
all nine and a partial mapping is a `KeyError` in the middle of a run rather than
a framework that is partly supported. It also refuses a second registration for
one framework: whichever import ran last would win, silently.

**Be imported.** Registration is an import side effect, so the registry holds
what a process happened to import. Add your module to `adapters/__init__.py`; a
test reads that directory for `register(` and fails if anything registers and
nothing imports it. A framework whose adapter nobody imported is not *withheld* —
it does not exist, and *absent* reads exactly like *unsupported*.

**Be wired.** Add your class to `ADAPTERS` in `cli/wiring.py`, which is how
`coldfix.toml`'s `[project].framework` reaches an implementation. That module is
the only one outside `adapters/` permitted to import an adapter, and the
exemption is asserted to have exactly one entry.

Note that these are two different questions. The registry answers *can this
framework be grounded*; the wiring answers *which class implements it*. A
framework can have an adapter and no grounding support — Flask does, today — and
`coldfix plan` reports the difference rather than letting you find out at the
fingerprint.

## 7. Checking it

```bash
coldfix plan --config your.toml
```

reports the adapter it resolved, the counters read off your declarations, how
many capabilities you claimed, and whether your framework is groundable at all.
It opens no container, no database and no model client, so it costs nothing and
answers on a laptop with neither.

If `groundable` says no, you have an adapter and no `register(...)` — the run
would be refused at fingerprinting with a message naming what is missing.
