"""Which frameworks can be grounded, and what each one supplies. **S-14.6.**

Epic 14's claim is that core is framework-neutral. Three places in core were
not: `compose.py` called Django's entry-point enumerator directly,
`stages.PREDICATES` held one entry, and `Framework.supported` was
`self is Framework.DJANGO`. ADR 148 §1 named all three and filed the fix here.

**Adapters push into this; nothing here pulls from them.** `adapters/django.py`
registers itself at import, and `explorer/` never imports `adapters/`. That is
what makes a registry in this package possible at all — ADR 148 said it would
have to live at the campaign layer *because adapters import
`explorer.fingerprint`*, which is true and does not follow: the cycle would need
this module to reach back down, and it does not.

**The import-order hazard is real and is the same one ADR 050 recorded.** A
framework whose adapter nobody imported is not withheld — it does not exist, and
*absent* reads exactly like *unsupported*. The mitigation is ADR 050's, which
this project has already proven once: `adapters/__init__.py` imports every
adapter module, and a test reads that directory for `register(` and asserts each
module it finds is reachable — from the filesystem rather than from a list,
because a list in a test is forgotten at the same moment as the import.

**Core cannot populate this, and that follows from a rule this project already
enforces.** `test_no_core_module_imports_an_adapter` holds that *adapters import
the core; the core must never import an adapter*, so no module under `src/`
outside `adapters/` may trigger the registration. Whoever assembles a run does —
the same contract `coldfix.primitives` has, one layer further out. The visible
consequence is that `fingerprint` refuses every repository in a process that has
not imported `coldfix.adapters`, and the refusal says `registered so far: none`,
which is the true statement about that process.

**Nothing here imports `Framework`, `Stage` or `Predicate` at runtime.**
`fingerprint` and `stages` both consult this module, so importing either back
would be the cycle this design exists to avoid. `Framework` is a `StrEnum`, so a
member and its value hash alike and a plain string key serves both callers.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # annotations only — see the module docstring
    from coldfix.explorer.entrypoints import Enumeration
    from coldfix.explorer.fingerprint import Framework
    from coldfix.explorer.stages import Outcome, Stage


class RegistryError(Exception):
    """A framework was registered twice, or asked for and not found."""


type Enumerator = Callable[..., Enumeration]
"""How a framework's entry points are found. Django parses `urls.py`; Flask reads
decorators off files. Both return an `Enumeration`, which is the whole point of
the interface — see ADR 148 §2."""

type Predicates = Mapping[Stage, Callable[[Any, Mapping[str, object]], Outcome]]
"""ADR 009's nine questions, answered in one framework's own terms."""


@dataclass(frozen=True)
class Grounds:
    """What grounding needs from a framework, supplied by its adapter.

    Named for what it answers — *can this be grounded, and with what* — rather
    than for the adapter, because an adapter is a great deal more than this and
    only this part is grounding's business.
    """

    framework: str
    enumerate_entry_points: Enumerator
    predicates: Predicates


_REGISTERED: dict[str, Grounds] = {}


def register(grounds: Grounds) -> None:
    """Declare that this framework can be grounded.

    Raises:
        RegistryError: the predicate table is missing a stage, or this framework
            is already registered. Two adapters for one
            framework is a configuration nobody can resolve — whichever import
            ran last would win, silently, and the run would use predicates the
            reader cannot identify from the code.
    """
    # **Imported here rather than at module scope, and the reason is this
    # module's whole shape.** `stages` consults the registry, so importing it
    # back at import time is the cycle the docstring above is about. By the time
    # an adapter registers, `stages` is loaded and this is a dictionary lookup.
    from coldfix.explorer.stages import Stage  # noqa: PLC0415 - deliberately local

    missing = sorted(stage.value for stage in Stage if stage not in grounds.predicates)
    if missing:
        message = (
            f"{grounds.framework} registered grounding support with no predicate for {missing}. "
            "`evaluate` measures all nine of ADR 009's questions, so a partial table is a "
            "KeyError in the middle of a run rather than a framework that is partly supported"
        )
        raise RegistryError(message)

    key = str(grounds.framework)
    if key in _REGISTERED:
        message = (
            f"{key} is already registered. Two sets of grounding support for one "
            "framework leaves the answer decided by import order, which is not something a "
            "reader can determine from the code"
        )
        raise RegistryError(message)
    # Keyed by the string, so a caller holding a `Framework` and one holding its
    # value reach the same entry — which is what lets this module stay free of a
    # runtime import of the enum.
    _REGISTERED[key] = grounds


def grounds_for(framework: Framework | str) -> Grounds | None:
    """What this framework supplies, or `None` if nothing registered it.

    `None` rather than a raise, because *no adapter for this* is an ordinary
    answer at the gate — `fingerprint` turns it into a refusal that names the
    framework, and that refusal is a result rather than an error.
    """
    return _REGISTERED.get(str(framework))


def groundable(framework: Framework | str) -> bool:
    """Whether an adapter has taught this system to ground this framework.

    **This replaced `Framework.supported`, and the difference is the point.**
    That property said *Django*, which was a fact about this project's history
    rather than about the repository in front of it. This says *something
    registered grounding support*, which is a fact about what is installed — and
    it stops being true for Django the moment nothing supplies it, which is what
    makes the import-order test above load-bearing rather than decorative.
    """
    return str(framework) in _REGISTERED


def registered() -> tuple[str, ...]:
    """Every framework with grounding support, sorted. For reports and for tests."""
    return tuple(sorted(_REGISTERED))
