# 143 — A budget per phase, and one ledger under all of them

**Status:** accepted
**Date:** 2026-08-25

## Context

S-17.1 asks for a full pipeline run on the holdout. It cannot start: there has
never been a `Sessions` implementation anywhere in `src/`. The protocol has
existed since S-12.7 and every caller in the tree is `lambda system: object()` in
a test — and `gates_for` has been written, tested and uncalled since S-13.6.

Neither gap is visible from inside a node. A node asks for a session by prompt
and uses it, so any factory returning something plausible satisfies every test in
the suite. This is the third instance of the pattern S-7.13, S-8.11 and S-7.14
closed: mechanism built, tested, and never given the caller that would exercise
it.

## Decisions

### 1. One session per prompt, because two phases refuse to share a budget

`GroundingRun.__post_init__` raises unless its budget stalls after **15** — *a run
that escalates after three unchanged reports would abandon a repository
mid-install.* `check_stall_configuration` raises unless an investigation's is
**8** — *at three, an agent that had ruled out three hypotheses would be stopped
while it was still buying exclusions.* Everything else takes S-5.4's default of
**3**.

A single `Budget` carries one `stall_after`, so a campaign with one budget cannot
satisfy both. `Session` is the only thing that constructs a `Budget`, and
`Sessions` is keyed on the prompt — so the prompt is where this gets decided, and
`STALL_AFTER` is the table that decides it.

**The table is short because getting it wrong is loud.** Both special cases are
constructor-level refusals at the start of a phase, not silent drift at the end
of one. Prompts absent from the table take the default, and naming the audits
there would be inventing a requirement in order to record it — they count rounds
and attempts rather than steps, and none of them refuses a number.

### 2. One ledger, or the ceiling is per-phase

`Budget.spent_eur` reads `self.ledger.total_usd`. Sessions holding separate
ledgers each see only their own spending, so a run could pass six ceilings on the
way to breaching one. `04-cost.md` §12.1 costs a worst case at ~$291; a ceiling
that sees a sixth of it is not a ceiling.

The ledger is shared by default and accepted as an argument, because pooling
several runs into one ledger is a real thing S-15.3 will want.

### 3. The factory memoizes, and that is load-bearing

`adapters.investigate` calls `resources.sessions(...)` **every time the node
runs**. A factory returning a fresh `Session` per call returns a fresh `Budget`
per call, so every per-phase cap resets on every node execution and S-5.4's
enforcement becomes counting to one. It would also discard the prompt cache
`04-cost.md` §4 is built around.

This is the sabotage worth keeping: the mutation is a one-line change that looks
like a simplification, and it fails two tests — an identity check and a cap that
stays spent.

### 4. A prompt no role owns is refused, by asking the index

`owner_of` is called before a session is built. It had no production caller
either, and it refuses both an unclaimed prompt and one two roles claim.

Asking `agents/roles.py` rather than keeping a list here is the point. A second
enumeration of prompts in this module would be a second answer to *which prompts
exist*, and the two would disagree the first time a step was added — which is the
failure this project has now found at seven epic joins.

### 5. `gates_for` gets its caller, and no way to ask for fewer gates

`gated_graph` reads the level through `standing`, which reads the append-only
ledger. There is no parameter through which a caller could request different
gates — ADR 130 refused exactly that when a level was not yet a thing a project
could earn, and the refusal still holds now that it is.

The level is read once, at compile time, because that is when `interrupt_before`
is decided (S-12.2: there is no runtime equivalent). That is also why
`00-BRIEF.md` §4's refusal of a slack-reducing patch stays in the ship node — a
patch does not exist at compile time.

**A gated graph with no checkpointer is refused in the campaign's vocabulary.**
`assemble` refuses it too; saying it here means the message names the run rather
than the graph.

### 6. What this deliberately does not do

It does not construct `Resources`. Most of that inventory — the workbench, the
hands, the executor, the probe, the measurer, the binder — is environment-specific
and genuinely the operator's, on the same argument `Hands` and `Executor` are
supplied rather than built. A god-constructor taking twenty arguments would be a
type whose only purpose is to be unpacked.

What is here is the two things that are **decisions**: which budget a step spends
against, and which gates a graph compiles with.

## Consequences

- `gates_for` is reachable. Of the three things ADR 129, 138 and the S-13.5 note
  recorded as designed-and-unreachable, only `ExperimentRef` remains — it needs a
  `Recall` that only the replay cache produces, and `run_investigation` still
  never sees the cache.
- **S-17.1 is still blocked**, and this closes only the third of its three
  reasons. There is no API key, a run costs real money, and AC 2's PR half needs
  S-16.2 — though the holdout was chosen because *the correct answer on it is
  "nothing found"*, so the null-result branch is the expected path and needs no PR.
- **Sabotage: 2 properties, both caught.**
