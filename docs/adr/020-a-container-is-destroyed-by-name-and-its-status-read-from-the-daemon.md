# 020 — A container is destroyed by name, and its status is read from the daemon

**Status:** accepted
**Date:** 2026-08-06

## Context

ADR 004 chose Docker and stated the properties wanted from it. It did not say
what the invocation looks like, and S-2.1's five acceptance criteria are all
statements about exactly that. Two of them turn out not to be satisfied by the
obvious construction.

**`docker run --rm` does not destroy the container after each run.** The CLI is
a client; the workload runs under the daemon. `--rm` fires when the client exits
cleanly — the case that was never in doubt. When `execute()` reaches its timeout
and kills the client's process tree, the container is untouched: it keeps
running, keeps holding the workspace bind mount, and keeps consuming the CPU
that every later measurement in the investigation is taken against. This is
precisely the orphan problem S-1.1 was careful about, moved one level up, and
S-1.1's process-group kill cannot see it because the workload was never a
process on this host.

**An exit code does not say whether a limit was enforced.** A container killed
for exceeding `--memory` reports SIGKILL, which is indistinguishable from any
other SIGKILL. Separately, `docker run` exits 125 when docker itself fails —
a missing image, an unparsed flag, no daemon — and 125 is also a legal exit code
for a workload, so a bad image and a workload that exited 125 are the same
observation at the CLI.

## Decision

**The container is named, and removed by name in a `finally`.** `--rm` is not
used. Every path out of a run — success, non-zero exit, timeout, OOM kill,
docker's own failure — passes through `docker rm --force --volumes`. A removal
that fails raises `ContainerNotDestroyedError`, including when another exception
is already propagating, where the two are chained and both are reported. A
container that outlived its run is the loudest failure this module has.

**The exit status is read back with `docker inspect` before removal**, and the
result carries that rather than the client's. The same call answers both
ambiguities: `.State.OOMKilled` distinguishes a memory kill from any other kill,
and the *absence* of the container distinguishes "docker never started one"
from "the workload exited 125". Neither is inferred from the text of docker's
stderr, which is carried through verbatim instead of classified.

**An out-of-memory kill raises rather than returns.** A truncated run is not a
measurement: its timing, its query counts and its output all describe a workload
that stopped part-way, and none of them look partial. Returned as an ordinary
non-zero exit it would be compared against a complete run. This is the same rule
as `ExecutionTimeoutError` — carry the partial output, refuse to call it a
result.

**`--memory-swap` is set equal to `--memory`.** Docker's default grants swap at
twice the limit, under which a workload over its cap gets slow instead of
getting killed — and a slow workload is a measurement this system would report
as a finding.

**`--pull never`.** An image fetched on first use spends the run's timeout on a
download, makes the first measurement of a session incomparable with the rest,
and reintroduces the network on the host side where `--network none` cannot see
it.

**The policy is not parameterised.** `Sandbox` has three fields — image,
workspace, limits — and no argument that enables networking, adds a second bind
mount, or lifts the read-only root. Each of those is an acceptance criterion
rather than a preference. A test asserts the field set itself, so widening it
fails a test rather than merging quietly.

**`--network none` is read as satisfying "localhost only".** That mode leaves a
loopback interface and nothing else: no bridge, no DNS, no route off the host. A
subject that must reach a sibling database container needs an `--internal`
network, which belongs to environment standup and must not be reachable by
widening this flag.

## Consequences

**Makes easy.** Every criterion in the story is a constant in one pure function,
`docker_run_argv`, which can be asserted against exhaustively without a daemon,
a network, or a machine willing to be filled with memory. The integration tests
prove the flags mean what docker documents; the fast tests prove they are always
there, on any machine, in under a second.

**Makes hard.** Three docker invocations per run instead of one — `run`,
`inspect`, `rm`. On a local daemon that is tens of milliseconds against a
workload measured in seconds, and it buys the only unambiguous answer to "was
this run complete?". Caching or reusing containers to avoid it is the exact
class of change this system flags in other people's code, and would reintroduce
state carried from one experiment into the next one's measurement.

**Rules out.** Reusing a container across runs. Falling back to an unsandboxed
execution when no daemon is listening — `docker_available()` exists for skipping
tests and is documented as never being control flow, because the fallback would
be running the subject on the host and there is nowhere else to run it.

**Left open.** `--cpus` is a quota, not a pinning. Quotas are what the AC asks
for and what bounds damage; they also introduce scheduler variance that
`--cpuset-cpus` would avoid. Whether that variance is visible above the noise
floor is a question for a certification against a real workload, not a guess to
build on now.

The container runs as the image's default user, which for `python:3.12-slim` is
root. On a Linux host that leaves root-owned files in the bind-mounted
workspace, which the harness then cannot clean up. Worktree lifecycle is S-2.2
and the standup that chooses images is S-7.2; this is recorded so that whichever
arrives first owns it rather than discovering it.

## Provenance

`docs/10-BACKLOG.md` S-2.1 acceptance criteria; ADR 004 for the choice of Docker
and for the threat model — an isolation boundary against accidents, not a
security boundary against hostile code — which is why `--cap-drop ALL` and
`--security-opt no-new-privileges` are included as free hardening while `noexec`
on `/tmp` is not, since pip and several build backends execute from the temp
directory.

The `--` separator before the image is the one adversarial detail: without it, a
workload invoked with `--network host` or `-v /:/host` would be parsed by docker
as options to `docker run` and would rewrite the policy above it.
`test_the_workload_cannot_rewrite_the_policy_through_its_own_arguments` attempts
exactly that and asserts it lands in the workload's argument list instead.
