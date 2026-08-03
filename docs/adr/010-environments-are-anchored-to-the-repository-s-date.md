# 010 — Environments are anchored to the repository's own date

**Status:** accepted
**Date:** 2026-08-02

## Context

S-0.3 selected three Django repositories and grounded all three. It also recorded
a sampling bias, before the runs started and again in the verdict:

> All three were committed to within three days of selection. Nothing here
> represents an abandoned repository with stale pinned dependencies against a
> Python version that no longer builds — a realistic case, and probably a harder
> one. Treat the verdict as bounded to maintained projects.

That bound is load-bearing. Two of the recurrence matrix's rows — *Python version
mismatch* and *dependency resolution failure* — came back empty, and they came
back empty substantially because of how the sample was drawn. All three repos ran
on Python 3.12 with no version friction at all.

The failure being excluded is specific and common. A repository last touched in
2019 declares `django>=2.0`; resolved today that yields Django 6, which its code
does not run on. Or it pins `psycopg2==2.7`, which does not build against a
current Python. The repository is not broken — it worked on the day it was
written. It breaks because we hand it a 2026 toolchain.

Framed that way it is not an unbounded variety problem. It is a *time* problem,
and time is a parameter we can read.

## Decision

Every environment E2 stands up is anchored to a date derived from the repository
itself — its most recent commit — and both the interpreter and the package index
are constrained to that anchor.

- **Dependency resolution** passes the anchor to the resolver, so candidate
  packages are limited to those published on or before it. `uv` supports this
  directly via `--exclude-newer` (and `UV_EXCLUDE_NEWER`), so an unpinned
  `django>=2.0` resolves to what it would have resolved to on the anchor date.
- **The interpreter version** is read from the repository's own declarations —
  `python_requires`, trove classifiers, `tox.ini`, CI workflow matrices — and the
  newest version the repository claims to support is fetched. `uv` fetches
  arbitrary CPython versions, so this costs a download rather than a system
  change.
- **The anchor is recorded in the workload artifact**, alongside the fixture
  recipe. A measurement taken under a resolved dependency set is only reproducible
  if the resolution inputs are captured.

The anchor is derived mechanically. No agent decides it and there is no new
obstacle category: `git log -1` and a manifest read are both deterministic.

## Consequences

**Makes easy.** An abandoned repository stops being a special case and becomes a
different value of an existing parameter. The two empty rows in S-0.3's recurrence
matrix are addressed before they are ever populated, which is cheaper than
discovering them on a user's repository.

Reproducibility improves for maintained repositories too, not only stale ones.
S-0.4 found that guard counters reproduce byte-identically while timings drift;
that guarantee is void if a rerun silently resolves a different dependency set.
Anchoring makes the dependency set a recorded input rather than a function of
when the tool happened to run.

**Makes hard, and this bound is real.** Anchoring covers the Python layer only.
An old `psycopg2` needing a `libpq` that current Debian no longer ships is an
operating-system problem, and `apt` has no equivalent of `--exclude-newer`. The
residue lands on the base image, which can be era-matched approximately but not
precisely. **This decision reduces the stale-repository failure class; it does
not eliminate it**, and the remainder belongs in S-17.2's honest limitations
rather than being described as solved.

Anchoring can also *introduce* failures. A dependency version contemporary with
the repository may carry a since-fixed incompatibility with a newer transitive
package, or a known security defect. The anchor is therefore a default, not a
constraint: it must be overridable per run, and an override must be recorded in
the artifact for the same reason the anchor is.

**Rules out.** Resolving dependencies against "latest" and treating whatever
comes back as the repository's environment. That is the current implicit
behaviour, it works only for actively maintained projects, and S-0.3's sample
could not have detected the problem.

## Provenance

`spikes/S-0.3-grounding/FINDINGS.md` — "Known bias in this sample", and the two
empty rows of the recurrence matrix. `uv --exclude-newer` and `uv python install`
verified available in this project's toolchain on 2026-08-02.
