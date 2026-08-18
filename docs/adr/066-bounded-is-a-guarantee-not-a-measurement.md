# 066 — Bounded is a guarantee, not a measurement

**Status:** accepted
**Story:** S-6.3 — store experiment results by reference
**Date:** 2026-08-11

## Context

`08-audit.md` F13 makes a quantitative claim: *`experiments` is append-only and
lives in checkpointed state. Forty experiments × full measurement output,
checkpointed after every node, is megabytes of duplicated writes.* Its fix is to
put results in the replay cache keyed by hash and leave the state holding hashes
and one-line summaries, with the agent fetching detail through a tool call. It
ends by noting the alignment with `04-cost.md` §5's context pruning.

AC 4 asks for *checkpoints under a stated size limit*, which means this story has
to state one and stand behind it.

## Decision

### The limit rests on arithmetic, not on a sample

A measured limit holds for the experiments somebody happened to test with. Every
`ExperimentRef` is size-checked at construction against `MAX_REFERENCE_BYTES`
(1 KiB), so S-5.4's cap of 40 experiments gives a log that **cannot** exceed
40 KiB whatever the measurements were. The stated limit of 64 KiB for the whole
state has that behind it, leaving 24 KiB for the artifacts that do not grow with
the investigation.

Without the per-entry check the bound would be a hope: an experiment spec
carrying a large parameter block grows the checkpoint with nothing noticing, and
the checkpoint is written after *every node*, so the log's size is multiplied by
the whole run.

### The reference carries the key, not only the digest

F13 says *keyed by hash*, and the digest is the recording's filename — so a
digest alone is enough to find the file, but only by scanning the store, since
S-5.1's lookup takes a key and derives the digest from it. Carrying the key makes
a fetch one read instead of a sweep, and makes the reference self-describing: a
log line that says *which* experiment this was without opening anything. A key is
identity, not a result, so the state still holds no measurements — and a test
asserts the encoded reference contains none of the measurement's output.

### The outcome bound is S-5.8's, shared rather than copied

F13's closing note about §5 is a design instruction, not a remark: the pruned log
and the checkpoint log are the same discipline applied to two artifacts — what
goes in the prompt and what goes in the checkpoint. Importing `MAX_SUMMARY_CHARS`
is what stops them drifting apart. The summary is composed from the primitive and
the target with only the outcome supplied, which is S-5.8's construction and F6's
reason.

### Size is measured as JSON, and that over-estimates on purpose

S-6.1 kept `langgraph` out of `src/` and made every state field
JSON-representable, so size can be measured without importing the framework.

**My assumption about why that was safe was wrong, and the test corrected it.**
I had reasoned that ADR 003's Postgres checkpointer stores JSON, so the JSON
encoding *is* what gets written. LangGraph's serializer is msgpack, and it is
**smaller** — 13,404 bytes against our 15,761 for a full forty-experiment log,
about 85%. That is the safe direction: a state that fits this limit fits what is
actually written, with room to spare. A proxy that under-estimated would give a
limit that passed in a test and was breached on disk. The ratio is now pinned by
a test rather than reasoned about.

## Consequences

**The sharpest property is that checkpoint size does not depend on measurement
size.** Two investigations identical but for what they measured — 100 bytes of
output per experiment against 100 kilobytes — produce checkpoints of *exactly*
the same size. That is what storing by reference means, stated as an equality.

**F13's claim is measured rather than repeated.** The same forty experiments
stored by value come to **1.21 MB** — F13's "megabytes", about 18× the limit, and
at a conservative 30 KB of output per experiment. That control is what makes the
64 KiB figure mean something: without it the limit would pass equally for a
design that stored everything and was tested with small results.

**Makes easy.** S-12.2's checkpointer inherits a bounded write. S-8.4's log entry
has a size budget to fit inside. The agent's *fetch the detail* tool is
`resolve`, one call.

**Makes hard.** A checkpoint is only resolvable against the store that recorded
it. S-5.1 partitions recordings by machine, so a checkpoint carried to another
machine references measurements that are not there — `ResultNotStoredError` says
so by name rather than returning nothing, because the state and the store
disagreeing is worth distinguishing from an experiment that was never run.

**Rules out.** Full results in checkpointed state, and a size limit justified by
having measured one investigation.

**One incidental fix.** The module's base exception was originally
`ReferenceError`, which shadows a Python builtin — `ruff`'s `A001` caught it, and
it is now `ExperimentReferenceError`. A shadowing name is caught by anything that
writes `except ReferenceError` meaning the builtin.

**Sabotage-verified on thirteen properties, all caught**, including raising the
per-entry budget until the arithmetic stops holding and raising the stated limit
until the by-value control passes. Baseline re-run green after the pass.
