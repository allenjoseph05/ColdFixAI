# 172 — The application layer is the CLI, and it is the one place that knows both

**Status:** accepted
**Date:** 2026-08-30
**Amends:** ADR 148 §1's *"the campaign is the only layer allowed to know both"*

## Context

The library was finished and nothing could start it. `pyproject.toml` declared no
`console_scripts`, there was no `__main__`, and `campaign_for` — twenty-five
required arguments — was called from exactly two files, both tests.

That is not a cosmetic gap. The only code that knew how to start this system was
code that also asserted about it, so the first real invocation would have been
somebody hand-assembling twenty-five values correctly, for the first time, on the
day the run costs money.

## Decision

**`coldfix.toml` supplies the fourteen values that are not objects**, and
`cli/config.py` refuses a file that cannot produce them, naming the section and
the key. A `KeyError` on `"database_url"` tells somebody a dictionary lacked a
key; *`[subject].database_url` is required* tells them what to type, and this is
the first file a new user writes.

**The adapter supplies four more, and `cli/wiring.py` unpacks it.** That module
is exempt from *the core must never import an adapter* and it is the only
exemption. `EXEMPT` is a frozenset of one, and a test asserts its exact contents
— an exemption list is the thing that erodes, because each addition is defensible
alone and the invariant is gone after four. A second test asserts the exempt
module actually imports an adapter, so a stale exemption cannot sit there
protecting nothing.

`orchestrator/assembly.py` declined to be this layer on the grounds that widening
a layering invariant *as a side effect of a story about something else* is too
expensive. That reasoning is why the widening happened here: this story was about
exactly that.

## `plan` cannot spend, and `run` will not without being told

**`plan` is the default and makes no model call.** It reads the file, resolves
the adapter, asks it for what it supplies, and reports. It deliberately does less
than `campaign_for`: opening a workbench needs Docker and opening the store needs
Postgres, and a command whose entire purpose is *have I configured this
correctly* should not require both to answer. It is also the only command that
works on a laptop with neither.

**`run` refuses without `--spend`, and refuses again without a credential.** The
flag is not a confirmation prompt — prompts are answered by habit, and this one
has to be typed on purpose. The credential is checked *before* anything opens, so
the failure is a message rather than a container and a database stood up for
nothing.

`ANTHROPIC_API_KEY` is read in `cli/main.py` and nowhere else under `src/`. A
library that reached for an environment variable would be a library that could
start spending because of something in a shell profile.

## `run` raises rather than pretending

Everything up to the point of handing values to `campaign_for` is built. The
handing-over is not, and `run` says so instead of running an untested path.

That is deliberate. Wiring a path nobody has executed and calling it done is how
a system arrives at its first real invocation with the confidence of code that
has never run — which is the failure this project has recorded at every epic
join. S-17.1 is the story that executes it, and it is a story precisely because
it costs money and has never happened.

## Consequences

`coldfix plan` works today against `coldfix.example.toml` and reports a real
Django campaign: adapter resolved, counters read off its declarations,
capabilities counted, ceiling shown, and whether the framework is groundable at
all — which is a different question from whether it has an adapter, and S-14.6 is
why the plan shows both. Pointing it at Flask reports an adapter with no grounding
support, and says the run would be refused at the fingerprint.

A test loads the shipped example, because it is the file the *no configuration*
refusal points people at and the one most likely to drift — nothing else reads
it.

What this does not do is make the system runnable end to end. It makes the run
one command **when there is a run**, which is the thing that was missing.
