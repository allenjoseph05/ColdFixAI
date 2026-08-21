# 131 — Two gates, two questions, and one master switch

**Status:** accepted
**Date:** 2026-08-21

## Context

S-12.4 put a human before `ship`. `08-audit.md` F16 says that is too late:

> `interrupt_before=["ship"]` means the human reviews after grounding, screening,
> investigation, repair, and audit are all paid for. If they would have rejected
> the direction, the whole budget is gone.

S-12.5 is the fix — an interrupt after the finding audit, before repair. Its AC
says **optional** where S-12.4's does not, and that word turned out to be the
whole design.

## Decisions

### 1. The asymmetry between the two gates is deliberate

The ship gate has no off switch. ADR 130 gave the reason: it guards an
irreversible outward act, and a parameter that could disable it is the thing that
story refused.

The early gate has one, and the same reasoning is what permits it. **It guards a
budget, not a patch.** An operator running unattended may reasonably decline to be
interrupted three phases early; the worst case is euros. Declining the ship gate
would ship a patch nobody read. So `early_review` exists and no `trust` parameter
does, and the two facts are consistent rather than in tension.

### 2. `gated` is the master switch and `early_review` narrows it

The first draft made them independent booleans. **Fifteen existing tests then said
`gated=False` and parked at `repair` anyway** — the signature of a switch that
does not do what its one obvious use implies. *No interrupts at all* is the thing
a test of the graph's shape wants, and it should not take two arguments to say.

So `gated=False` removes every gate, and `early_review=False` keeps the ship gate
while dropping the early one. Each switch now has a caller who wants exactly it.

### 3. Two reports, and neither carries the other's evidence

`Finding` has no patch field and nowhere to put one. There is not a patch yet, and
a report with an empty patch section invites the reader to answer the later
question — *is this patch right* — with the earlier question's material.

What it carries instead is `Routing.describe()`, which is the verdict, where it
sends the run, and **why that rather than the obvious**. S-9.8 recorded that
`because` is not decoration: two of the five routes are reached from more than one
verdict, and a reader who cannot tell those apart cannot act on either. Writing
only the verdict name would leave a person deciding whether to spend three repair
attempts on a finding whose label they can see and whose argument they cannot.

`spends_repair` rides along because it is the premise of the gate. A finding going
back for more experiments spends no repair budget, so there is nothing to approve
or decline, and the report says so rather than implying a decision is owed.

### 4. One rule about which flag to read, shared

Both gates read the **latest** flag of their kind, never the first. S-11.7 sends a
broken patch back to the Surgeon and S-9.8 sends an unsound finding back for more
experiments; either way a second round appends, and showing the earliest would
present a human with the verdict on something already superseded.

`_latest` returns `None` rather than raising, because *no flag of this kind* means
different things at the two gates — a defect at the ship gate, and simply *not
there yet* at the early one. The callers say which. Sabotaging the rule fails a
test at **both** gates, which is what confirms it is one rule and not two.

### 5. The checkpointing tests are ungated throughout

S-12.2's suite is about what the checkpointer writes and when. A run that stops at
a gate visits four nodes rather than seven, which would let *a checkpoint after
every node* pass while proving it for less than half of them. That is a test
weakened by an unrelated change, and it is worth saying in the file rather than
leaving the next reader to wonder why the calls carry a flag.

## Consequences

Epic 12 has one story left: S-12.6, time travel.

**When S-13.4 lands, both gates want the same condition and this is where it
goes.** S-12.4's AC and S-12.5's both say *at trust level 0*, and both currently
resolve to *always* for the reason ADR 130 gives. One place, asked once — and the
`early_review` parameter is already the shape that condition will take, so the
ledger replaces a default rather than introducing a concept.
