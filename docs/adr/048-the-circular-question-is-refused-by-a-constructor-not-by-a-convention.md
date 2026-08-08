# 048 — The circular question is refused by a constructor, not by a convention

**Status:** accepted
**Story:** S-3.18 — bound comparison
**Date:** 2026-08-08

## Context

`08-audit.md` F8 cut this primitive down before it was written: "how many queries
must this endpoint issue" is a question about intent, and an agent able to answer
it would already know the fix. F8 keeps three bounds — bytes that must be read,
rows the response schema requires, instructions a reference implementation
retires — and drops semantic minimums for arbitrary business logic.

What F8 does not say is *how* the drop is enforced, and the difference matters.
Restricting the helpers to the three computable cases leaves the circular answer
one line of arithmetic away: nothing stops a caller dividing a query count by a
number they made up and calling the result headroom. A primitive whose central
claim is "this question cannot be answered honestly" cannot leave the dishonest
answer available.

Three further things had to be decided, none of them in the documents: what
counts as *enough* headroom to be worth an investigation, what happens when no
bound is computable, and what happens when a measurement lands below its floor.

## Decision

**`Bound.__post_init__` refuses a floor on any metric whose minimum is
semantic**, by name, with the reason. `db.query`, `http.request`, `file.open` and
the three blocked-time counters are on the list; the message for `db.query`
points at `db.rows`, which *is* computable from the response the workload
returned. This is the project's recurring construction — an ordering or
permission requirement becomes a type whose constructor performs the check — used
here so that skipping the helper does not skip the refusal.

**The threshold is a factor of the measurement, not a percentage of the bound.**
`WORTH_INVESTIGATING = 1.5`: the most a perfect fix could win. A factor is what
an investigation is deciding about, and 1.5× means an *optimistic* ceiling of a
33% improvement, of which a real fix gets a fraction, against the ~6% timing
noise floor S-0.4 measured on a 350ms endpoint. `01-primitives.md` §13's own
example — 76% of bound, so 1.32× available — is the case it calls "nothing left",
which puts the line in the right place.

**No computable bound is a `Screening` with no comparisons, not an exception.**
F8's consequence is that this is the *ordinary* case. An optional check that
raised on the common path would be wrapped in a `try` and then switched off, and
it would then not run on the workloads where a bound does exist either. The
report says the check said nothing, and repeats F8's own sentence about applying
opportunistically rather than as a universal pre-check.

**A measurement below its floor raises.** A bound claims the work cannot be done
for less; a measurement under it falsifies the claim. The useful output is that
one of the two inputs is broken, never an efficiency above 100%.

**A floor of zero yields `None`, not infinity**, and `worth_investigating`
defaults to `True` wherever no factor could be computed. A headroom check that
cannot compute a factor must not be the reason nobody looked.

## Consequences

The primitive is registered with no capability requirement and no project-fact
gate, because there is no fact that decides it: whether a computable floor exists
is a property of the individual workload, and `screen` is what answers it. That
makes it the only primitive in the set offered unconditionally, which is correct
for a check that costs one division over a measurement already taken.

`INSTRUCTIONS` is named here before S-3.19 exists to measure it. The bound is
computable before the instrument is — a hand-written reference implementation's
retired-instruction count is a floor whether or not anything is counting the
subject yet — and `screen` reports the metric as unbounded until S-3.19 produces
it.

The honest summary of this primitive is small: on a Django endpoint it can floor
rows and nothing else, and the thing actually wrong with an N+1 is the query
count, which is exactly what cannot be floored. The row bound still catches it
indirectly — 200 rows returned where 103 are required — but that is luck about
this defect's shape rather than coverage. F8's consequence stands: screening in
the general case is scaling plus flat-cost detection, and this is an
opportunistic extra.
