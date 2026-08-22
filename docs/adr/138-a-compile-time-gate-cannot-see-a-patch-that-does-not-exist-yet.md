# 138 — A compile-time gate cannot see a patch that does not exist yet

**Status:** accepted
**Date:** 2026-08-22

## Context

Three stores existed and nothing read or wrote two of them. The trust ledger
(S-13.4) was never consulted; the playbook (S-13.1, S-13.2) had no production
writer. S-13.6 is the story that connects them, and it is marked **SAFETY**
because its first criterion is the only place in this system where a human review
can be *skipped*.

ADR 130 refused to build that switch: *a `trust: int` parameter would have exactly
one reachable value, and the danger is not that nobody could flip it — it is that
a caller could, turning the gate off with no ledger to justify it.* The refusal
was right. It is now spent, because a level is a thing a project earned, recorded
append-only, and `standing` is the only way to obtain one.

## Decisions

### 1. The level chooses the gates, and the early one opens first

| level | early review | ship gate |
|---|---|---|
| `GATED` | yes | yes |
| `FAMILIAR` | no | yes |
| `TRUSTED` | no | no |

ADR 131's asymmetry decides the order: the early checkpoint guards a **budget**
and the ship gate guards an **irreversible outward act**, so the cheaper
protection is the one to drop first. That was recorded when the two gates were
built and is spent here rather than restated.

### 2. §4 is enforced in the node, because the gate is compiled before the patch exists

`interrupt_before` is a compile-time argument — S-12.2 established that there is
no runtime equivalent. A trust level is available at compile time; **a patch is
not.** So a graph compiled for a `TRUSTED` project has no ship gate at all, and
`00-BRIEF.md` §4 requires human review for a slack-reducing patch *at any trust
level*.

The ship node therefore refuses one outright and escalates, whatever `gates_for`
returned. Nothing is invalidated and nothing is recorded, because nothing
shipped.

**LangGraph's dynamic `interrupt()` was measured and not used.** It exists and
would express a runtime gate directly — but resuming one needs
`Command(resume=...)` rather than `invoke(None, config)`, which would change
`resume`, `waiting_at`, and the `before` fix ADR 133 built on `interrupt_before`
semantics. A node that declines to ship gets the same guarantee without touching
three modules and a safety property, and it is where the rule belongs anyway: the
patch is refused by the thing that would have shipped it.

`REVIEWED_AT_EVERY_LEVEL` lives in `repair/slack.py` beside `LABEL`. Not only to
break an import cycle — the refusal belongs with the classifier that decides the
label, so the node enforcing it and the report explaining it read one sentence.
Two spellings of a refusal is how they come to disagree, which is the argument
`LABEL` itself makes one line up.

### 3. A shipped patch moves the ledger, or the ledger never moves

Without a writer, `gates_for` can only ever read `GATED` — a ledger that exists
and is not written, as useless as one that exists and is not read. `ship` records
`ACCEPTED`; the human paths that reject or revert are the caller's to record, and
`record_outcome` is the one way in.

### 4. `Resources.failures` became `Resources.store`

By this story it was one journal answering three questions — what was tried and
failed, what has been learned about projects of a kind, and what autonomy this
project earned. A field named for one of them is a field the next caller looks
past.

### 5. The playbook writer records only what auth established

F4's poison is *"DRF always uses TokenAuthentication"* — a claim about what a
project of a kind requires. So the entry is written after the auth stage rather
than at the end of grounding: a run that fails later has still learned whether
this kind of project needs a credential.

**Everything written is provisional, structurally.** `writer` appends and does
nothing else; there is no argument through which something could be written
already believed, and three different projects must agree before `trusted`
returns one.

## Consequences

`gates_for` is a pure function of a level, so a caller wires it as
`assemble(wiring, saver, **gates_for(standing(...).level))`. Nothing in `src/`
does that yet — the campaign entry point that would is S-17.1's, and this story
deliberately stops at making it possible and provable rather than inventing a
caller for it.

**S-6.3's named-parameter trap appeared for the fourth time.** `written.append`
does not satisfy `PlaybookWriter`, because the protocol declares a named
parameter and `list.append`'s is positional-only. S-12.1, the S-12.7 draft, the
`Step` closure, and now this. It is worth expecting rather than rediscovering:
any `Protocol` in this codebase with a named parameter will reject the obvious
one-line callable.
