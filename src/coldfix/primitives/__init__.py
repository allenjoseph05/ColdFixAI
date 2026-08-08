"""Experiment types the Diagnostician composes.

A primitive is a way of constructing a contrast between two executions — not a
detector. Detectors have a ceiling equal to their list; primitives compose.

The registry is the one designed extension point in this codebase.

Epic 3. See `docs/01-primitives.md`.

**Importing this package registers every primitive, and that is the point.**
Registration happens as a side effect of importing the module that declares it,
so before this the contents of `REGISTRY` depended on what a process happened to
have imported first: a caller that imported `scaling` and `bounds` got a
two-instrument toolkit and a `Selection` that listed nothing missing, because a
primitive nobody imported is not withheld — it does not exist. Absent and
inapplicable are the two answers ADR 030 went to trouble to separate, and an
import order silently produced the wrong one.

That also broke the guarantee `Selection` is built on. It is a snapshot so the
tool list cannot change mid-investigation (ADR 002: a list that grows invalidates
the cached prefix behind it), and nothing made the list *complete* at the moment
the snapshot was taken. Found by composing the epic; `tests/primitives/
test_epic_composed.py` asserts that every module registering a primitive is
imported here, so the next one cannot be forgotten.

**This costs about a second, measured, and the trade is deliberate.** Importing
the package pulls in every implementation: `-X importtime` puts it at ~1.05s, of
which ~690ms is the sandbox chain `ablation` needs for `DiagnosticSession` and
~290ms is Hypothesis, which S-3.17's engine is. Paid once per process, and a
process that runs an investigation runs for hours. It would matter for a CLI, and
the fix if it ever does is a declarative manifest the registry can render without
importing the implementation, with the module imported when the primitive is
actually run. That is a change to the registry's contract and belongs in a story
rather than here; noted so the second is on the record rather than discovered.
"""

from coldfix.primitives import (
    ablation,
    bounds,
    faults,
    input_search,
    instructions,
    isolation,
    load,
    longitudinal,
    perturbation,
    scaling,
    search,
    temporal,
)

__all__ = [
    "ablation",
    "bounds",
    "faults",
    "input_search",
    "instructions",
    "isolation",
    "load",
    "longitudinal",
    "perturbation",
    "scaling",
    "search",
    "temporal",
]
