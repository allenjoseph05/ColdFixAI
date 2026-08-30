"""Epic 7, composed: one unknown repository, all the way to an emitted workload.

Epic 7, S-7.13. The epic's goal is one sentence — *turn an unknown repository
into a runnable, scalable, resettable workload* — and the composition check that
closed the epic proved it can be performed. **It proved it in a test.**

That was enough while the only caller was the check itself. It stopped being
enough at S-12.7, where the orchestrator's `ground` node needs something to call
and `tests/` is not importable from `src/`. `GroundingRun` is a driver the
Explorer steps through rather than an entry point, and it is constructed in
`tests/explorer/test_run.py` and in no module here. So this is the sequence
lifted out of the check and given a name.

**Nine modules in the order a caller would use them**, which is the order the
composition check established: fingerprint → anchor → interpreter → routes →
auth → fixtures → resolution → verification → emission. Each value is fed to the
next exactly as it comes out, and where that does not work the join is the
defect — which is how the check found six of them.

**A seventh, found by this story and fixed here.** The check asserts
`resolution.resolved` and then calls `verify_work` with no `headers` and no
`cookies`. On its own subject every route is open, so the credential it mints is
never needed and its absence changes nothing — but `attach` exists precisely to
turn a `Credential` into the two mappings a subsequent request carries, and
nothing was calling it. A route that actually required auth would have been
minted a credential, driven without it, and measured whatever a 401 costs.
**The seventh instance of one shape**: a value one story produces and another
consumes, with neither story's tests holding both ends.

**What this decides and what it refuses to.** It sequences; it does not choose.
Which entity a route serves is the Explorer's to know — `prefer` grew an
`entity` argument because ranking a mechanism by how well it seeds two scales is
a property of the mechanism and not of the workload, and the alphabetical
tie-break that followed seeded a hundred authors and drove `/books/`. So the
plan arrives from the caller, and this refuses rather than guesses.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from pydantic import JsonValue

from coldfix.explorer.anchor import Anchor, Interpreter, anchor_for, interpreter_for, resolve
from coldfix.explorer.auth import (
    Credential,
    PlaybookLookup,
    Reply,
    Resolution,
    TrustedLookup,
    attach,
    no_playbook,
    no_trusted,
    resolve_auth,
)
from coldfix.explorer.emission import EmissionError, EmittedWorkload, emit
from coldfix.explorer.entrypoints import Enumeration
from coldfix.explorer.fingerprint import Detected, Fingerprint, Identification, fingerprint
from coldfix.explorer.fixtures import Mechanism, discover, factory_seeder, prefer
from coldfix.explorer.playbook import (
    PlaybookWriter,
    UseRecorder,
    learned_from_auth,
    no_record,
    no_use,
)
from coldfix.explorer.registry import grounds_for
from coldfix.explorer.stages import Grounding, Progress, evaluate
from coldfix.explorer.surface import HostSurface, Surface
from coldfix.explorer.work import Seeder, Verification, WorkVerificationError, verify_work
from coldfix.sandbox.reset import ResetStrategy
from coldfix.sandbox.verification import VerifiedReset
from coldfix.screening.workload import EnvironmentAnchor, Workload


class CompositionError(Exception):
    """One repository could not be taken through the sequence."""


class NotGroundableError(CompositionError):
    """The run stopped at a stage, and the stage says which.

    Separate from every module's own error because the answer *this repository
    cannot be ground* is a result rather than a fault — `00-BRIEF.md` §9 makes
    a repository the Explorer honestly failed on an answer, and S-7.11's own
    acceptance is that it *reports failure on a fourth rather than claiming
    success on empty data*. What is refused here is carrying on with a value the
    previous stage did not produce.
    """


@dataclass(frozen=True)
class Plan:
    """What the Explorer decides and this sequence will not infer.

    Every field is something a module refuses to guess, and most of them were
    made explicit by a defect. `entity` is the sharpest: without it `prefer`
    ties two equally-good factories and breaks the tie alphabetically, which
    composed means seeding the wrong table and measuring an empty list.
    """

    workload_id: str
    description: str
    entity: str | None = None
    """Which entity the route serves, for `prefer`. `None` leaves the ranking to
    decide, which is correct only where the repository ships one factory."""

    factory_module: str | None = None
    """Where the chosen factory is importable from. `None` means synthesise even
    if a mechanism was found — a mechanism this cannot import is not one this can
    seed with."""

    target: str | None = None
    requirements: Sequence[str] = ()
    reset: ResetStrategy = ResetStrategy.SNAPSHOT_RESTORE
    reset_between: Sequence[str] | None = None
    repeats: int = 3
    headers: Mapping[str, str] = field(default_factory=dict)
    cookies: Mapping[str, str] = field(default_factory=dict)
    """Carried on every request *in addition to* whatever auth resolution minted.
    The credential wins on a collision, which is `attach`'s rule and not this
    module's: a caller supplying its own `Authorization` beside a token
    credential has two credentials and one slot."""


@dataclass(frozen=True)
class Grounded:
    """One repository, ground, with everything the sequence established.

    Holds the intermediate artifacts rather than only the emitted workload,
    because a caller that has to report *why this stopped* needs them and the
    envelope carries none of it.
    """

    identification: Fingerprint
    anchor: Anchor
    interpreter: Interpreter | None
    enumeration: Enumeration
    auth: Resolution
    verification: Verification
    reset: VerifiedReset
    """The measurement and the proof, carried because **`emitted` cannot be taken
    apart again** and S-7.14's loop needs both: `GroundingRun.finish` is the only
    way a run succeeds and it takes these two, not the document they produced.
    Without them a driver has two choices and both are worse — invent a second
    success path, or re-run a sweep it has already paid for."""

    emitted: EmittedWorkload
    progress: Progress

    @property
    def workload(self) -> Workload:
        return self.emitted.workload

    def facts(self) -> Mapping[str, JsonValue]:
        """The project facts, in the shape `CheckpointedState.project` names.

        *Fingerprint, adapter and workspace path.* Flattened to JSON here rather
        than by the orchestrator, because what a fingerprint is made of is this
        epic's subject and a node that reached into it would be a second place
        that has to change when it grows a facet.
        """
        found = self.identification
        return {
            "root": str(found.root),
            "framework": found.framework.value.value,
            "declared_version": _detected(found.declared_version),
            "orm": _detected(found.orm),
            "database": _detected(found.database),
            "test_runner": _detected(found.test_runner),
            "undetermined": list(found.undetermined),
            "anchor": self.anchor.on.isoformat(),
            "interpreter": None if self.interpreter is None else self.interpreter.version,
        }


def ground_workload(  # noqa: PLR0913 - the repository, how to run it, how to
    # request from it, what the Explorer decided, the reset proof and what has
    # been learned about projects of this kind are six independent facts from
    # five different owners. Bundling them would invent a type whose only
    # purpose is to be unpacked here.
    root: Path,
    *,
    python: Sequence[str],
    request: Callable[[str], Reply],
    plan: Plan,
    reset: VerifiedReset,
    surface: Surface | None = None,
    playbook: PlaybookLookup = no_playbook,
    trusted_entries: TrustedLookup = no_trusted,
    learn: PlaybookWriter = no_record,
    used: UseRecorder = no_use,
) -> Grounded:
    """Take one repository from unknown to emitted workload. **AC 1.**

    `python` and `request` are the caller's because nothing under `src/` may
    reach the network or choose an interpreter on its own account — S-7.2's
    convention for commands and S-7.3's for interpreters, and the reason `Reply`
    is deliberately not an HTTP client.

    **`playbook` is S-13.1's third criterion — *retrieved into Explorer context
    at grounding*.** The seam was built at S-7.4 and nothing filled it: this
    function called `resolve_auth` without a key, so the consult never happened
    and `no_playbook` was not even reached. Defaulted to `no_playbook` rather than
    made required, because that is a real configuration — a first run against a
    fresh store has learned nothing — and because a function is what keeps
    *consulted and empty* distinct from *not consulted*, which S-13.5's learning
    curve depends on.

    **The key is the fingerprint's**, not one derived here. `playbook_key()` is
    framework and major version, and a second spelling of it would file entries
    where nothing looks for them.

    **`learn` is S-13.6's half, and it writes only what auth established.** S-13.1
    deliberately shipped no production writer, because *a wrong entry propagates
    silently to all future runs and compounds* and S-13.2 was the gate. That gate
    exists now, so this records — and everything it records is **provisional**,
    which is structural: nothing here can write an entry already believed, and
    three different projects have to agree before one is trusted.

    Recorded **after** the auth stage rather than at the end, because that is
    what it is about; a run that fails later has still learned whether this kind
    of project needs a credential.

    Raises:
        NotGroundableError: the repository is not a supported framework, has no
            route that can be requested, or needs a credential that could not be
            resolved. Each names the stage rather than the symptom.
        WorkVerificationError, EmissionError: the run reached verification and
            the work did not hold up. Left to travel, because *it ran and did
            nothing* is the finding `evidence_of_work` exists to produce.
    """
    # **One surface for the whole sequence, resolved once.** S-17.7 gave every
    # subject-facing step a `surface` parameter and nothing threaded one, so all
    # eight resolved `None` to the host and the decision never reached grounding.
    # Resolving here rather than per step is the point: a command and the
    # predicate that judges it have to agree about the filesystem, and two `or`
    # expressions in different functions are two places that can stop agreeing.
    where = surface or HostSurface(Path(root))

    identification = fingerprint(root)
    if not isinstance(identification, Fingerprint):
        raise NotGroundableError(_unsupported(identification))

    anchor = anchor_for(root)
    interpreter = interpreter_for(root)

    # **The framework's own enumerator, not Django's. S-14.6.** This line called
    # `enumerate_entry_points` directly, which is Django's — one of the three
    # places ADR 148 §1 recorded core still knowing a framework. The fingerprint
    # above already decided which framework this is, and `grounds_for` cannot
    # return `None` here because `fingerprint` refuses anything unregistered.
    grounds = grounds_for(identification.framework.value)
    if grounds is None:  # pragma: no cover - the gate above admits only registered frameworks
        raise NotGroundableError(_unsupported(identification))
    enumeration = grounds.enumerate_entry_points(root, python=python, surface=where)
    drivable = enumeration.drivable
    if not drivable:
        message = (
            f"{root} has no entry point that can be requested. The enumerator found "
            f"{len(enumeration.scored)} candidate(s), and a *parsed* route carries no address "
            "because a parse cannot establish what prefix an `include()` mounted it under"
        )
        raise NotGroundableError(message)

    best = drivable[0]
    path = best.request_path
    if path is None:  # pragma: no cover - `drivable` filters on exactly this
        message = f"{best.name} was reported drivable and has no request path"
        raise NotGroundableError(message)

    auth = resolve_auth(
        root,
        python=python,
        path=path,
        request=request,
        playbook=playbook,
        trusted_entries=trusted_entries,
        playbook_key=identification.playbook_key(),
        surface=where,
    )
    if not auth.resolved:
        message = (
            f"{path} needs a credential that could not be resolved.\n{auth.describe()}\n"
            "Driving it anyway would measure whatever a rejected request costs, which is a "
            "real measurement of the wrong thing"
        )
        raise NotGroundableError(message)

    learn(
        identification.playbook_key(),
        learned_from_auth(
            requirement=auth.requirement.scheme.name,
            credential=None if auth.credential is None else auth.credential.scheme.name,
            resolved=auth.resolved,
        ),
    )

    headers, cookies = carried(auth.credential, plan)
    try:
        verification = verify_work(
            root,
            python=python,
            path=path,
            workload_id=plan.workload_id,
            description=plan.description,
            reset=plan.reset,
            reset_between=plan.reset_between,
            target=plan.target,
            seed=_seeder(root, plan, where),
            environment=_environment(plan, anchor=anchor, interpreter=interpreter),
            headers=headers,
            cookies=cookies,
            repeats=plan.repeats,
            surface=where,
        )
        emitted = emit(verification, reset=reset)
    except (WorkVerificationError, EmissionError):
        _note(used, identification.playbook_key(), auth, worked=False)
        raise

    _note(used, identification.playbook_key(), auth, worked=True)

    return Grounded(
        identification=identification,
        anchor=anchor,
        interpreter=interpreter,
        enumeration=enumeration,
        auth=auth,
        verification=verification,
        reset=reset,
        emitted=emitted,
        progress=evaluate(
            identification,
            Grounding(root=root, python=python, surface=where, auth=auth, work=verification),
        ),
    )


def _note(used: UseRecorder, key: str, auth: Resolution, *, worked: bool) -> None:
    """Record how acting on a trusted entry went. **S-13.7's demotion path.**

    **Recorded here rather than in `resolve_auth`, because this is where the
    answer is.** A mint that succeeds says a user was created; it does not say the
    route accepted the credential — and F4's poisoned entry (*DRF always uses
    TokenAuthentication*) mints perfectly well in a session-authenticated project
    and then gets a `403` on every request. What settles it is the workload being
    driven, and that happens here.

    **Only a failure the credential could have caused counts as one.**
    `WorkVerificationError` and `EmissionError` are *the work did not hold up*,
    which is what a wrong scheme produces: `verify_work` refuses to measure an
    error response, so a route answering `403` throughout arrives as exactly this.
    Anything else — an interpreter that would not start, a database that went away
    — travels without a use being recorded, because it is not evidence about the
    entry. Two failures quarantine an entry, and spending one of them on a fact
    about the machine would demote a memory that was right.

    A run that acted on nothing records nothing. There is no *use* of an entry
    that was never read.
    """
    if auth.acted_on is None:
        return
    used(key, auth.acted_on, worked=worked)


def carried(
    credential: Credential | None, plan: Plan
) -> tuple[Mapping[str, str] | None, Mapping[str, str] | None]:
    """What a request carries. **The seventh join, and it was missing.**

    The composition check minted a credential, asserted it existed, and drove the
    route without it. That changed nothing on a subject whose every route is
    open, which is exactly why nothing caught it: **the defect is invisible until
    the route needs what was minted**, and then it measures whatever a rejected
    request costs — a real measurement of the wrong thing.

    Public because it is the join rather than an implementation detail, and a
    join with no test is how this one survived a composition check.
    """
    if credential is None:
        return (plan.headers or None, plan.cookies or None)
    return attach(credential, headers=plan.headers, cookies=plan.cookies)


def _seeder(root: Path, plan: Plan, surface: Surface) -> Seeder | None:
    """The repository's own factory where it has one, and synthesis where it does not.

    S-7.5's *use existing fixtures in preference to synthesis* was honoured inside
    its own module and nowhere else until the composition check; `None` here is
    what makes `verify_work` synthesise, which is the documented default rather
    than a fallback invented here.
    """
    if plan.factory_module is None:
        return None
    chosen = prefer(discover(root), entity=plan.entity)
    if not isinstance(chosen, Mechanism):
        return None
    return factory_seeder(chosen, module=plan.factory_module, surface=surface)


def _environment(
    plan: Plan, *, anchor: Anchor, interpreter: Interpreter | None
) -> EnvironmentAnchor | None:
    """The resolved dependency set, recorded on the artifact. S-7.12 AC 4.

    `None` where the caller named no requirements, and that is *not recorded*
    rather than *resolved against today* — the distinction `EnvironmentAnchor`
    keeps, because a rerun that silently resolved a different Django voids
    S-0.4's byte-identical guard counters.
    """
    if not plan.requirements:
        return None
    version = None if interpreter is None else interpreter.version
    return resolve(plan.requirements, anchor=anchor, python_version=version).recorded()


def _detected[T](facet: Detected[T] | None) -> JsonValue:
    """One fingerprint facet as JSON, or `None` where nothing established it.

    `None` is *nothing established this*, which `Fingerprint.undetermined` reports
    separately and deliberately — a facet defaulted to a plausible value is one
    nobody can tell from a facet that was actually read off a file.
    """
    if facet is None:
        return None
    return facet.value.value if isinstance(facet.value, Enum) else str(facet.value)


def _unsupported(identification: Identification) -> str:
    return (
        f"{getattr(identification, 'root', '?')} is not a repository this system can ground.\n"
        f"{identification.describe()}"
    )
