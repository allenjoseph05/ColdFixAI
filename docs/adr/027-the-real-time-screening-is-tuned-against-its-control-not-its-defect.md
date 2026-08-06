# 027 — The real-time screening is tuned against its control, not its defect

**Status:** accepted
**Date:** 2026-08-06

## Context

S-2.8 asks the system to detect RTOS imports, deadline annotations,
safety-certification markers and real-time framework signatures, refuse on
detection with a one-paragraph explanation, run the check before grounding, and
prove it on a fixture.

`CLAUDE.md` and ADR 007 both single this category out: it is *the only category
where running the system could make things worse while reporting success*. The
reason is specific and worth restating, because it is what makes this a refusal
rather than a caveat — **a caching optimisation improves every metric this
system measures while degrading worst-case timing.** The tool would report a
confident, verified, correct-looking improvement that makes the system less
safe, and every check downstream would agree, because every check downstream
measures the average case.

## Decision

**Detecting real-time systems is easy; not refusing Django applications is the
whole problem.** That inversion is the substance of this ADR.

This tool's pinned development target is a helpdesk application. `deadline` is
an ordinary field name in half the task trackers ever written. `scheduler`,
`priority`, `critical`, `real-time` and `safety` are ordinary English, and they
appear in exactly the software this system exists to make faster. A detector
keying on those words refuses its own target on the first day and is worse than
no detector, because it fails in the direction that makes the tool useless while
looking diligent.

So **every pattern is anchored to a token that does not occur in ordinary
application code**: `SCHED_DEADLINE` rather than `deadline`, `IEC 61508` rather
than `safety`, `\bSIL[- ]?[1-4]\b` rather than `SIL` — the last because a bare
`SIL` matches *silicon*, *silent* and `SILENCE_DEPRECATION`.

**The fixture is a pair, and the control is the load-bearing half.** ADR 006's
rule is that every defect carries a control or the detector learns to say yes.
`flight_controller` plants markers in all four categories. `task_tracker` is an
ordinary web application deliberately packed with every tempting word at once,
and it must be **cleared**. A third test asserts the control still contains
those words, because the way the second claim quietly stops meaning anything is
somebody tidying the vocabulary out of the fixture rather than the detector
changing.

**Detection runs before grounding because grounding will require a
`ScreenedRepository`, and screening is the only thing that makes one.** Not a
rule about call order that a later story could get wrong — there is no
unscreened repository object for grounding to accept. Fourth use of this
construction, after `VerifiedDatabase`, the session types and `VerifiedReset`.

**An incomplete scan is not a clear one.** A repository too large to finish
scanning is refused certification rather than reported clean. For a check whose
failure mode is degrading a safety-critical system while reporting success,
"nothing was found" and "we stopped looking" must never be the same answer.

**`screen()` reports and `ScreenedRepository` decides.** A developer checking
whether a pattern is over-broad needs the evidence, not an exception, and the
refusal carries the file and line of everything it matched — a refusal nobody
can audit is one that gets worked around.

**Generated trees are skipped; vendored ones are not.** `node_modules` and
`.venv` are excluded. `vendor` and `third_party` are deliberately not: a
vendored RTOS is precisely what is being looked for, and third-party code being
unpatchable (S-2.9) does not make it undetectable.

## Consequences

**Makes easy.** Auditing a refusal — it names the marker, the category, the file
and the line. Adding a marker: one entry in a tuple, and the control fixture
immediately says whether it was too broad.

**Makes hard.** Detecting a real-time system that names nothing. A repository
with hard timing requirements expressed only in a specification document this
scan never reads is not detected, and that is a real gap rather than a
theoretical one. The refusal is evidence-based, so a system with no evidence in
its source passes.

**Rules out.** Keying on ordinary English. Every proposal to add `deadline`,
`latency`, `jitter` or `realtime` as a marker has to explain how the control
fixture still clears.

**Left open, and slightly funny: this repository would refuse itself.**
`realtime.py` holds every pattern as a literal and the fixtures plant markers on
purpose, so screening ColdFix's own root detects dozens of markers. That is
correct behaviour for an evidence-based detector rather than a bug, and the test
that checks the tool's own source screens clean is pointed at
`src/coldfix/bench` for exactly this reason. It is recorded so nobody
"fixes" it by adding a self-exemption, which would be a hole any repository
could use by naming a directory `coldfix`.

## Provenance

`docs/10-BACKLOG.md` S-2.8 and its note; ADR 007's real-time section for the two
reasons and their ordering; ADR 006 for the control rule; ADR 011 for the pinned
development target that the control fixture is modelled on.

Sabotage-verified on five properties, each asserting the edit applied.
Loosening the deadline pattern to a bare `\bdeadline\b` fails 4 tests including
the control — the single most valuable result here, because it is the mistake
that would have shipped. Matching `SIL` without its integrity number fails 3, on
*silicon*, *silent* and `SILENCE_DEPRECATION`. Treating a truncated scan as
clear fails 1. Screening without refusing fails 3. Scanning binary files fails 1.
