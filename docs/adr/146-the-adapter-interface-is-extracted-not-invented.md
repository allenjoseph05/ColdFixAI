# 146 — The adapter interface is extracted, not invented

**Status:** accepted
**Date:** 2026-08-26

## Context

S-14.1 asks for a formal interface with eight operations, four declarations and
full typing. The obvious way to write one is to design it: decide what a
framework ought to provide and hand S-14.2 a specification to satisfy. That
produces an interface whose only evidence is that it looked right, and S-14.3's
acceptance — *core code unchanged*, asserted by running two adapters through the
same pipeline — would then be the first time anybody found out.

There was a better source. Every one of the eight operations already exists as
Django-specific code in `src/`, and every one of the four declarations is already
*consumed* by a core type that today receives it by hand.

**Nothing in `src/` builds any of the four.** No `ProjectProfile` is constructed
outside tests, no `Localizer` is constructed anywhere at all, no query hook is
registered in production, and every `PatchPolicy` is the bare default. The
codebase had been writing this story's absence down for eleven epics:
`counting.py` says the counters *attach through hooks a framework adapter
declares (S-14.1)*; `normalize`'s `deny` is *the adapter's list of path
fragments*; `PatchPolicy` says *an adapter is the eventual source of the
project-specific entries*; `Framework.supported` names *the adapter, the reset
strategies and the query counter* as the three Django-specific things.

So the interface is a transcription, and each half can be checked against
something that already exists.

## Decisions

### 1. The declarations return the core's own types

`patch_policy()` returns a `PatchPolicy`, `localizer()` returns a `Localizer`,
`run_workload` returns the `Drive` the screening layer already reads, `seed`
returns exactly what `work.Seeder` returns. An adapter that returned its own
notion of a protected path would create a second vocabulary for a thing that
already has one, and the translation between two vocabularies is where a safety
rule quietly stops applying.

The test for the deny list goes through `localize` and asserts *which line a
reader is sent to*, rather than reading the field back — sabotaging
`localizer()` to drop the list has to change an answer, not a value.

### 2. An adapter may add protected paths and may not remove one

`patch_policy()` concatenates onto `DEFAULT_PROTECTED_PATTERNS`. There is no
argument to it and no field on `Declarations` that widens what a patch may touch.

The natural implementation — take the declaration as the policy — silently drops
seventeen default patterns when an adapter declares two, and the visible symptom
is a patch that edits the test suite and applies cleanly. `PatchPolicy`'s own
docstring promises the defaults *stand alone so that a project without an adapter
is still protected*; this is the method where that promise survives adapters
existing.

### 3. The write goes through the session, and the type refuses the other one

`apply_patch` and `read_source` take a `CandidateSession`. The write itself is
`session.apply_patch`, which is where the protected-path filter runs, because
that is the only route by which a diff becomes a file. `DiagnosticSession` is not
a subtype, so S-2.3's separation survives the new seam as a type error.

**This constrains the interface, not the implementer.** An adapter is arbitrary
Python and can open a file itself; several operations hand it a `root`. What it
cannot be is *handed* an unguarded writer. That is the difference between a
boundary and a promise, and it is worth saying plainly rather than claiming the
Protocol sandboxes anything.

### 4. `reset_state` is a provider, not the act — and the name is the backlog's

The AC names an operation that sounds like *reset the state*. It returns the
mechanisms instead, cheapest first, for two reasons that both predate this story.
S-2.7 will not trust a reset it has not driven ten times, so an adapter that
reset on demand would be an unverified reset wearing a verified one's name —
S-0.5 is the recorded instance, where rollback alone failed ten times out of ten
while passing the check its story specified. And `02-architecture.md` §1.5
requires a *fallback* when reset does not restore state, which needs a list
rather than a choice already made. `choose_reset` already takes exactly this
shape.

### 5. Capabilities are split, and the harness half is derived

`ADAPTER_CAPABILITIES` names the four an adapter can answer for — event
counters, fixture seeding, fixture shaping, state reset. `HARNESS_CAPABILITIES`
is the complement, computed rather than listed, so a thirteenth `Capability` is a
deliberate classification instead of an omission.

An adapter claiming `DIAGNOSTIC_WORKTREE` would be claiming a capability whose
implementation it has never seen, and `Registry.select` would offer a primitive
on the strength of it. Whether that claim is *enforced* is S-14.4's: a Protocol
cannot constrain what a method returns, and the conformance suite is where an
adapter is driven rather than read.

### 6. Not `@runtime_checkable`, and half the story is checked by mypy

`isinstance` against a Protocol checks that eight attributes exist and nothing
about their signatures. It would pass for any object with the right eight names,
which reads as a stronger statement than it is.

The conformance assertions are therefore annotated assignments and deliberate
`type: ignore` comments in the test file. `warn_unused_ignores` is part of
`mypy --strict`, so an ignore that stops being necessary fails the gate: widening
`apply_patch`'s parameter to `Session` makes `# type: ignore[arg-type]` on a
diagnostic session unused, and the run goes red. **That sabotage passes pytest
and fails mypy**, which is the concrete argument for the gate being four commands
rather than one.

### 7. No `AdapterError`

Written, then deleted. Every failure this module can have already has an owner:
`UnknownCounterError` for a name outside the catalogue, `CounterError` for a
counter that is not an adapter's to supply, `HookError` for a duplicate. An
exception class nothing raises is one the first caller reaches for instead of the
specific one.

## Consequences

**S-14.2 is a transcription too.** Each operation has a named existing
implementation to wrap, recorded as a table in the module docstring. The Django
adapter is where those eleven epics' worth of Django-specific code stops being
spread across `explorer/`, `sandbox/` and `primitives/`.

**The adapter is the last place a measurement can be fabricated, and this module
does not prevent it.** `run_workload` returns numbers because only the framework
knows how to count its own queries. *No finding without a measurement* is upheld
above this line by schemas and below it by nothing. S-14.4's conformance suite
must drive an adapter against a subject with a known answer; until then an
adapter is trusted code and should be read as such.

**The four declarations still have no production caller.** They are consumed by
core types that exist and are exercised by tests; wiring them into a campaign
needs a real adapter, which is S-14.2, and a second one to prove the core did not
move, which is S-14.3. This is a one-story gap inside one epic rather than the
`ExperimentRef` shape, and it is recorded here so the next session can tell the
difference.

**Sabotage: 7 properties, 7 caught** — the narrowed patch policy, the unwound
hook registration, the dropped deny list, the widened write path (mypy only), a
renamed operation, a hand-written capability split, and registering below the
counter catalogue.
