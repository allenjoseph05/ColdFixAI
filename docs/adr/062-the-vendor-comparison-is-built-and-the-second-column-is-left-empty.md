# 062 — The vendor comparison is built, and the second column is left empty

**Status:** accepted
**Story:** S-5.9 — vendor cost comparison on effective cost (**partial**)
**Date:** 2026-08-09

## Context

S-5.9 exists because ADR-002 chose a vendor on SDK ergonomics rather than price,
and the Notes are explicit that the question "will otherwise be re-argued from
list prices and memory."

Four of its six acceptance criteria cannot be satisfied today, and the reasons
are different from one another:

| AC | Blocker |
|---|---|
| 1 — run the scenario set against ≥2 vendors | No second vendor account exists, and running one is real spend against an external service. Confirmed with Allen: there is no second vendor. |
| 2 — cost per **confirmed** finding | Confirming a finding is E9's finding audit, which does not exist. S-0.8's scenarios produce no findings at all. |
| 4 — experiments to conclusion | **Not merely unmeasured — S-0.8 measured it and found it unmeasurable.** In 60 runs the model chose *no finding, stop* zero times: it reasons correctly, withholds the verdict, and proposes one more experiment. |
| 6 — supersede ADR-002 if the numbers warrant | Needs the numbers. |

`CLAUDE.md` requires asking rather than improvising when a dependency is missing,
so this was put to Allen before any code was written. The decision: build the
half that needs neither spend nor a missing epic, and record the rest as blocked.

## Decision — build the cost model, refuse to invent the comparison

AC 3 and AC 5 need no vendor run at all. They are the part that makes the
decision falsifiable:

- **AC 5** is *recording facts*: minimum cacheable prefix, cache TTLs and their
  write premiums, the read multiplier, prefix-match semantics, and cache scope.
- **AC 3** is *arithmetic*: effective input cost given a hit rate, alongside list
  price.

`effective_input_usd_per_mtok` is the headline. At §12.2's engineered shape — 120
calls, 12k prompt, 85% cached — an input token costs roughly a quarter of its
list rate, and the write premium is amortised across the calls that read it, so a
one-call run pays it in full.

**One vendor profile is recorded and the second is deliberately absent.** No run
against another vendor has happened and none of its cache figures were verified,
so writing one would be exactly the *re-argued from memory* failure the story was
written to end. `recorded_profile` refuses an unknown vendor with a message that
says so. A test asserts `VENDORS` holds precisely one entry, so adding a second is
a deliberate act with a failing test behind it rather than a quiet import.

The Notes' claim — *a vendor 30% cheaper per token but with a larger minimum
cacheable prefix can cost more here* — is demonstrated against a **hypothetical**
profile that lives only in the test file and is named hypothetical. Its figures
are invented to exercise the arithmetic and make no claim about any real vendor.
A control asserts the cheaper vendor wins once the prompt clears its minimum,
without which the demonstration would pass for a module that simply always
preferred Anthropic.

## Decision — absent measurements report their absence

`MeasuredRun` carries cost per confirmed finding, experiments to conclusion, and
observed hit rate, and every one is `None` until a real run supplies it. A
comparison defaulting them to zero would read as *this vendor is free and reaches
its conclusions instantly*.

The rendered report names each blocker specifically rather than saying "unknown":
cost per confirmed finding *needs E9's finding audit*, and experiments to
conclusion is *not measurable — S-0.8 found the model never concludes*. A reader
who does not know why a column is empty will eventually fill it with a guess.

`Comparison.cheapest()` returns `None` below two vendors and the report says *this
is a cost model rather than a comparison*. Naming a winner from a field of one is
how ADR-002 came to be defended rather than tested.

## Decision — the profile is derived, not copied

Anthropic's prices come from S-5.3's `PRICE_BOOK` and its minimum prefixes from
S-5.7's `MINIMUM_CACHEABLE_PREFIX`, rather than being restated here. A second copy
is a second thing to go stale, and going stale is precisely the failure this story
exists to prevent. `price_book_agrees(profile)` is the guard that notices if that
ever changes.

## Consequences

**S-5.9 is recorded as PARTIAL, not DONE.** `CLAUDE.md` says a story is done when
its acceptance criteria are provable, and four of these are not. The backlog entry
names each blocker and what would unblock it.

**ADR-002 stands, untested.** Nothing here supersedes it, and the report says so
in as many words. What has changed is that the question is now falsifiable: when a
second vendor's figures exist, they are a data addition rather than a code change.

**The `price_book_agrees` guard could not fail, and sabotage found it.** It took
no argument, so it could only ever return `True` and a version that always did was
indistinguishable from the real one. It now takes a profile and is exercised
against one that disagrees. **Fourth time this project has recorded S-3.12's
finding**: a guard nothing can make fail is a guard nobody has checked.

**Sabotage-verified on twenty-four properties, all caught.**
