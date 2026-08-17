# 046 — The fuzzer we wrap is Hypothesis, because AFL guides on the wrong thing

**Status:** accepted
**Story:** S-3.17 — input space search
**Date:** 2026-08-08

## Context

S-3.17's first two acceptance criteria are:

> - Wraps an existing fuzzer (AFL-based) rather than implementing mutation from scratch
> - Fitness function rewards resource consumption, not coverage

The note under the story is emphatic: *do not write a fuzzer*. That part is not
in tension with anything. The parenthetical "(AFL-based)" is.

**AFL is coverage-guided.** That is what it is for, and it is exactly why
SlowFuzz and PerfFuzz — both cited in `01-primitives.md` §14 — are AFL *forks*
rather than AFL configurations: making the fitness function reward resource
consumption meant changing the engine. Neither fork is packaged as a Python
library, and neither instruments CPython.

**And the AFL-lineage Python engines do not run here.** Measured, not assumed:

```
uv pip install atheris
  building atheris from sdist (no wheel for this platform)
  error: [WinError 193] %1 is not a valid Win32 application
```

`python-afl` installs but drives the `afl-fuzz` binary, which is Unix-only. So a
literal reading of the AC produces a primitive that cannot run on the development
machine, must be shipped inside a container for every test, and *still* searches
by coverage — leaving cost to rank the corpus afterwards, which is the weaker
thing SlowFuzz exists to improve on.

`hypothesis.target()` is the other established option: targeted property-based
testing, from Löscher & Sagonas (ISSTA 2017), where the caller supplies the
fitness function the engine maximises. It is an existing search engine. Wrapping
it writes no mutation code.

## Decision

Wrap Hypothesis. `hypothesis` becomes a project dependency rather than a dev one,
because the primitive is product code. The AC's "(AFL-based)" is not met and is
recorded here as deliberately not met; "wraps an existing fuzzer" and "fitness
rewards resource consumption" both are, and the second could not have been met
the other way.

`search` takes a `SearchStrategy` and there is no parameter that accepts inputs.
That is what keeps the note honoured structurally: a caller cannot hand this
module a corpus and turn it into the generator.

## Consequences

**Hypothesis hill-climbs numeric draws only, and this is load-bearing.** From
`hypothesis/internal/conjecture/optimiser.py`:

```python
# we can only (sensibly & easily) define hill climbing for numeric-style nodes.
if node.type not in {"integer", "float", "bytes", "boolean"}:
    continue
```

Measured against a subject whose cost is the square of its `a` count, six seeds
each:

| strategy | unguided worst | guided worst |
|---|---|---|
| `st.text(alphabet="ab")` | 306 | 306 |
| `st.lists(st.integers())` | 39 | 113 |

A text campaign is *silently* a random sample. The module docstring says so, and
a test asserts the equality — so that a Hypothesis release which fixes this fails
the test rather than quietly changing what the primitive claims. Callers fuzz
numbers, lists of numbers, or bytes decoded into text.

**Guidance comes from the observation, not from the phase.** Sabotaging
`Phase.target` out of the settings did not change the outcome: the generation
phase also refuses mutations that lower an observed target, so the campaign
stayed guided. Removing the `target()` call did change it. The phase is therefore
unconditional — with no observations recorded the optimiser finds nothing to
climb and spends no calls — and `guided=False` means only that nothing is
observed.

**The time cap is checked between examples, and cannot be tighter.** A single
input that takes an hour overruns by an hour. There is no version of this
primitive where that is untrue: it is hunting for slow inputs, so the mechanism
that would cut one short is the mechanism that would discard the answer.
Hypothesis's own per-example `deadline` is switched off for the same reason — at
its 200ms default it fails the campaign on precisely the input the campaign was
run to find.

**A campaign is a candidate generator, not a finding generator.** One sample per
input sits below the ~20ms timing floor S-0.4 measured, and the search then
selects the maximum of a noisy population, which is the shape of a false
positive. `confirm` hands the champion and an equally large input to S-1.6's
interleaved comparison, and only that is allowed to say one is slower.
