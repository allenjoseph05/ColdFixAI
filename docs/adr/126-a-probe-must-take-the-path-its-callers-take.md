# 126 — A probe must take the path its callers take

**Status:** accepted
**Date:** 2026-08-20

## Context

The fast test subset had grown to 17½ minutes with five failures, and the question
asked was whether to parallelise it.

`pytest-xdist` was the obvious answer and the wrong one. Eight test files measure
real durations — `bench/test_timing.py`, `test_interleaving.py`,
`test_certification.py`, `audit/test_scales.py`, `explorer/test_standup.py` among
them — and running them under the load of other workers would corrupt them
systematically. That is the same argument this project makes for why the system
itself does not parallelise measurement, and it applies to the suite for the same
reason.

Measuring first gave a better answer: **four tests accounted for eight of the
seventeen minutes**, and all four were failures rather than slow passes.

## Decisions

### 1. A missing image is not a slow test

`pyproject.toml` keeps the `docker` marker separate from `slow` because *a slow
test is one you choose not to wait for, a docker test is one this machine cannot
run at all*. A daemon that is up will accept `docker run alpine` and then spend the
whole timeout pulling from a network that may be slow, metered or absent — which is
the second of those wearing the costume of the first.

`fixtures/containers.require_image` skips with the `docker pull` that would fix it.
Two tests went from 120-second timeouts to instant skips. **Nothing pulls**: a
fixture that fetched hundreds of megabytes on a first run would turn `pytest` into
a download, and its failure mode would be a timeout rather than a skip.

The image names are module constants, so the check and the `docker run` cannot
drift — a check against one name and a run against another skips nothing and still
times out.

### 2. A probe must take the path its callers take

`docker_available()` ran `docker version --format ...`. Three measurements on one
degraded Docker Desktop:

| command | result |
|---|---|
| `docker version --format ...` | under a second |
| `docker ps --quiet` | one second |
| `docker ps --filter name=... --format ...` | still running at ninety |

A version handshake is answered by the API layer and says nothing about the
container store. `--quiet` reaches the store and **still** misses it. Only the
filtered, formatted form — the one `standup.diagnose` actually calls — hangs.

So the probe now runs that shape, against a name that cannot match anything. A
check that does not exercise the path its callers take keeps reporting a daemon
healthy right up until each caller spends its whole timeout finding out otherwise
— and **a timeout is a failing test**: it says *your code is broken* about somebody
else's daemon.

### 3. The probe gets its own short timeout

Ten seconds, not the sixty the housekeeping operations get. This runs once per test
that needs Docker, so the cost of *discovering* a broken daemon is paid over and
over, and a probe that waited a minute would turn a skipped suite into a slow one.
A healthy daemon answers the same command in about a second.

### 4. Checking the image is not checking the daemon

`test_off_cpu.py::test_the_measurement_works_inside_the_container_sandbox` checked
that its image was present and never asked whether the daemon was usable. So it
built its container, took its measurement, and failed in **teardown** when
`docker rm --force --volumes` blew past sixty seconds — reported as
`ContainerNotDestroyedError`.

That error is meant to be loud, and it stays loud: S-2.2 makes a container
outliving its run a failure because it holds the deliberately-broken ablation
source ADR 004 requires be incapable of shipping. **Nothing about it was
weakened.** What changed is that the test now asks `docker_available()` first, so
on a daemon that cannot clean up it never starts a container to strand — the same
guard every other Docker test already had.

The image check answers *can this container start*. It says nothing about whether
anything can be removed afterwards, and the two are different questions.

## Consequences
`tests/explorer/test_standup.py` went from **443 seconds with three failures** to
**58 seconds with none** — 12 passed, 4 skipped. `test_off_cpu.py` went from 181
seconds and a failure to 18 seconds for the whole file.

**The whole fast subset**, from the same machine on the same afternoon:

| | before | after |
|---|---|---|
| runtime | 17 min 31 s | ~10 min |
| failures | 5 | 0 |
| skips | 3 | 8 |

Every one of the five failures was a healthy test on an unhealthy daemon.


**The generalisable finding is the probe, not the images.** Any check of the form
*is X available* that uses a cheaper call than its callers do will report available
and hand them a hang. The cheap call is the tempting one precisely because it is
cheap.
