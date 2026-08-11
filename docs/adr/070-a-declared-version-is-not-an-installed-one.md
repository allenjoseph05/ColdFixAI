# 070 — A declared version is not an installed one

**Status:** accepted
**Story:** S-7.1 — framework fingerprinting
**Date:** 2026-08-11

## Context

The Explorer's first act, and the thing S-13.1 keys playbooks on. Three
acceptance criteria: detect framework, version, ORM, database and test runner
from manifests and imports; return a structured fingerprint; and produce an
honest *unsupported* for anything else.

Nothing here calls a model. Reading `pyproject.toml` and looking for `manage.py`
is a function, and `CLAUDE.md` forbids replacing one with a model call.

## Decision

### The version is reported as *declared*, because that is all a manifest holds

A manifest says `django>=5.0`. That is a **constraint**, not a version: what is
importable could be 5.0 or 5.2, and on a project with a lockfile it is whatever
the lock pinned rather than either. Recording it as "the version" would put a
number in the fingerprint that nothing measured — the exact shape of claim this
project's first non-negotiable exists to prevent.

So the field is `declared_version`, `describe()` says so in words, and the
installed version is left to S-7.2, which is the story that stands the
environment up and can ask it.

### Identified-and-unsupported is a different answer from unknown

*This is Flask, which is not supported yet* sends somebody to the roadmap.
*Nothing here looks like a web application* sends them to check they pointed at
the right directory. Both are refusals; only one is a mystery, and they call for
different actions — so `Unsupported` carries what it **did** identify, and
`Framework` names frameworks this system cannot ground. Naming is not supporting.

The two outcomes are exclusive by construction: `fingerprint` returns
`Fingerprint | Unsupported`, and a `Fingerprint` is only ever built for a
supported framework. S-4.5's rule — a healthy-looking result with an empty field
reads as success at every call site.

### Every facet carries the file that established it

A fingerprint keys playbooks. A key nobody can trace back to a file is one nobody
can check when the playbook it selected turns out to be wrong, so `Detected`
pairs each value with its evidence and a test asserts every facet has some.

This also lets the fingerprint distinguish strengths of evidence rather than
flattening them. `psycopg` in the dependencies says Postgres is *possible*;
`ENGINE: django.db.backends.postgresql` in a settings file says it is what runs.
The settings file wins, and where only the driver is found the evidence says
**"a driver, not a configured engine"** rather than claiming more than it saw.
`manage.py` beats a dependency list for the same reason: a project can depend on
Django for one management command, but only an application has `manage.py`.

### A facet nothing establishes is `None`

A project with no declared test runner is not a project that uses `unittest`; it
is one whose test runner this cannot see. `undetermined` enumerates them so the
gaps are reportable, and S-7.2 knows what to go and find out.

### The playbook key is framework and **major** version

A playbook learned against Django 5.0 applies to 5.0.3. Keying on the full
version would make every patch release a cold start, and S-13.5 measures the
learning curve by how much a playbook saves on the tenth project of a kind — ten
projects keyed differently share nothing and the curve never bends.

A constraint with no determinable floor keys as `unversioned` rather than being
filed under a version nobody established.

## Consequences

**Makes easy.** S-7.2 gets a starting point with its gaps named. S-13.1 gets a
stable key. A user pointed at the wrong directory gets told which of the two
things went wrong.

**Makes hard.** Detection is manifest-shaped, so a project that declares nothing
and vendors its dependencies fingerprints as unknown. That is the honest answer
and the alternative — importing the project to find out — is S-7.2's job and
needs an environment.

**Rules out.** A fingerprint holding an unsupported framework, a version field
that conflates declared with installed, and a facet defaulted rather than left
open.

**Sabotage-verified on fourteen properties, all caught.** Two needed the
*sabotage* rewritten rather than the code or the test: defaulting `Detected`'s
evidence field changed no call site, because every construction passes one, so it
proved nothing until it was rewritten to make a real detector stop recording its
source; and the poetry pattern never matched the file. A sabotage that changes
nothing is not evidence of anything, which is the same failure the harness itself
had in the last two composition checks.
