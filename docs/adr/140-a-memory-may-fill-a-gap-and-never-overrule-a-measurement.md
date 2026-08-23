# 140 — A memory may fill a gap and never overrule a measurement

**Status:** accepted
**Date:** 2026-08-23

## Context

S-13.1 gave a playbook entry a shape, S-13.2 built the gate that decides when one
may be believed, S-13.6 wrote the first entries — and nothing had ever read one.
`Resolution` carried entries *unread* by design, three modules deferred the
schema to Epic 13 and then declined to guess, and the whole store sat on the
read-only side of a boundary nobody had crossed.

That made it one of the three things in this codebase that were designed and
unreachable, and it is why S-13.5's ablation could only ever measure zero: the
withheld and retrieved conditions were identical runs.

S-13.7 is the story that lets an entry change an outcome, which makes it the
story where `08-audit.md` F4's poison — *a wrong entry propagates silently to all
future runs and compounds* — finally has somewhere to propagate to.

## Decisions

### 1. Two lookups, and only one of them may decide anything

`PlaybookLookup` stays exactly what S-7.4 built: everything filed under the
fingerprint key, provisional entries included, carried unread. It is *context* —
what the Explorer is shown.

`TrustedLookup` is new and returns only what `standings` promoted: three
different projects recorded a successful use, and no two failures quarantined it.
It is the only list a decision may rest on.

**The separation is at the type level rather than at the call site.** A single
lookup with a filter applied by whoever called it would put the safety decision
in every caller, and the backlog note for this story is explicit that acting on
`recall` is the mistake to avoid — it returns provisional entries too.

### 2. The one place a memory decides, and it is a gap rather than a disagreement

`resolve_auth` acts only when the probe returned `Scheme.UNKNOWN` and the
observation *did* speak to authentication — a `401` with no `WWW-Authenticate`
header. That is a route saying *something is enforcing authentication and I will
not say what*: a credential is known to be needed, `UNKNOWN` is not mintable, and
today that repository simply does not ground.

Everything else is refused, and each refusal has its own reason:

| Situation | Why not |
|---|---|
| the route answered `200`, `403`, or `401` with a challenge | a measurement of *this* route, and a prior about projects of its kind does not override one |
| the probe was inconclusive (`404`, `500`) | the answer said nothing about authentication; `Requirement`'s own docstring sends the Explorer to a different path, not to a user nobody asked for |
| two trusted entries name different schemes | refused, not resolved — see below |
| the entry names `JWT` or `NONE` | detectable and not mintable, and nothing to do, respectively |
| the entry records a failure | *the route stayed unreachable* is a record of what to expect, not an instruction |

`Established` gains `REMEMBERED`, which is the weakest of the three. The enum's
own docstring already refuses to merge a declaration with an observation, because
a report that flattened them would put a guess and a measurement in the same
column. A prior about a *kind* of project is weaker than either.

### 3. Two trusted entries that disagree are refused rather than resolved

Each was earned on three different projects. A disagreement between two of them
is evidence that the fingerprint does not determine the answer — and picking one
is the alphabetical tie-break that seeded a hundred authors and drove the wrong
route at S-7.13.

Refusing on *more than one scheme* rather than on *more than one entry* is the
part worth stating: a lesson learned twice is the ordinary case and must still
act.

### 4. The use is recorded where the answer is, not where the decision was

AC 3 asks that acting on an entry be recorded so a wrong one demotes itself. The
tempting place is `resolve_auth`, and it is the wrong one: **a mint that succeeds
says a user was created, not that the route accepted the credential.** F4's
poisoned entry — *DRF always uses TokenAuthentication* — mints perfectly well in a
session-authenticated project and then collects a `403` on every request. A use
recorded at the mint would score that as a success, three times, and the entry
would never lose its standing.

So `Resolution` reports `acted_on`, and the composition records the use once the
workload has actually been driven. **Only a failure the credential could have
caused counts as one**: `WorkVerificationError` and `EmissionError` are *the work
did not hold up*, which is exactly what a wrong scheme produces, because
`verify_work` refuses to measure an error response. An interpreter that would not
start travels without a use being recorded — two failures quarantine an entry,
and spending one of them on a fact about the machine would demote a memory that
was right.

### 5. The reader lives beside the writer, and returns a name

`learned_from_auth` composes the sentence; `remembered_requirement` reads it back.
They share the three fragments as constants, so the template has one owner —
`slack.py` keeps `REVIEWED_AT_EVERY_LEVEL` beside `LABEL` for the same reason. A
round-trip test walks every `Scheme` through both, because a template edited on
one side is how a memory silently stops being actionable while still looking
trusted.

It returns the scheme's **name**, not a `Scheme`. `playbook.py` holds no auth
vocabulary and `auth.py` imports it, so returning a `Scheme` would be a cycle; the
caller has to recognise the name anyway. The lookup table is by `name` because
`Scheme` is a `StrEnum` over sentences — `Scheme("TOKEN")` raises.

### 6. The seams take the key, which is why they had never been filled

`PlaybookWriter` was `writer(store, key)` and `UseRecorder` was drafted the same
way. **The key is `Fingerprint.playbook_key()`, derived inside the sequence**, so
a caller could only bind one by fingerprinting the repository itself first — and
the one place that would happen twice is the one place two spellings of a key can
disagree. `PlaybookLookup` had it right from the start: the key is a parameter.

Both writers now take it, and that is what makes the wiring possible at all.

### 7. `adapters.ground` wires the journal, and the campaign wires the repository

`Grounder` gains the four journal seams as keyword parameters. The campaign binds
what it owns — the checkout, its interpreter, how to request from it, the plan,
the reset proof — and the node supplies what the *run* owns, from
`Resources.store` and `Resources.project`, which are already there for the
failure memory and the trust ledger.

The node reduces the result to the no-argument callable the loop takes: the loop
drives a repository and has no business carrying a journal through itself.

This closes all four seams at once. `playbook_from_store` had been unreachable
since S-13.1 and `writer` since S-13.6.

## Consequences

- **The playbook is no longer designed-and-unreachable.** Two remain:
  `ExperimentRef` (ADR 129) and `gates_for` (ADR 138), both waiting on S-17.1.
- **S-13.5 is unblocked.** Both halves are now real: S-7.14 made *steps to ground*
  a variable, and this makes the retrieved and withheld conditions different runs.
  The ablation can measure something other than zero.
- A first run against a fresh store behaves exactly as before. Nothing is trusted
  until three different projects agree, so `actionable` answers `None` and the
  probe's verdict stands.
- **Sabotage: 4 properties, 2 caught first time.** The two survivors were both
  missing tests, and both were the *join* rather than the helpers: the failure
  half of the use recording asserted only that the error travelled, and the node
  test asserted that a callable was wired rather than which list it read. Ninth
  and tenth instances of the pattern S-13.3 recorded.
