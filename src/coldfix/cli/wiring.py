"""The one module that knows both the core and the adapters. **S-17.18.**

`test_no_core_module_imports_an_adapter` holds that *adapters import the core;
the core must never import an adapter*, and it exempts exactly this file. The
exemption is narrow on purpose and a second test asserts the exemption list has
one entry, so it cannot grow quietly into the erosion it is designed to survive.

**Why there has to be one such place.** `campaign_for` takes what an adapter
supplies — reset candidates, capabilities, counters, a workload — rather than the
adapter itself, precisely so core stays clean. Something has to do the unpacking,
and ADR 148 §1 already named what: *the campaign is the only layer allowed to
know both*. `orchestrator/assembly.py` declined to be that layer, on the grounds
that widening a layering invariant as a side effect of a story about something
else is too expensive. This story is about exactly that, so it pays the cost here
and nowhere else.

**The registry decides what can be grounded; this decides what implements it.**
S-14.6's `explorer/registry.py` answers *has anything taught this system to
ground Django* — a question about grounding. This answers *which class is the
Django adapter*, which is a question about wiring, and the two are separate:
a framework could in principle be groundable by one supplier and driven by
another. Importing this module is also what populates that registry, because
`coldfix.adapters` registers on import.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from coldfix.adapters import HARNESS_CAPABILITIES, FrameworkAdapter, Subject
from coldfix.adapters.django import DjangoAdapter
from coldfix.adapters.flask import FlaskAdapter

if TYPE_CHECKING:
    from collections.abc import Sequence

    from coldfix.primitives.registry import Capability
    from coldfix.sandbox.reset import ResetMechanism


class WiringError(Exception):
    """No adapter implements the framework this configuration names."""


ADAPTERS: Mapping[str, type[FrameworkAdapter]] = {
    "Django": DjangoAdapter,
    "Flask": FlaskAdapter,
}
"""Framework name to the class that implements it.

Written out rather than discovered by scanning, because a scan that found nothing
would be indistinguishable from a framework with no adapter — the same *absent
reads like unsupported* confusion S-14.6 recorded for the grounding registry. A
test reads `adapters/` for classes declaring a `framework` and asserts each one
appears here, which keeps the list honest without making it magic."""


def adapter_for(framework: str) -> FrameworkAdapter:
    """The adapter implementing this framework.

    Raises:
        WiringError: nothing implements it. Names what is available, because the
            likely cause is a typo in `[project].framework` and the fix is
            visible the moment the alternatives are on screen.
    """
    found = ADAPTERS.get(framework)
    if found is None:
        known = ", ".join(sorted(ADAPTERS)) or "none"
        message = (
            f"no adapter implements {framework!r}. This system can drive: {known}. "
            "The framework in the configuration must match one of those exactly"
        )
        raise WiringError(message)
    return found()


def supplied_by(
    adapter: FrameworkAdapter, *, root: Path, python: Sequence[str], path: str
) -> dict[str, object]:
    """The four arguments `campaign_for` takes from an adapter, unpacked here.

    Lifted from `tests/orchestrator/test_assembly.py`, which has performed this
    unpacking since S-17.15 and carries the comment explaining why it is the
    caller's job. A test doing it was the whole problem: the only code that knew
    how to start this system was code that also asserted about it.
    """
    subject = Subject(root=root, python=python)
    capabilities: frozenset[Capability] = frozenset(adapter.capabilities()) | HARNESS_CAPABILITIES
    resets: Sequence[ResetMechanism] = adapter.reset_state(subject)

    def workload() -> object:
        """One drive of the subject, at the scale the screen starts from."""
        return adapter.run_workload(
            subject,
            entry_point=path,
            scale=1,
            created={},
            repeats=1,
            timeout=300.0,
        )

    return {
        "framework": adapter.framework.value,
        "reset_candidates": resets,
        "capabilities": capabilities,
        "counters": tuple(sorted(adapter.declarations.hooks)),
        "workload": workload,
    }
