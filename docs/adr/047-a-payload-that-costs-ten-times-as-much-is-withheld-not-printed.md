# 047 — A payload that costs ten times as much is withheld, not printed

**Status:** accepted
**Story:** S-3.17 — input space search
**Date:** 2026-08-08

## Context

S-3.17's fourth acceptance criterion:

> Findings involving denial-of-service potential are flagged for different disclosure handling

`01-primitives.md` §14 says why — "ReDoS is a denial-of-service vector, not merely
a slowness bug" — but neither says which findings those are, or what "different
handling" is in code. Both had to be decided.

Two things make an input a denial-of-service vector rather than a function that
is slower than it ought to be:

1. **Asymmetry.** The sender spends what an ordinary sender spends; the subject
   spends far more. A slow endpoint that is slow for everyone is a performance
   problem. One that is slow only for a particular 100 bytes is an attack.
2. **Reachability**, which this primitive already gates on:
   `PARSES_UNTRUSTED_INPUT`. Nobody attacks a subject whose inputs they cannot
   choose.

Asymmetry is the measurable one, and measuring it requires a control that
`01-primitives.md` §14 names: this primitive varies *which* input, so the
comparison has to hold *how much* constant. Against the whole population, "40×
the median" would be reported for an input that was simply the largest one tried
— which is `scale_volume`'s finding, answered better by `scale_volume`.

## Decision

**The denominator is the median cost of the inputs that are the same size as the
champion**, within ±20%, excluding the champion itself. Below five such peers
there is no median worth taking — the median of two numbers is one of them — and
the disclosure state is `UNDETERMINED` rather than `ORDINARY`.

Excluding the champion is not tidiness. Leaving it in puts the largest value into
its own denominator, which raises the median and understates every asymmetry — an
error in the direction of *not* reporting a vulnerability.

**The threshold is 10×: an order of magnitude for the same number of bytes
sent.** It is a screening flag for how a finding is handled, not a severity
rating. Measured against the canonical subject, insertion sort came back at
1.06× — correctly, since its worst case is roughly twice its average for the same
size, which is a quadratic algorithm and not an attack.

**Different handling is performed, not described.** `report()` builds its string
without the payload unless the state is `ORDINARY`; `witness()` is the one named
way to obtain it; `Candidate.payload` carries `repr=False` so an incidentally
logged candidate does not put a working exploit into a traceback or an experiment
record.

**Three states, and it fails closed.** `UNDETERMINED` withholds the payload as
`RESTRICTED` does. The case where nobody has established that an input is safe to
circulate is not the case to print it in.

## Consequences

A campaign over payloads with no length — integers, floats — is always
`UNDETERMINED`, because size cannot be held constant when the payload has no
notion of size. `_size_of` returns `None` rather than 0 or 1 for exactly this
reason: a fabricated size would make such a campaign look as if it had a control.

The 10× threshold will miss a genuine vector whose amplification is 8×, and will
flag a merely bad algorithm that happens to reach 10×. Both are the ordinary cost
of a threshold, and both are visible: `amplification` is on the campaign, and the
report states the ratio and its denominator so a reader can disagree with the
line rather than with an unexplained label.

Nothing here decides severity, assigns a CVE, or contacts anybody. It routes a
finding to a different queue and declines to print the exploit on the way.
