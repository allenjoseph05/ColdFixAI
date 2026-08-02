# 008 — Query counting uses `force_debug_cursor`, never `settings.DEBUG`

**Status:** accepted
**Date:** 2026-08-02

Numbered 008 because 001–007 are reserved for S-0.2.

## Context

Counting SQL queries per request is a lab-bench primitive, and the obvious way
to do it in Django is to set `settings.DEBUG = True` so that
`connection.queries` is populated, then read it after the request.

S-0.3 established that this destroys at least one real target. NetBox appends
`debug_toolbar.urls` to its URLconf conditionally on `DEBUG`, evaluated at import
time. Flipping `DEBUG` after startup causes the URLconf to import an application
that is not in `INSTALLED_APPS`, and the request dies with:

```
RuntimeError: Model class debug_toolbar.models.HistoryEntry doesn't declare
an explicit app_label and isn't in an application in INSTALLED_APPS.
```

The failure is not specific to `django-debug-toolbar`. `DEBUG` is a settings
flag that projects branch on at import time — for URLconfs, middleware,
static-file handling, template loaders and logging — and Django itself treats it
as fixed for the process lifetime. Mutating it post-startup is outside its
contract.

What makes this worth an ADR rather than a bug fix is the failure distribution:
the naive approach **worked on two of the three repositories** in S-0.3 and
broke the third. That is the profile of a defect that ships, survives the test
suite, and is later misattributed to the target repository rather than to the
harness.

## Decision

The query-counting primitive sets `connection.force_debug_cursor = True` and
reads `connection.queries`, restoring the previous value afterwards. It never
reads, writes, or requires any particular value of `settings.DEBUG`.

`force_debug_cursor` is the same switch Django's own `assertNumQueries` and
`CaptureQueriesContext` use. It affects only the cursor wrapper, so no
application code observes it and no import-time branch is re-evaluated.

This is a constraint on the harness, not advice to an agent. It is not
overridable per target.

## Consequences

**Makes easy.** Query counting works against a process started in its normal
configuration, so measurements are taken against the same settings the
application actually runs under. That matters directly for fidelity: a
measurement taken under `DEBUG = True` is not a measurement of production
behaviour, since `DEBUG` changes template loading, static-file serving and error
handling. Avoiding the flag removes a systematic distortion as well as a crash.

**Makes hard.** `connection.queries` is per-connection, so a target using
multiple database aliases or a connection pool needs the flag set on each
connection under measurement. `CONN_MAX_AGE` is non-zero on at least one S-0.3
target, so the restore step has to be reliable — a connection left with
`force_debug_cursor = True` leaks unbounded query text into memory for the life
of the process. The primitive therefore restores in a `finally`, and this is a
property worth an adversarial test per the project's testing rules.

**Rules out.** Any measurement that genuinely needs `DEBUG = True` — Django only
records query *timings* in `connection.queries` under the debug cursor, which
`force_debug_cursor` does provide, so nothing needed is actually lost here. But
it does rule out reusing `django-debug-toolbar` as a measurement source, since
that requires being in `INSTALLED_APPS` at startup and is a per-request HTML
panel rather than a programmatic counter. Consistent with the project rule that
counting is code, not an integration.

## Provenance

`spikes/S-0.3-grounding/FINDINGS.md`, repository C (`netbox` 4.6.7), section
"Consequence for the lab bench". The same section records a related measurement
caveat that belongs to S-0.4 rather than here: consecutive identical requests
differed by four queries purely from cache warmth, so ablation deltas below that
threshold are noise.
