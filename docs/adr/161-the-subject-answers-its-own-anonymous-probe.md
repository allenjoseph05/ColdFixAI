# 161 — The subject answers its own anonymous probe

**Status:** accepted
**Date:** 2026-08-28

## Context

S-17.9 binds a repository to `ground_workload` so that `Resources.ground` has a
producer. Two things stood between those, and the first was a defect.

## The join S-17.7 left open

ADR 159 decided that grounding runs on one surface and routed all eight
subject-facing call sites through a `Surface` parameter. Every one defaults to
`None`, resolved to `HostSurface(root)` — which is what made the adoption
provably behaviour-preserving.

**`ground_workload` passed a surface to none of them.** It calls
`enumerate_entry_points`, `resolve_auth`, `verify_work` and `_seeder`, and builds
the `Grounding` progress is evaluated against; all five took the default. So every
step resolved its own `HostSurface`, and the decision never reached grounding at
all.

Both sides were complete and the join had no owner. That is the shape every
composition check in this project has found, and it arrived here without one
because S-17.7's AC were about the surface existing and the call sites routing
through it — both true, and neither about the composer.

**The composer now resolves once and passes the object down.** Not because one
`or` expression is tidier than five, but because five are five places that can
stop agreeing, and a command and the predicate judging it have to agree about the
filesystem or the loop cannot make progress. `factory_seeder` takes the surface
too, which closes the limit ADR 159 stated and left open.

**The test for this had to be driven, not inspected.** A `ground_workload` that
accepts a surface and threads it nowhere returns exactly what a correctly threaded
one returns against an unsupported repository — the run stops either way. What
tells them apart is whether the surface saw anything, so the test hands in a
recording surface and asserts on what reached it.

One trap inside that test is worth recording. The fake checkout's `manage.py` must
declare `DJANGO_SETTINGS_MODULE`, because `settings_module(root)` reads it from
there — without it `resolve_entry_points` returns before running any command, the
recording surface legitimately sees nothing, and **the test passes against a
sequence that threads nothing**. The first version had exactly that shape.

## The probe

`resolve_auth` makes one unauthenticated request to learn whether the route
demands a credential. `Reply`'s docstring says the type is *deliberately not an
HTTP client*: nothing under `src/` may reach the network on its own account. The
subject also has no egress (ADR 029), and grounding is `manage.py` introspection
rather than a running server — so there is nothing listening to request.

**So the subject answers the probe about itself**, in its own interpreter, through
the surface — the way `drive` already works. `probe_through(surface, python=,
settings=)` injects a program that calls Django's own test client and reports the
status, the headers, and where the answer came from.

`answered_path` is the load-bearing field and the reason the client is called with
`follow=True`. A client that follows redirects turns `login_required`'s 302 into a
200 holding a login page, and nothing in the status or the headers tells that
apart from the endpoint answering; `redirect_chain` is only populated when the
redirect was actually followed. A probe without it reports `answered_path` as
`None` for every login redirect in existence, and `resolve_auth` reads an open
route.

**A subject that does not answer is refused rather than scored.** Returning
`Reply(status=0)` would be an observation of nothing that `resolve_auth` reads as a
scheme, and the run would proceed to measure whatever a rejected request costs —
which is a real measurement of the wrong thing, the failure `NotGroundableError`
already exists to prevent one step later.

## What `grounder_for` binds, and what it does not

It binds the checkout, its interpreter, the surface, the plan the Explorer
decided, the reset proof, and how to make one request. It returns a callable
taking exactly `playbook`, `trusted_entries`, `learn` and `used` — the `Grounder`
protocol.

**Those four stay the node's deliberately.** They file under
`Fingerprint.playbook_key()`, derived *inside* the sequence, so a caller could bind
one only by fingerprinting the repository itself first. S-13.7 settled the split:
the campaign owns the repository, the run owns the journal. Binding them here
would re-decide that quietly.

**`settings` is supplied rather than detected.** `settings_module(root)` is a file
read and `grounder_for` has no other reason to open the checkout; more to the
point, a probe run against a guessed configuration that happens to load would
report a route needing no credential, which is the single answer that costs a real
measurement. A caller with its own client passes `request` and omits it. Supplying
neither is refused.

**The return type is `Callable[..., Grounded]`, not `Grounder`.** The protocol
lives in `orchestrator.adapters` and importing it into `explorer/` would point the
explorer at the layer above it. A test in `tests/explorer/` asserts the produced
callable satisfies the protocol structurally, which is where that dependency
belongs.

## Consequences

**Two of the six are now real**: `hands` (S-17.8) and `ground`. `bind`, `measure`,
`executor` and `probe` remain, and nothing assembles a `Resources`, so S-17.1 is
still not a run.

**The `Seeder` limit is closed for `factory_seeder` and only for it.** A
caller-supplied seeder is still a callable taking `root` and `python`, and binding
a surface into one is that caller's job.
