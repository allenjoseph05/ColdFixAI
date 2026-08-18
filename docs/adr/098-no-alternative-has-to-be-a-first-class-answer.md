# 098 — "No alternative" has to be a first-class answer

**Status:** accepted
**Story:** S-9.5 — alternative explanation attack
**Date:** 2026-08-17

## Context

*Proposes a different mechanism consistent with the same measurements. If one
exists and was not excluded, verdict is `unsound`.*

This is the first attack in Epic 9 that genuinely needs a model. S-9.2, S-9.3 and
S-9.4 turned out to be arithmetic — which axes were varied, how wide the span
was, what r² came back. *Is there another story these numbers would also tell* is
not computable, and `08-audit.md` names it as the flaw schema validation cannot
reach:

> "No finding without a measurement" prevents fabrication. It does not prevent a
> correct measurement supporting a wrong conclusion.

## Decision

### The empty answer is the design

AC 2 turns **any** alternative into `unsound`, and the amended S-9.8 routes
`unsound` back to investigate. So an auditor that cannot say *I have nothing*
guarantees an investigation never ends — which is S-0.8's measured failure,
reached through the audit instead of through the agent. An attack that always
finds something is not merely useless here; it is the failure mode.

So the empty answer is built for rather than tolerated:

- the prompt offers it explicitly, with the words *that is a result, not a
  failure* and *the right answer whenever you have to strain to find one*,
  because a model that has to invent the escape hatch will not use it;
- the parser treats it as ordinary rather than exceptional, and accepts it in any
  case;
- the report says *this attack passing, not this attack failing to run*, because
  a reader who cannot tell those apart will treat a clean finding as an
  incomplete audit.

### The judgement is the model's; the citations are checked

An alternative that quotes a figure nobody measured is not *consistent with the
same measurements* — it is a story that happens to mention numbers. Every figure
it rests on is checked against what the harness recorded, which is the discipline
S-8.3 applies to a verdict and the same non-negotiable underneath both.

**Checked against every experiment, not against one, and this is why it does not
reuse `check_citations`.** That function compares one mapping to one measurement.
A log holds many, and the same metric legitimately takes different values in
different experiments — `db.query` is 7 in the sweep that ruled the database out
and 1004 in the ablation that found the cause. Reusing it here would call one of
two real numbers a fabrication. `measured_pairs` collects every value each metric
took, and a citation is valid if it matches any of them.

That is a case where the *correct* move was to write a second checker rather than
share one, and the reason is recorded so it does not read as duplication somebody
should tidy up.

### "Not excluded" is argued by the auditor, not decided by the code

AC 2 makes a finding unsound only when an alternative was *not excluded*, and
deciding whether exclusion X covers alternative Y is the same semantic judgement
this whole story exists because code cannot make. So the auditor is shown the
rejections — S-9.1's evidence already carries them, verdicts included — and must
say which one fails to cover its proposal.

An alternative offered without that argument is refused. *There might be another
explanation*, with no account of why the existing experiments missed it, is not
an objection anybody can act on.

### A malformed answer is an absent one

`ATTACK_DESIGN` cannot cascade — §3 records that no deterministic validator
exists for designing an attack — so there is no cheap retry to fall back on and a
malformed reply raises rather than being rejected and re-asked. Same line ADR 085
drew, on the other side of it.

## Sabotage

Sixteen properties, all caught, no survivors. The pair that matters is *the empty
answer stops being recognised* against *every answer reads as no alternative*:
this attack can fail in both directions, and the first is the one ADR 094 was
written about.
