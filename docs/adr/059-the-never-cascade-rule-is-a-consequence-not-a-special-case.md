# 059 — The never-cascade rule is a consequence, not a special case

**Status:** accepted
**Story:** S-5.6 — cascade with escalation logging
**Date:** 2026-08-09

## Context

S-5.6 lists four criteria, and the fourth restates `CLAUDE.md`'s standing
non-negotiable:

> **No cascading on hypothesis generation or attack design** — no validator
> exists for those

Written as its own rule, that is a rule somebody has to remember to apply. For a
non-negotiable, that is the wrong shape.

## Decision — AC 4 falls out of AC 1

AC 1 already says *cheap model attempted first wherever a deterministic validator
exists*. S-5.5 encoded `04-cost.md` §3's table, including the check that makes
each step type safe and the two rows that record *none exists*. So cascade is
gated on `mechanical_check is not None`, and hypothesis generation and attack
design are refused by the same condition that admits the other six. There is no
branch naming them, and adding a step type with no check would be refused without
anybody editing this module.

**A caller may not supply the missing validator.** `cascade` refuses a creative
step type even when handed a `validate` callable. §3's table is the statement that
no *deterministic* check exists; a caller-supplied one is a judgement wearing a
validator's clothes, and accepting it would route the one step nothing can verify
onto a cheap model with every other guard in the system satisfied. If a real
validator is ever built, §3 changes — a code change and a recorded decision.

## Decision — start where S-5.5 routes, escalate one rung

This is what reconciles two things that look like they conflict.

`04-cost.md` §12.3's engineered case lists repair as *cascade mid→frontier*.
S-5.5's AC 4 says mechanical steps never hit the frontier tier by default. Both
hold once *routing* and *escalation* are separated: repair's mechanical work is
**routed** to mid, and frontier is only ever **reached** by escalating after two
failed validations. Mechanical work is never routed to the top; it arrives there
having failed its own check twice.

The rule is uniform — attempt at the routed tier, escalate one rung dearer — and
it reproduces §12.3 exactly at both ends of the table: repair mid→frontier, and
grounding, which §12.3 routes cheap, cheap→mid.

A useful invariant follows: because S-5.5 never routes mechanical work to the top
tier, every cascadable step always has somewhere to escalate to.

## Decision — a rejected result is never returned

`cascade` raises `NoDearerTierError` when the result fails its validator on the
dearest tier available, rather than returning the last attempt. A cascade that
handed back an answer its own validator rejected would make the validator
decorative, and the caller cannot tell a validated result from an unvalidated one
by looking at it. The same applies when a step is configured onto the top tier and
has nowhere to escalate: that is a real failure the caller must handle, not
something to paper over.

## Decision — §3's promotion rule, which the acceptance criteria omit

§3 ends with a fifth instruction the story's AC do not carry:

> Log the escalation rate per step type — if a step escalates more than ~30% of
> the time, promote it permanently.

Above that rate, two cheap attempts plus a dear one cost more than starting dear,
so the cascade is losing money on the step it was meant to save it on.
`promotion_candidates()` is that rule.

**A rate below ten samples is `None`, not a number.** One escalation out of one
attempt is 100%, and promoting on that moves a step type to the dearest model on
a coin flip. Same rule S-4.2 applies to a ratio whose denominator is too small to
divide by.

**The log is two-sided.** `never_escalated()` reports step types that have not
escalated across enough attempts to notice, because that is either the result the
technique exists for — the cheap model genuinely handles the step — or a
validator that cannot fail, which is a cascade checking nothing. The log cannot
distinguish them and says so by reporting the number rather than a verdict.

## Consequences

**This is the fourth Epic 5 story whose acceptance criteria summarize a table
elsewhere and lose part of it.** S-5.3's field list omitted the API's second cache
figure; S-5.4's *exhaustion halts* omitted §7.2's four dispositions; S-5.5's
*class to tier* omitted §12.3's per-phase routing and §3's validator column; S-5.6
omits §3's promotion threshold. In this epic the backlog is the summary and
`04-cost.md` is the specification — worth expecting rather than rediscovering,
and now recorded twice.

**S-5.5's decision to keep the validator's *name* pays off here.** A boolean would
have been enough to gate the cascade, but the refusal messages, `cascadable()`
and the promotion report all quote the check by name — which is what lets a
reader verify a routing decision against §3 rather than trusting it.

**Sabotage-verified on twenty-one properties, all caught.**
