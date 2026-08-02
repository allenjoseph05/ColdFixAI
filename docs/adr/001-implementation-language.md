# 001 — Implementation language: Python 3.12+

**Status:** accepted
**Date:** 2026-08-02

## Context

The core has to do three things that constrain the choice: instrument a running
program from the inside, replace a function in a live process to measure what it
cost, and read a database driver's query log. All three are language-level
capabilities, not library features.

The first adapter targets Django, so the subject is Python. That does not by
itself force the *core* to be Python — a core in another language could drive a
Python subject over a protocol — but it means every instrumentation hook would
have to cross that boundary.

## Decision

Python 3.12+ for the entire core. `mypy` strict, `ruff`, `pytest`.

Cross-language support arrives through MCP in E14, not by writing the core
twice. Per the project's own rule, MCP does not get built until a second adapter
exists in another language; until then it is overhead.

## Consequences

**Makes easy.** The E0 spikes are the evidence. S-0.4 replaced
`ListSerializer.to_representation` in a running process, guarded on
`field_name`, and toggled it between consecutive requests — a three-line change
that measured a 1020 ms component. S-0.5 read and restored Postgres sequence
state through the subject's own ORM. ADR 008's `force_debug_cursor` is a Django
internal reached directly. None of that survives a process boundary intact: a
cross-language core would need an agent inside the subject, and that agent would
be Python anyway.

**Makes hard.** The core inherits Python's weaknesses in the one place they
matter — the measurement path. `time.perf_counter` is fine, but S-0.4 found
`time.sleep` carrying 80–100 µs of syscall overhead per call, which set the
floor on how small an injected delay could be. Any future primitive needing
sub-millisecond resolution has to account for the interpreter, not just the
subject.

Type safety is opt-in and enforced by tooling rather than the runtime. `mypy`
strict is therefore not a style preference here; it is the substitute for a
compiler, and the exclusions in `pyproject.toml` are deliberate and narrow.

**Rules out.** A core that runs where the subject cannot — an embedded target,
a language without runtime patching. That is consistent with the scope in
`00-BRIEF.md` §3, which already excludes embedded firmware and kernels.

## Provenance

`docs/10-BACKLOG.md` S-0.1 notes. Validated by every E0 spike: the ablation
mechanism, the reset mechanism, and the query counter are all Python-native.
