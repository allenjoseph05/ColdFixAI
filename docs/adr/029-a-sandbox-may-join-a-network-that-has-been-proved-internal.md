# 029 — A sandbox may join a network that has been proved internal

**Status:** accepted
**Date:** 2026-08-06

## Context

Epic 2 shipped nine stories, each individually tested. Asked whether it was
flawless, the honest answer turned out to be no, and the reason was not a bug in
any module — **the modules had never been composed.** The reset tests connected
to Postgres from the host; the sandbox tests ran workloads that talked to
nothing. Nothing anywhere proved a containerised workload could reach a database
and be reset afterwards, which is the only thing the epic is for.

Writing that end-to-end test was impossible as the code stood. `Sandbox`
hardcoded `--network none`, which gives a container a loopback interface and
nothing else, so a workload could not reach a sibling Postgres by any route. The
architecture, as built, refused to run a Django application.

`docker_run_argv`'s comment had already named the answer — *"a subject needing
to reach a sibling database container needs an `--internal` network, which is a
standup concern"* — but filed it as somebody else's problem, and a story that
never ran a real workload never noticed it was its own.

## Decision

**`Sandbox` gains a `network` field, and it is not a string.**

`InternalNetwork` is a value type whose constructor runs
`docker network inspect --format {{.Internal}}` and refuses anything that does
not answer `true`. `Sandbox.network` is typed `InternalNetwork | None`, so there
is no name a caller can pass that attaches a workload to the default bridge.
Sixth use of the constructor-as-check idiom in this project.

This keeps S-2.1's AC 3 true rather than trading it away. Measured before the
code was written: a container on an `--internal` network fails to open a socket
to `1.1.1.1` and succeeds in querying a sibling database, in the same run. What
widened is "localhost only" to "this internal network only". Egress did not.

**`create()` verifies rather than trusting the command it just ran.** A name
already taken by a bridged network makes `docker network create` fail while
leaving a perfectly usable — and externally routed — network behind. A test
constructs exactly that situation.

**The database sits on two networks; the workload sits on one.** The second
problem only appeared once the first was fixed: `SnapshotRestoreReset` connects
from the *host*, and a host cannot reach an internal network. So the database
container is created on the default bridge with a published port and then
additionally attached to the internal network under the alias `db`, while the
workload container is on the internal network alone.

The asymmetry is deliberate and worth stating: **the subject's code has no route
off the host; the database, which runs no subject code, is reachable by the
harness that has to reset it.** That is the topology real environment standup
will need, and composing the epic is what found it.

The alias is `db` rather than the container's unique name because the production
guard's default host allowlist contains `db`. A network alias keeps container
names unique and hostnames conventional, which is a nicer answer than widening
the allowlist.

## Consequences

**Makes easy.** Running a real subject at all. Everything Epic 3 needs —
scaling a workload across three orders of magnitude with a reset between each —
requires both halves at once, and that combination now has a passing test.

**Makes hard.** Nothing that was previously easy. The default is unchanged: a
`Sandbox` with no network still gets `--network none`, and every existing test
of that behaviour still passes.

**Rules out.** Attaching a workload to a bridged network, including by accident,
including by a future caller who has a network name in hand and no idea what
`Internal` means.

**Left open.** The database container has egress, because it is on the default
bridge. It runs no subject code, so this is a considered position rather than an
oversight — but a compromised Postgres image would have a route out, and a
deployment that cared could put the harness itself in a container on the
internal network instead of publishing a port.

## Provenance

Written after Epic 2 was declared complete, in response to being asked whether
it was flawless. It was not, and the flaw was structural rather than local.

The brittle test from S-2.1,
`test_there_is_no_field_by_which_isolation_could_be_requested_away`, **failed
when the field was added** — which is exactly what it was written for. It forced
this widening to be argued for rather than slipped in, and it now also asserts
that `network` is annotated `InternalNetwork | None` so a future change to `str`
fails the same way.

Sabotage-verified, each asserting the edit applied. Accepting a non-internal
network fails the bridged-network test. Trusting `docker network create` without
re-inspecting fails the name-collision test. Ignoring the field and always
emitting `--network none` fails the end-to-end test that a workload reaches its
database.

**The general lesson is the one worth keeping.** Nine stories of module tests,
five constructor-enforced safety properties, four sabotage findings, 487 passing
tests — and the epic could not perform its own purpose. Per-module verification
says nothing about composition, and a suite where every file tests one import is
a suite that will not tell you.
