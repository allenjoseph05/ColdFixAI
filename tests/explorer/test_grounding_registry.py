"""Grounding asks the adapter, not Django. **S-14.6.**

Epic 14's claim is that core is framework-neutral, and ADR 148 §1 recorded three
places where it was not: `compose.py` called Django's entry-point enumerator
directly, `stages.PREDICATES` held one entry, and `Framework.supported` was
`self is Framework.DJANGO`.

The tests that matter here are the two absences — that core no longer names a
framework, and that a framework nobody has taught this system to ground is
refused rather than half-attempted — plus the import-order hazard a push-based
registry brings with it, which this project has already been bitten by once at
ADR 050.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, cast

import pytest

import coldfix.adapters  # noqa: F401 - importing the package is what registers the adapters
from coldfix.explorer import registry
from coldfix.explorer.entrypoints import Enumeration, enumerate_entry_points
from coldfix.explorer.fingerprint import Detected, Fingerprint, Framework, Unsupported, fingerprint
from coldfix.explorer.registry import (
    Grounds,
    RegistryError,
    groundable,
    grounds_for,
    register,
    registered,
)
from coldfix.explorer.stages import (
    FRAMEWORK_NEUTRAL_PREDICATES,
    Grounding,
    Outcome,
    Stage,
    StageError,
    predicates_for,
)
from coldfix.explorer.stages import Verdict as StageVerdict

SRC = Path(__file__).resolve().parents[2] / "src" / "coldfix"
ADAPTERS = SRC / "adapters"


def _names_django(path: Path) -> int:
    """How many times this module evaluates `Framework.DJANGO` **as code**.

    Comments and docstrings are invisible to `ast`, which is what makes this
    usable on modules that explain the leak they no longer have.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "DJANGO"
        and isinstance(node.value, ast.Name)
        and node.value.id == "Framework"
    )


def _module_level_names(path: Path) -> set[str]:
    """Names this module binds at module scope."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bound: set[str] = set()
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else []
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        bound.update(t.id for t in targets if isinstance(t, ast.Name))
    return bound


def _imported_names(path: Path) -> set[str]:
    """Every name this module imports, however it spells the import."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom | ast.Import):
            names.update(alias.asname or alias.name for alias in node.names)
    return names


@pytest.fixture(name="restored")
def _restored() -> Iterator[None]:
    """Put the registry back afterwards.

    The registry is process-global by design — adapters push into it at import —
    so a test that registers a framework has to undo it or the next test sees a
    framework nothing in `src/` supplies. Snapshotting the module's own dict
    rather than adding a `clear()` to production: a reset function exists only
    for tests, and something would eventually call it in a run.
    """
    before = dict(registry._REGISTERED)
    try:
        yield
    finally:
        registry._REGISTERED.clear()
        registry._REGISTERED.update(before)


def a_predicate(stage: Stage, note: str) -> Any:
    def predicate(grounding: object, payload: Mapping[str, object]) -> Outcome:
        del grounding, payload
        return Outcome(stage, StageVerdict.HOLDS, note)

    return predicate


def full_table(note: str) -> dict[Stage, Any]:
    return {stage: a_predicate(stage, note) for stage in Stage}


def a_fingerprint(framework: Framework) -> Fingerprint:
    return Fingerprint(
        root=Path(),
        framework=Detected(value=framework, evidence="a test said so"),
        declared_version=None,
        orm=None,
        database=None,
        test_runner=None,
    )


# ================================== AC 3: the gate is a registry, not an enum property


def test_a_framework_nobody_registered_is_refused_by_name() -> None:
    """**`Framework.supported` said Django.** That was a fact about this project's
    history rather than about the repository in front of it, and it put a
    framework's name in core. The gate now asks what an adapter registered."""
    assert groundable(Framework.DJANGO)
    assert not groundable(Framework.FLASK)


def test_the_refusal_names_what_is_missing_rather_than_which_framework(tmp_path: Path) -> None:
    """AC 3. *This is Flask* is a fact; *nothing has taught this system to ground
    Flask* is the thing a reader can act on, and listing what **is** registered
    turns it from a dead end into a comparison.

    The sentence this replaced pointed at S-14.3 as the story that would add a
    second adapter. That landed in August, so it was telling readers to wait for
    something that had already happened.
    """
    root = tmp_path / "subject"
    root.mkdir()
    (root / "requirements.txt").write_text("flask>=3.0\n", encoding="utf-8")

    found = fingerprint(root)

    assert isinstance(found, Unsupported)
    assert "nothing has taught this system to ground it" in found.reason
    assert "registered so far: Django" in found.reason


def test_a_registered_framework_is_admitted(restored: None) -> None:
    """The control. A gate that refused everything would pass the test above."""
    del restored
    register(Grounds(Framework.FLASK, _flask_enumeration, full_table("flask")))

    assert groundable(Framework.FLASK)
    assert "Flask" in registered()


# ============================ AC 2: the predicates are the adapter's, not core's


def test_core_holds_no_framework_named_predicate_table() -> None:
    """`stages.py` had `_DJANGO_PREDICATES` and `PREDICATES = {Framework.DJANGO: ...}`.

    Read as code rather than as text. Both names still appear in this module's
    prose, saying what used to be there, and a substring check would fail on the
    explanation of its own fix.
    """
    bound = _module_level_names(SRC / "explorer" / "stages.py")

    assert "_DJANGO_PREDICATES" not in bound
    assert "PREDICATES" not in bound
    assert not _names_django(SRC / "explorer" / "stages.py")


def test_six_of_the_nine_predicates_are_framework_neutral() -> None:
    """**The surprise, asserted so it is not undone by a later tidy-up.**

    `_DJANGO_PREDICATES` was named for a framework and six of its nine members
    read a payload the subject was probed for and answer in terms nothing
    Django-specific appears in. Only `_clone`, `_endpoint` and `_configure` are
    actually Django's — two reach for its enumerator and one runs `manage.py
    check`. Moving all nine to the adapter would have made every future adapter
    restate six identical functions.
    """
    assert set(FRAMEWORK_NEUTRAL_PREDICATES) == {
        Stage.DEPENDENCIES,
        Stage.CONNECT,
        Stage.MIGRATE,
        Stage.AUTH,
        Stage.SEED,
        Stage.WORK,
    }


def test_a_second_frameworks_predicates_are_the_ones_reached(restored: None) -> None:
    """AC 2. The lookup goes through the registry, so a framework that registers
    its own table gets its own answers rather than Django's."""
    del restored
    register(Grounds(Framework.FLASK, _flask_enumeration, full_table("from the flask table")))

    table = predicates_for(a_fingerprint(Framework.FLASK))
    # The predicate ignores both arguments; what is under test is which table
    # the lookup returned, not what the predicate does with a `Grounding`.
    outcome = table[Stage.CONFIGURE](cast("Grounding", None), {})

    assert outcome.detail == "from the flask table"


def test_an_incomplete_predicate_table_is_refused_at_registration(restored: None) -> None:
    """AC 5. `evaluate` measures all nine, so a partial table is a `KeyError` in
    the middle of a run rather than a framework that is partly supported.

    Refused where it is declared, because that is the only moment the adapter's
    author is present to read the message.
    """
    del restored
    partial = full_table("flask")
    del partial[Stage.SEED]

    with pytest.raises(RegistryError, match="no predicate for"):
        register(Grounds(Framework.FLASK, _flask_enumeration, partial))


def test_registering_one_framework_twice_is_refused(restored: None) -> None:
    """Whichever import ran last would win, silently."""
    del restored
    register(Grounds(Framework.FLASK, _flask_enumeration, full_table("first")))

    with pytest.raises(RegistryError, match="already registered"):
        register(Grounds(Framework.FLASK, _flask_enumeration, full_table("second")))


def test_a_detected_framework_with_no_predicates_still_refuses_clearly() -> None:
    """AC 5's other half: the refusal stays a refusal at the partial state.

    ADR 148 warned that admitting Flask at the fingerprint without doing the rest
    would turn a clear refusal into a `KeyError` on `PREDICATES[Framework.FLASK]`.
    It would not have — `predicates_for` already read `.get(...)` — and it still
    does not, which is asserted here rather than believed.
    """
    with pytest.raises(StageError, match="nothing has taught this system to ground Flask"):
        predicates_for(a_fingerprint(Framework.FLASK))


# ================================ AC 1: the enumeration comes from the adapter


def test_the_django_adapter_registers_the_enumerator_grounding_uses() -> None:
    """AC 1's structural half. `compose.py` called `enumerate_entry_points`
    directly; it now asks the registry, and this is what the registry hands it."""
    grounds = grounds_for(Framework.DJANGO)

    assert grounds is not None
    assert grounds.enumerate_entry_points is enumerate_entry_points


def test_compose_does_not_import_the_enumerator_it_used_to_call() -> None:
    """The absence, read off the imports. A module that still imported it could
    still call it, and the registry lookup beside it would look like the seam
    while the direct call did the work.

    The imports specifically, not the text: `grounds.enumerate_entry_points` is
    the registry attribute this now goes through, and it is spelled the same.
    """
    imported = _imported_names(SRC / "explorer" / "compose.py")

    assert "enumerate_entry_points" not in imported


# ============================== AC 4: core does not name a framework any more


def test_no_module_outside_adapters_names_django_except_the_detector() -> None:
    """AC 4, swept across the tree rather than asserted on the three sites ADR 148
    listed — the point is that a fourth cannot appear quietly.

    `fingerprint.py` is the one exception and it is a real one: *detecting*
    Django means knowing that `manage.py` exists and that a requirement named
    `django` is a signal, which is knowledge about Django that has to live
    somewhere and cannot live in an adapter that is only reachable once the
    framework is known.
    """
    offenders: dict[str, int] = {}
    for path in sorted(SRC.rglob("*.py")):
        if ADAPTERS in path.parents or path.name == "fingerprint.py":
            continue
        if _names_django(path):
            offenders[str(path.relative_to(SRC))] = _names_django(path)

    assert not offenders, f"core still names Django outside the detector: {offenders}"


def test_a_process_with_no_adapters_refuses_everything_and_says_so(
    restored: None, tmp_path: Path
) -> None:
    """**The reachable failure state, named rather than left to be discovered.**

    Core may not import an adapter — `test_no_core_module_imports_an_adapter`
    holds that line — so nothing under `src/` can guarantee this registry has
    anything in it. A process that never imported `coldfix.adapters` grounds
    nothing, and the honest thing is for the refusal to say which frameworks are
    registered rather than to name the one in front of it.

    Asserted because *refuses everything* is a bad state to reach silently, and
    `registered so far: none` is the sentence that turns it into a diagnosis.
    """
    del restored
    registry._REGISTERED.clear()
    root = tmp_path / "subject"
    root.mkdir()
    (root / "requirements.txt").write_text("django>=5.0\n", encoding="utf-8")

    found = fingerprint(root)

    assert isinstance(found, Unsupported)
    assert "registered so far: none" in found.reason


# ======================= the import-order hazard a push registry brings with it


def _adapter_modules_that_register() -> set[str]:
    """Every module under `adapters/` containing a top-level `register(` call."""
    found = set()
    for path in sorted(ADAPTERS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            call = node.value if isinstance(node, ast.Expr) else None
            if isinstance(call, ast.Call) and getattr(call.func, "id", None) == "register":
                found.add(path.stem)
    return found


def test_every_adapter_that_registers_is_imported_by_the_package() -> None:
    """**ADR 050's construction, for ADR 050's reason, a second time.**

    Registration is an import side effect, so the registry's contents depend on
    what a process happened to import. A framework whose adapter nobody imported
    is not *withheld* — it does not exist, and *absent* reads exactly like
    *unsupported* at the fingerprint gate, which is a wrong answer that looks
    like a right one.

    Read from the filesystem rather than from a list, because a list in a test is
    forgotten at the same moment as the import it was meant to guard.
    """
    init = (ADAPTERS / "__init__.py").read_text(encoding="utf-8")

    registering = _adapter_modules_that_register()

    assert registering, "no adapter registers anything; this test would pass on an empty tree"
    unreachable = {name for name in registering if f"import {name}" not in init}
    assert not unreachable, f"these adapters register and nothing imports them: {unreachable}"


def test_django_is_groundable_only_because_something_registered_it() -> None:
    """The property the test above protects, stated as behaviour.

    If this ever passes with an empty registry, the gate has grown a default and
    `Framework.supported` is back under another name.
    """
    assert groundable(Framework.DJANGO)
    assert grounds_for(Framework.DJANGO) is not None
    assert "Django" in registered()


def _flask_enumeration(*args: object, **kwargs: object) -> Enumeration:
    """A stand-in enumerator. Never called — the tests using it assert on the
    registration, not on what it returns."""
    del args, kwargs
    message = "the stand-in enumerator was called"
    raise AssertionError(message)
