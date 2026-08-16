# 088 — The chain carries its exclusions' conditions, and derives its own confidence

**Status:** accepted
**Story:** S-8.6 — evidence chain assembly
**Date:** 2026-08-16

## Context

Four acceptance criteria — a Pydantic model requiring symptom, exclusions,
localization, mechanism, complexity, site, context and confidence; every
localization link requiring an attached measurement, schema-rejected otherwise;
context files each carrying the reason they were implicated; and a golden-file
test for serialization.

`02-architecture.md` §2.4 calls this artifact *simultaneously the input to Layer 3
and the body of the eventual pull request*. Every guard here is a schema rule
rather than a convention because the next reader is the Surgeon, the one after is
the Adversary, and the one after that is a person deciding whether to merge.

Two of §2.4's lines turned out to be the story.

## Decision

### `exclusions` holds S-8.5's `Exclusion`, not a bare experiment

§2.4 sketches an exclusion as `{hypothesis, primitive, measurement, verdict:
rejected}` — with **no conditions**. `08-audit.md` F3 is the finding that an
exclusion recorded as fact permanently blocks the correct hypothesis, and S-8.5
fixed it *inside the investigation* by making every exclusion carry its
preconditions.

Flattening those back here would reintroduce F3 at the one boundary where it does
most damage: **the report a human reads.** *Not the database, queries flat at 7,
7, 7* printed in a pull request with no mention of the uniform fixtures it was
established under is precisely F3's false fact, now with a reviewer's signature
under it.

So the chain holds `Exclusion` and `render()` prints the conditions beside the
claim. A test asserts the rendered report says *under fixture shape uniform … 
scale 10 to 1000*.

### `confidence` is derived, required, and recomputed

§2.4 says *derived from number of independent confirmations*. `03-agents.md` §4.4
writes it as a bare `float`, and the authority map gives artifact schemas to §2.4.

A confidence an agent writes is a number nobody measured. But a bare property
would not survive serialization, and this artifact is serialized on purpose,
travels to two other agents, and becomes a pull request. So it is **S-7.9's
construction**: the field is required — the copy is mandatory — and validating a
chain recomputes it and refuses a copy that disagrees. `assemble` has no
`confidence` parameter at all, so nothing that *makes* a chain gets to choose the
number.

**Independence is counted in distinct primitives.** Two confirmations from one
instrument are one kind of evidence; scaling and ablation agreeing is two, which
is S-8.7's thesis behaviour showing up in the number.

**Exclusions deliberately do not raise it.** Ruling something out does
intuitively raise confidence in what remains, and counting it would let an agent
lift its own number by excluding things nobody suspected.

**It is not a probability, and two properties keep that honest.** `1 - 2**-k` is a
*model* — each independent instrument that agrees halves the remaining doubt — and
a model is an assumption, not a measurement. It can never reach 1, so no chain
claims certainty; and the report says in words that the figure is a count on a
0–1 scale. `00-BRIEF.md` §6's diagnostic agreement across ten runs remains the
project's actual reliability number.

### AC 2 is structural rather than a validator

A `LocalizationLink` holds an `Experiment`, and S-8.4 already refuses one whose
measurement is empty. So there is **no `measurement` field here for anybody to
leave blank** — asserted by inspection, with the active attempt made one layer
down where the refusal actually is. Third instance of the pattern S-8.1 started:
the enforcement is an absence.

`share_of_cost` is bounded to a fraction and requires a stated `basis`, which is
`Bound.basis`'s construction — a number whose derivation is not stated is one
nobody can dispute. The schema cannot check the arithmetic, and says so.

### Context reasons are required because the list is load-bearing

`02-architecture.md` §3: *scope is determined by the evidence chain's context
list, not by the agent's guess.* A file admitted with no reason is a file the
Surgeon may edit because somebody felt it was relevant.

### What the schema cannot do, carried in the artifact

`RESIDUE` travels into the rendered report: this proves every link carries a
measurement and that the confidence matches the confirmations, and **does not**
check that the mechanism follows from them. `08-audit.md` records that as the
separate flaw the finding audit exists for — *schema validation and adversarial
review address different failure modes; we had only the first*.

## Consequences

The golden file at `tests/diagnosis/golden/evidence_chain.json` pins the wire
format. A diff there means the artifact three downstream components read has
changed shape, so it is regenerated deliberately and never reflexively.

S-8.5's numeric conditions are now stored as `float` rather than as written. This
story found it by serializing one: `scales=[10, 100]` stored Python `int`s under a
field annotated `str | float`, and pydantic's union serializer said so. A stored
type that disagrees with its annotation is a golden file waiting to move.

## Sabotage

Twenty-six properties, all caught — after five survived, in two distinct groups.

**Two redundant conditions, which is S-7.4's shape for the third and fourth time
in this epic.** `min_length=1` on `localization` cannot change an outcome,
because *at least one confirming link* strictly implies *at least one link*; and
`min_length=1` on `Implicated.reason` cannot either, because *not whitespace*
implies *not empty*. Both read as guards while guarding nothing. Both were
collapsed, leaving the stronger check to speak — and in the first case the
refusal is also the better sentence, since *no confirming localization link is
not a diagnosis* says more than *too short*.

This is now frequent enough to state as a habit: **when two checks sit beside
each other, ask whether one implies the other before writing a test for both.**
S-8.3 found the same shape in an unreachable branch; these two are its
sibling — reachable, but unable to change an answer.

**Three genuinely untested guards, found only because the redundant pair forced a
second pass.** `mechanism`, `scope` and `metric` could all be emptied with every
test still passing, while their neighbours `basis` and `reason` were covered.
That asymmetry is the tell: a field is not tested because it is important, it is
tested because somebody happened to think of it. They are now swept in one
parametrised test over every string a reader depends on.

The two that matter most were caught from the start: flattening exclusions back
to bare experiments, and believing a stated confidence instead of recomputing it.
