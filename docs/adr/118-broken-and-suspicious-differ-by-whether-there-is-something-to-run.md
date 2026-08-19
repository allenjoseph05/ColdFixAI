# 118 — `broken` and `suspicious` differ by whether there is something to run

**Status:** accepted
**Story:** S-11.7 — verdict
**Date:** 2026-08-19

## Context

Five attacks have answered. `02-architecture.md` §4.4 gives three consequences:
`clean` proceeds to Layer 5, `broken` returns to the Surgeon with a reproducing
input, `suspicious` escalates to a human with the concern stated.

The acceptance criteria add the rule that matters: **`broken` requires a
reproducing input, schema-enforced.**

## Decisions

### 1. Nothing here calls a model

Combining five answers is `CLAUDE.md`'s *do not add a model call where a function
would do* — S-9.8's opening sentence, and it holds harder in this epic, where
three of the five attacks are arithmetic over measurements and never asked a
model anything.

### 2. Three verdicts, and no fourth

S-9.8 needed `inconclusive` as a fifth value because its `unsound` routes back to
*investigate* and spends experiment budget: it had to be able to say *do not spend
that*. Here `suspicious` already means escalate to a human, so an audit that could
not see enough **is** a suspicious one and needs no new word.

That keeps the AC's vocabulary exactly as written while preserving the rule this
epic has now built five times: **an attack that did not run is a concern, never a
pass.**

### 3. The two landing verdicts differ by evidence, not severity

`broken` means *here is something you can run*. `suspicious` means *a person has to
weigh this*. The routing falls out of that rather than being asserted: a patch with
a failing case is one the Surgeon can act on; a patch that is merely worrying has
no failing case to fix, so returning it would only produce another guess.

This is why **`broken` wins precedence** when both apply. It spends a cheap repair
attempt rather than a person, and the concerns travel in `results` where the human
sees them if it comes back.

It also decides every adapter. A detected cheat, an envelope breach and a weak test
are all `SUSPECT` — none of them produces a case anybody can run. *The improvement
only exists warm* is a judgement about a set of measurements, and handing it back
as a failing input would be handing back an input that does not exist.

### 4. `Reproduction` is a type because there are two sources

S-11.2 produces one directly: an adversarial input and the program that shows the
difference. S-11.5's suite produces the other: the command that passes on the
original and fails on the patch. Both are *a thing somebody can run to see it
again*, and only one of them is a `ReproducingInput`. `CLAUDE.md` allows the
abstraction once a second case exists; this is the second case.

The rule is enforced twice — at the verdict (`broken` without a reproduction cannot
be constructed) and at the attack result (`BROKE_IT` is *defined* as the outcome
that carries one, and no other outcome may). The second is what stops `broken`
being reached through a reproduction attached to a passing attack.

### 5. An attack absent from the list is a concern

Not supplying a result is the quietest way to skip an attack. `verdict_for` checks
the five against `Attack` and reports the gap, so a caller that ran two attacks and
objected to neither cannot get `clean`.

### 6. This story is finally the round cap's caller

S-11.1 wired `Phase.PATCH_AUDIT`'s `authorize_round` and `record_round` and left
the round's *conclusion* to the caller **in as many words**, because S-11.2 to
S-11.5 had not defined their verdicts. They have, so this module is that caller and
the conclusion is the verdict's own name.

`clean` and `suspicious` spend no round — neither starts another audit. `broken`
returns to the Surgeon only while a round remains; past the cap it escalates,
because a third attempt is the same loop with a bigger bill.

## Consequences

**Both sabotage survivors were branches no fixture could reach.**

- `from_cheat`'s *the improvement vanishes cold* branch. A warm-only gain usually
  trips the caching check as well, so every fixture reached that branch with a
  detected cheat already beside it and deleting it changed nothing. It needs the
  shape S-11.3 needed its own case for: a warm-up excess too small to trip the
  check, where every class comes back `NOT_DETECTED` and the improvement still is
  not one anybody gets.
- `route`'s handling of `clean`. **`authorize_round` checks the cap and does not
  record**, so a round nobody spends is invisible to `used` — asserting the count
  cannot see a spurious check. What can see it is exhausting the cap and then
  routing a `clean` verdict: it must still ship, where a branch that consulted the
  cap would raise instead.

The second is worth keeping in mind generally: **a counter that only increments on
`record` cannot detect a spurious `authorize`.** The observable difference is
behaviour at the boundary, not the number.

**Sabotage: 38 properties, all caught, zero skipped, after two survived.**
