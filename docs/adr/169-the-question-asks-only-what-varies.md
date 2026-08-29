# 169 — The question asks only what varies

**Status:** accepted
**Date:** 2026-08-29
**Amends:** ADR 060 (S-5.7's prompt assembly)

## Context

S-17.16 was written as *hand the rendered blocks to `call`*. That is one line,
and it would have made the run **more** expensive.

Epic 5 assembled a five-segment prompt — system, playbook, source, log, question
— with a cache breakpoint at the end of the first four. `Session.run` rendered
it on every call and nothing ever sent it: each agent built its own
`messages=[{"role": "user", "content": question}]`, so no `cache_control` had
ever reached the API. Two composition checks recorded that.

What neither recorded is that **the agents were rendering the stable segments
into their questions**. `design.render_question` emitted `SOURCE UNDER
SUSPICION\n{source}` and `EXPERIMENT LOG\n{log.render()}` into the question, and
`Prompt.render` separately built `SOURCE` and `LOG` blocks from the same two
values. Four agents did this: `design` and `hypothesis` with both segments,
`interpretation` with the log, `explain` with the source.

Forwarding the blocks would have sent every one of those twice. `04-cost.md`
§12.2 makes this the dominant cost variable — 120 investigate calls at 60k
uncached is $39.00 against $1.68 pruned and 85% cached — and the duplicated
segment is the log, which grows with every experiment.

## Decision

**A question contains only what varies between one call and the next.** The
stable segments reach the model as the session's blocks and nowhere else.

`render_question` no longer takes `source` or `log` in any of the four modules,
and `design`, `generate` and `interpret` no longer take them either. This is not
tidying: an unused parameter that a caller still passes reads as *this content is
being sent from here*, which is exactly the belief that produced the duplication.

## The system prompt is the caller's, and the request never shapes it

The first version of this change put `Segment.SYSTEM`'s text into the request's
`system`. It is the obvious reading — the segment is called system — and it would
have shipped a worse defect than the one the story fixes.

`orchestrator/adapters.py:492` opens **one** session for the whole investigate
loop, with `_INVESTIGATION_PROMPT = hypothesis._SYSTEM`, and all three
Diagnostician steps run on it. While each agent sent its own `_SYSTEM`
explicitly, that was a billing and caching mismatch — precisely what
`repair/sessions.refuse_foreign_session` was written for, and which nobody ever
applied to the Diagnostician. The moment the session's string becomes what is
*sent*, `design` and `interpret` receive the **hypothesis** prompt: told to answer
with a statement, a primitive and a rationale when they owe a specification and a
verdict.

Nothing in the suite would have caught it. Every fixture builds its recordings
from the same session it drives the agent with, so the recording and the call
agree on a request that is wrong in the same way. The full gate was green —
3307 passed — with the defect live.

So **`as_request` skips `Segment.SYSTEM` and each caller passes its own
`_SYSTEM`.** A session's system string identifies the session — it is what
`owner_of` attributes, what `refuse_shared_session` compares, what the prefix is
billed against — and that is a different job from being the prompt a model reads.
Conflating them is what produced this.

The cost is one breakpoint of four: the system text is sent as a plain string and
does not cache. The playbook, the source and the log do, and they are the part
that grows. **The system text was never being cached across the loop's three
steps anyway** — see the correction below.

**The alternative was a session per step**, which is what `sessions_for` already
documents itself as (*"a session per agent step"*) and what `owner_of` and
`refuse_foreign_session` are built for. It is the better end state and it is not
this story. It needs `refuse_foreign_session` on the five call sites that never
adopted it, and a shared `Budget`, so it belongs in a story with its own AC.

## Correction: the loop already has three cached prefixes

**Recorded 2026-08-29, and it was wrong in this ADR before it was checked.** The
first version of this section justified skipping the system segment partly on
cost: that keeping one session for the loop keeps one cached prefix, and that a
session per step would make it pay three write premiums instead of one.

**Both halves are false.** Prompt caching is a prefix match and the render order
is `tools` -> `system` -> `messages`, so the `system` parameter is *part of* the
cached prefix and sits ahead of every message block. The three Diagnostician
steps send three different `_SYSTEM` strings, so they have **three separate cache
entries already** — on `main`, before this story and after it, under any session
arrangement. Session objects do not decide this; the bytes sent do.

What follows:

- **§12.2's figures were already optimistic for the investigate loop**, and this
  story did not change that in either direction.
- **A session per step costs nothing extra in cache terms.** The premiums are
  paid today. The objection recorded against it below was not real.
- **The decision to skip the system segment still stands**, on the argument that
  actually holds: a session's system string is not the prompt its steps send, so
  shaping it into the request substitutes one agent's instructions for another's.

## The headers moved with the content

A block carries raw text. Removing `SOURCE UNDER SUSPICION` from the question
without putting it anywhere would have sent the model a bare path between a
playbook and a question, with nothing saying what it was — a silent prompt
regression that no cost measurement would show and no test was watching for.

`context.labelled` puts each segment's own name above its text, taken from
`Segment` rather than written out at four call sites, so a renamed segment cannot
leave a stale header on the one block nobody re-read. Empty text stays empty
rather than becoming a lone header.

## An investigation and its session must name the same source

Before this story a session built for a different source was a duplicated string
and nothing worse — the agents were sending their own copy. Now the source
reaches the model **only** as the session's cached block, so a mismatch would
have the run reason about one file while measuring another, silently, with every
measurement in the log still correct.

`Investigation.__post_init__` refuses the mismatch. It **assigns** the log in the
line above and **refuses** the source, and the asymmetry is deliberate: the
session's own `PrunedLog` is an empty placeholder and replacing it loses nothing,
while a source is a caller's answer to *what are we studying* and two different
answers is not a question this can settle by picking one.

## The four adversarial call sites keep building their own message list

`CLAUDE.md`: *the Adversary never sees the Surgeon's reasoning — enforced by
constructing a fresh message list, not by instructing the model to ignore it.*
`audit_messages` is that construction, and blocks assembled by the session are a
prompt assembled somewhere else. Accepting one would make the guarantee depend on
what that somewhere else happened to render.

So the tree is partitioned: seven call sites shape their request from the blocks
(`design`, `explain`, `hypothesis`, `interpretation`, `proposal`,
`falsification`, `patch`) and four build their own (`audit/invocation`,
`audit/patchaudit`, `audit/testquality`, `repair/testaudit`). A test lists both
halves, because the dangerous direction is drift into the permissive one: an
audit site that quietly started using the blocks would still pass every audit
test, since a fresh message list and a rendered prefix carry the same words.

The caching forgone is the audit's ten calls, not investigation's hundred and
twenty.

## `cache_control` is not part of a request's identity

`request_digest` strips it recursively before hashing. A breakpoint changes what
a call *costs*, not what it *asks* — the API returns the same answer to the same
text whether or not a prefix of it was served from a cache. Were it part of the
digest, every recording in the suite would be invalidated by a change that cannot
alter a reply.

The stripping is recursive rather than a top-level pop, because a breakpoint sits
on a content block inside a message's content list, two levels down, and a
shallow strip would leave exactly the ones that are used.

What is emphatically still hashed is the text. Moving content out of a question
and into a block gives a different digest, which is correct: that is a different
prompt, and a test asserts it rather than leaving the exclusion looking like a
general licence to ignore differences.

## Consequences

`04-cost.md` §12.3's engineered figure is now *reachable* rather than achieved.
Three of the four stable segments are sent with breakpoints on them and
consecutive calls send a prefix the later one can read, which is the property a
cache hit is a match on — but the rate itself cannot be measured under a
replaying client, where `warm_hit_rate()` would report whatever number the
recording carries. That measurement belongs to the first run against a real API,
which is S-17.1.

**A session per step is left open**, with the five call sites that never adopted
`refuse_foreign_session` still able to be handed a session whose system string is
not their prompt. That is now only a billing and caching mismatch again, as it
was before this story — but it is the mismatch that made the trap above
reachable, and it should be closed.

`Prompt.viability` should be checked per model when that run happens: S-5.7
recorded that haiku's minimum cacheable prefix is 4096 tokens, so routing a step
down a tier can raise its effective cost.
