"""Feeding both revisions the inputs nobody tests with, and diffing what comes back.

Epic 11, S-11.2. *Constructs adversarial inputs: empty collections, nulls,
duplicates, ties, unicode, boundary sizes, unordered results. Runs both revisions
and diffs outputs. On difference, returns a reproducing input.*

**Nothing here calls a model, and that is the story's first decision.**
`03-agents.md` §6.2 gives the Adversary a `craft_input(spec)` tool, which reads
like a generation problem. It is not one: the seven classes are enumerated by the
acceptance criterion and transcribed identically by `02-architecture.md` §209 and
`03-agents.md` §411, so what varies between subjects is *which* of them a workload
can be fed — not what they are. `CLAUDE.md` is explicit that a model call does not
go where a function would do, and a catalogue is a function. S-11.1 supplies the
isolated invocation for the parts of this epic that need reasoning; this part
needs a list and a comparison.

**The driver is the caller's and the inputs are this module's.** A `Probe` is
source text that knows how to hit one workload; only whoever grounded the
repository knows an endpoint's signature. Inventing one here would be this module
guessing at a program it has never seen, and guessing wrong produces
`NOT_COMPARED` on every input — an attack that ran and established nothing.

**A false *identical* is the worst thing this module can produce**, and it is the
same hazard `bench/diffing.py` opens with: every other instrument makes a number
somebody reads, and this one makes a verdict a patch ships on. Two constructions
answer it.

*Silence is never agreement.* A probe that produced no parseable output on either
revision is `NOT_COMPARED`, and `Equivalence.survived` is false unless at least
one input was really compared. The obvious implementation — collect payloads,
diff, report no differences — reports a patch as equivalent precisely when the
probe was broken enough to produce nothing, which is S-3.1's *no* against *not
known* at the last gate before a human sees the change.

*Nothing loosens the comparison.* `diff` makes order-insensitivity opt-in per
comparison because the decision belongs to whoever knows whether the query had an
`ORDER BY` — and the Adversary is precisely the party who does not. So this module
never opts in. The unordered comparison is run only to **label** a difference as
order-only, never to forgive one, and there is no parameter through which a caller
could ask for the looser answer.

**A difference that does not repeat is not an objection.** On any difference both
revisions are run again, and the original is checked against *itself* first: a
response carrying a timestamp or a fresh uuid differs from itself, and reporting
that as a broken patch sends the Surgeon to rewrite code that was right. Only a
difference that survives the repeat becomes a `ReproducingInput`, which carries
the exact program that produces it — an objection nobody can re-run is one nobody
can act on.

**The two sessions are opposite types**, as S-10.2's gate and S-10.6's `verify`
are. `DiagnosticSession` has no `apply_patch`, so *the revision before the change*
is a fact about the type rather than a claim about which worktree the caller
happened to hand over.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from coldfix.bench.diffing import Difference, JsonValue, diff
from coldfix.bench.execute import ExecutionResult, ExecutionTimeoutError
from coldfix.sandbox.modes import CandidateSession, DiagnosticSession

MARKER = "::coldfix-equivalence-output:: "
"""What the harness prints in front of the workload's output.

The subject prints too — SQL echoes, warnings, a framework's startup banner — so
the payload is delimited rather than assumed to be all of stdout. A marker line
that is absent means *no output was produced*, which is a failure and not an
empty result.
"""

OBSERVED_EXIT = 0
PROBE_ERROR_EXIT = 3
"""Three, and the same three S-10.2 gives a script that could not run, because it
is the same fact: the probe established nothing about the subject."""

DEFAULT_TIMEOUT_SECONDS = 120.0
"""Shorter than S-10.2's 300s, and deliberately its own constant. A falsification
test drives a workload at the scale the cost shows up at; an equivalence probe
drives it on an empty list. Sharing the number would tie two deadlines that have
no reason to move together."""

MAXIMUM_EVIDENCE_CHARS = 2_000
"""How much of a failed probe's output travels with the result, tail first — the
end of a traceback is the part that names what broke."""

DEFAULT_BOUNDARY_INTEGERS = (0, -1, 2**31 - 1, 2**63 - 1)
"""Where off-by-one and overflow live. The last two are the ones that bite in
practice: `2**31 - 1` is the top of a Postgres `integer` column, and `2**63 - 1`
is past the largest integer JavaScript can hold exactly, so a patch that starts
serialising an id as a number rather than a string breaks there and nowhere
else."""

COMPOSED = "caf" + chr(0x00E9)
"""The word `cafe` with an acute, in four code points: the accented letter is one
of them (NFC)."""

DECOMPOSED = "cafe" + chr(0x0301)
"""The same word in five: a plain `e` followed by the combining acute (NFD).

Spelled with `chr` rather than as a literal or an escape. As literals the two are
indistinguishable on screen and in a diff; as escapes an editor that normalises
source puts the literals back. This form is the only one that stays legible, and
the pair is worth nothing the moment its two entries become the same string.
"""

ASTRAL = chr(0x1F9EA)
"""A character outside the basic multilingual plane — one code point, two UTF-16
units, and the boundary a length or a slice computed in the wrong unit falls on."""

SHARP_S = chr(0x00DF)
"""A character whose upper case is *two* characters, so a patch that adds a case
fold changes the length of a string it was only supposed to normalise."""

RESIDUE = (
    "Equivalence here is equivalence of what the probe prints. A patch that also "
    "writes a row, sends a mail, warms a cache or leaves a file behind produces "
    "identical output and is not equivalent, and no comparison of two payloads can "
    "see that. `CLAUDE.md` states the hard case of this as a refusal: output "
    "equivalence cannot detect an introduced race, which is why concurrency fixes "
    "are diagnosed and never patched. Do not read a surviving attack as the patch "
    "being safe."
)


class EquivalenceError(Exception):
    """The equivalence attack could not be mounted."""


class Shape(StrEnum):
    """The seven classes AC 1 names. An enum, for two reasons.

    S-11.7's verdict has to say *which* attack broke the patch, and a class named
    in free text is one two reports spell differently. And a class that goes
    missing from the catalogue fails a test here rather than quietly shrinking the
    sweep — the same argument S-10.1 made for `catches`.
    """

    EMPTY = "empty collection"
    NULL = "null"
    DUPLICATES = "duplicates"
    TIES = "ties"
    UNICODE = "unicode"
    BOUNDARY = "boundary size"
    UNORDERED = "unordered results"


class Failure(StrEnum):
    """Why a run produced no payload. Five, and none of them is an empty result."""

    RAISED = "the probe raised before it produced output"
    NO_OUTPUT = "the probe exited cleanly without producing output"
    UNPARSABLE = "the probe's output was not JSON"
    TRUNCATED = "the probe's output was elided before it could be read"
    TIMED_OUT = "the probe did not finish inside its timeout"


class Outcome(StrEnum):
    """What one input established. Five, and only two of them are about the patch."""

    MATCHED = "both revisions produced the same output"
    DIFFERED = "the two revisions produced different output, and the difference repeated"
    PATCH_BROKE_THE_PROBE = (
        "the original produced output for this input and the patched revision did not"
    )
    NOT_COMPARED = (
        "the original revision produced no output, so there was nothing for the patched "
        "revision to be compared against"
    )
    NONDETERMINISTIC = (
        "the difference did not repeat, so it is the subject varying rather than the patch"
    )

    @property
    def objection(self) -> bool:
        """Whether this outcome is something the Surgeon has to answer."""
        return self in {Outcome.DIFFERED, Outcome.PATCH_BROKE_THE_PROBE}


@dataclass(frozen=True)
class AdversarialInput:
    """One input, and which class of nastiness it belongs to."""

    shape: Shape
    label: str
    payload: JsonValue

    def __post_init__(self) -> None:
        if not self.label.strip():
            message = "an adversarial input needs a label; a reproducing input nobody can name "
            raise EquivalenceError(message + "is one nobody can look up")


@dataclass(frozen=True)
class Probe:
    """How to drive one workload with one input. Supplied, never invented here.

    `script` runs in the subject's interpreter with the input bound to
    `coldfix_input`, and binds its answer to `output`. Everything else about the
    workload — the settings module, the client, the route — is the script's
    business, because it is the only party that knows them.

    **The script is never written into the subject's tree.** It travels on the
    command line, which is S-10.2's rule and for its reason: S-2.4 rejects a patch
    that touches a test, so a probe materialised as a file would be a protected
    path that every later diff shows.
    """

    workload: str
    script: str

    def __post_init__(self) -> None:
        if not self.script.strip():
            message = (
                "a probe with no script runs nothing. Both revisions would report the same "
                "absence of output and the attack would read as the patch surviving"
            )
            raise EquivalenceError(message)


@dataclass(frozen=True)
class Observed:
    """A run that produced a payload."""

    payload: JsonValue
    wall_seconds: float


@dataclass(frozen=True)
class Unobserved:
    """A run that produced no payload, and what it did instead.

    Not an empty payload. `bench/diffing.py` keeps `null` and *absent* apart for
    the same reason: a serializer that dropped a field and one that emptied it are
    different programs, and a comparison that conflates them cannot tell which.
    """

    reason: Failure
    evidence: str
    exit_code: int | None
    """`None` when the run was killed, so there was no exit code to read."""


@dataclass(frozen=True)
class Divergence:
    """Where two payloads disagree, and whether it is only their order."""

    differences: tuple[Difference, ...]
    order_only: bool
    """The same elements in a different sequence.

    Reported, not excused. Whether reordering is a behaviour change depends on
    whether the endpoint is paginated or sorted — a fact about the subject that
    this module does not have — so the classification is handed on and the
    judgement is left where it belongs.
    """

    def __post_init__(self) -> None:
        if not self.differences:
            message = (
                "a divergence with no differences is two payloads that matched, recorded as "
                "though they had not"
            )
            raise EquivalenceError(message)

    @property
    def first(self) -> Difference:
        return self.differences[0]

    def summary(self) -> str:
        head = "the same elements in a different order" if self.order_only else str(self.first)
        extra = len(self.differences) - 1
        return f"{head}{f' (and {extra} more)' if extra else ''}"


@dataclass(frozen=True)
class ReproducingInput:
    """AC 3. An input that makes the two revisions disagree, and the program to prove it.

    **Only ever constructed after the difference has been seen twice**, so a
    payload carrying a clock or a uuid cannot reach a reader as a broken patch.

    `program` is the whole point. `02-architecture.md` §222 sends a `broken`
    verdict *back to the Surgeon with a reproducing input*, and an input that
    arrives without the means to reproduce it is a claim the recipient has to take
    on trust — which is the thing this epic exists not to do.
    """

    input: AdversarialInput
    before: JsonValue
    """What the original revision produced. Always present: without it there is
    nothing for the patched revision to have diverged from."""

    after: Observed | Unobserved
    """What the patched revision did. `Unobserved` is the strongest form of the
    objection rather than a missing measurement — the original answered this input
    and the patched revision could not."""

    divergence: Divergence | None
    """`None` exactly when `after` produced no payload to disagree with."""

    program: str
    """The exact source that reproduces it, ready to run against either revision."""

    def __post_init__(self) -> None:
        if isinstance(self.after, Observed) and self.divergence is None:
            message = (
                "a reproducing input that reproduces nothing: both revisions produced a payload "
                "and no difference was recorded between them"
            )
            raise EquivalenceError(message)
        if isinstance(self.after, Unobserved) and self.divergence is not None:
            message = (
                "the patched revision produced no payload, so there is nothing a divergence "
                "could have been computed against"
            )
            raise EquivalenceError(message)

    @property
    def summary(self) -> str:
        if self.divergence is not None:
            return self.divergence.summary()
        if isinstance(self.after, Unobserved):
            return self.after.reason.value
        message = (
            "a reproducing input carrying neither a divergence nor a failed run; "
            "__post_init__ refuses both pairings that reach here"
        )
        raise EquivalenceError(message)

    def describe(self) -> str:
        lines = [
            f"REPRODUCING INPUT — {self.input.shape.value}: {self.input.label}",
            f"  input:  {_render(self.input.payload)}",
            f"  before: {_render(self.before)}",
        ]
        if isinstance(self.after, Observed):
            lines.append(f"  after:  {_render(self.after.payload)}")
        else:
            lines.append(f"  after:  no output — {self.after.reason.value}")
            if self.after.evidence.strip():
                lines.append(f"    {self.after.evidence.strip()}")
        lines.append(f"  what differs: {self.summary}")
        lines.append("  run this against either revision to see it again:")
        lines.extend(f"    {line}" for line in self.program.splitlines())
        return "\n".join(lines)


@dataclass(frozen=True)
class Probed:
    """One input, and what it established."""

    input: AdversarialInput
    outcome: Outcome
    note: str


@dataclass(frozen=True)
class Equivalence:
    """Everything the attack tried, and what came back.

    Holds every input rather than the objections alone, because *nine inputs tried
    and nothing found* and *nine inputs tried, seven of which the probe could not
    drive* are different answers and only one of them is a null result.
    """

    workload: str
    probed: tuple[Probed, ...]
    reproducing: tuple[ReproducingInput, ...]
    runs: int
    """Container runs made. Two per input, plus two more wherever a difference had
    to be confirmed."""

    def __post_init__(self) -> None:
        if not self.probed:
            message = (
                "an equivalence attack over no inputs found no difference, which reads as the "
                "patch surviving an attack that was never mounted"
            )
            raise EquivalenceError(message)
        objections = sum(1 for item in self.probed if item.outcome.objection)
        if objections != len(self.reproducing):
            message = (
                f"{objections} inputs produced an objection but {len(self.reproducing)} "
                "reproducing inputs were kept. S-11.7 requires one for every `broken`, and a "
                "difference recorded without the means to reproduce it cannot be sent back"
            )
            raise EquivalenceError(message)

    @property
    def compared(self) -> tuple[Probed, ...]:
        """Inputs where the original answered, so the patched revision had a case to answer."""
        return tuple(
            item
            for item in self.probed
            if item.outcome in {Outcome.MATCHED, Outcome.DIFFERED, Outcome.PATCH_BROKE_THE_PROBE}
        )

    @property
    def inconclusive(self) -> tuple[Probed, ...]:
        """Inputs the probe could not drive. Not evidence about the patch."""
        return tuple(item for item in self.probed if item.outcome is Outcome.NOT_COMPARED)

    @property
    def unstable(self) -> tuple[Probed, ...]:
        """Inputs where the subject disagreed with itself."""
        return tuple(item for item in self.probed if item.outcome is Outcome.NONDETERMINISTIC)

    @property
    def complete(self) -> bool:
        """Whether every input in the sweep was actually driven."""
        return not self.inconclusive

    @property
    def survived(self) -> bool:
        """Whether this attack failed to break the patch. **Three conditions, not one.**

        The first is the one an obvious implementation omits: something must have
        been compared. Without it a probe broken enough to produce nothing on
        every input reports the patch as equivalent, which is the false
        *identical* `bench/diffing.py` names as the worst outcome available here.

        The third is a judgement and errs toward more scrutiny. An input where the
        subject disagreed with itself says the workload is not deterministic under
        this probe, and under that condition the inputs that *matched* matched
        once — which is weaker than it reads. A patch audit that resolves doubt in
        favour of shipping is the wrong way round.
        """
        return bool(self.compared) and not self.reproducing and not self.unstable

    def describe(self) -> str:
        lines = [
            f"EQUIVALENCE ATTACK on {self.workload} — {len(self.probed)} inputs, {self.runs} runs.",
            f"  compared: {len(self.compared)}   objections: {len(self.reproducing)}   "
            f"not compared: {len(self.inconclusive)}   unstable: {len(self.unstable)}",
        ]
        if not self.compared:
            lines.append(
                "  **Nothing was compared.** The original revision produced no readable output "
                "for any input, so this attack says nothing about the patch. It is not a clean "
                "bill: fix the probe and run it again."
            )
        elif not self.complete:
            shapes = sorted({item.input.shape.value for item in self.inconclusive})
            lines.append(
                f"  **The sweep is partial.** {len(self.inconclusive)} inputs were never driven "
                f"({', '.join(shapes)}), so those classes were not attacked rather than attacked "
                "and survived."
            )
        if self.unstable:
            lines.append(
                f"  **The subject is not deterministic under this probe.** {len(self.unstable)} "
                "inputs gave two different answers on the same revision, so the inputs that "
                "matched matched once."
            )

        for item in self.reproducing:
            lines.extend(item.describe().splitlines())

        if self.survived:
            lines.append(
                f"  No difference found across {len(self.compared)} inputs. That is a null "
                "result and it ships as one."
            )
        lines.append(f"  {RESIDUE}")
        return "\n".join(lines)


def catalogue(*, page_size: int | None = None) -> tuple[AdversarialInput, ...]:
    """AC 1: the seven classes, as data.

    `page_size` is optional and has **no default**, which is deliberate. Probing
    one under, exactly, and one over a page boundary is the highest-yield boundary
    there is on a list endpoint — and it needs a number nobody in this module
    knows. Guessing a common one would let a report claim the page boundary was
    attacked when some other number was, so an unsupplied page size means those
    three inputs are absent and `Equivalence.describe` counts what was covered.

    Raises:
        EquivalenceError: `page_size` is below one.
    """
    if page_size is not None and page_size < 1:
        message = f"a page holds at least one row; got {page_size}"
        raise EquivalenceError(message)

    inputs = list(_STANDARD)
    if page_size is not None:
        inputs.extend(
            AdversarialInput(
                shape=Shape.BOUNDARY,
                label=f"{count} rows against a page of {page_size}",
                payload=[{"id": index} for index in range(1, count + 1)],
            )
            for count in (page_size - 1, page_size, page_size + 1)
        )
    return tuple(inputs)


def harness(script: str, payload: JsonValue) -> str:
    """The program that drives `script` with `payload` and prints what came back.

    **Everything on the wire is ASCII, and the unicode class is why.** The
    payload is embedded `ensure_ascii=True` and the output is encoded the same
    way, so a container whose stdout is not UTF-8 cannot mangle a character in
    transit. Without it the unicode attack is the one input class whose result
    cannot be trusted — a mangled character reads as a difference the patch did
    not cause, or, mangled identically on both sides, as an agreement that was
    never tested.

    **`compile` is inside the guarded block**, which S-10.2 learned by being
    wrong about it once: outside, a probe with a syntax error dies before the
    `try` and the interpreter picks its own exit code, so the traceback naming
    the real problem never reaches the caller as evidence.

    The script binds its answer to `output`. A script that binds nothing is a
    failed run and says so — returning `null` instead would make *the probe is
    broken* and *the workload returned null* the same observation, and the second
    is a legitimate result the null attack class exists to produce.
    """
    encoded = json.dumps(payload, ensure_ascii=True)
    missing = "the probe did not bind `output`, so there is nothing to compare"
    return (
        "import json, sys, traceback\n"
        f"_input = json.loads({encoded!r})\n"
        f"_script = {script!r}\n"
        "_namespace = {'__name__': '__main__', 'coldfix_input': _input}\n"
        "try:\n"
        "    _code = compile(_script, 'equivalence_probe', 'exec')\n"
        "    exec(_code, _namespace)\n"
        "except BaseException:\n"
        "    traceback.print_exc()\n"
        f"    sys.exit({PROBE_ERROR_EXIT})\n"
        "if 'output' not in _namespace:\n"
        f"    print({missing!r}, file=sys.stderr)\n"
        f"    sys.exit({PROBE_ERROR_EXIT})\n"
        "try:\n"
        "    _encoded = json.dumps(_namespace['output'], ensure_ascii=True)\n"
        "except (TypeError, ValueError):\n"
        "    traceback.print_exc()\n"
        f"    sys.exit({PROBE_ERROR_EXIT})\n"
        f"print({MARKER!r} + _encoded)\n"
        f"sys.exit({OBSERVED_EXIT})\n"
    )


def read(result: ExecutionResult) -> Observed | Unobserved:
    """Turn one run into a payload or into the reason there isn't one. Pure.

    Separated from the run for S-10.2's reason: the branching is the part worth
    attacking, and a function that also needs a container is one a sabotage pass
    cannot reach cheaply.

    **Truncation gets its own reason.** A stream long enough to lose its middle
    can leave the marker line cut in half, and reporting that as *not JSON* sends
    a reader after a bug in the probe when the fault is that the subject printed
    eight megabytes.
    """
    evidence = _evidence(result)
    if result.exit_code != OBSERVED_EXIT:
        return Unobserved(reason=Failure.RAISED, evidence=evidence, exit_code=result.exit_code)

    line = _marker_line(result.stdout)
    if line is None:
        reason = Failure.TRUNCATED if result.truncated else Failure.NO_OUTPUT
        return Unobserved(reason=reason, evidence=evidence, exit_code=result.exit_code)

    try:
        payload: JsonValue = json.loads(line)
    except json.JSONDecodeError as broken:
        reason = Failure.TRUNCATED if result.truncated else Failure.UNPARSABLE
        return Unobserved(reason=reason, evidence=str(broken), exit_code=result.exit_code)
    return Observed(payload=payload, wall_seconds=result.wall_seconds)


def run_on(
    session: DiagnosticSession | CandidateSession,
    probe: Probe,
    adversarial: AdversarialInput,
    *,
    interpreter: str = "python",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Observed | Unobserved:
    """Drive one revision with one input.

    A timeout is a failure to observe, never an empty answer. A killed run
    establishes nothing in either direction, and reading it as *no output, same as
    the other side* is how two dead probes agree with each other.
    """
    program = harness(probe.script, adversarial.payload)
    try:
        result = session.run([interpreter, "-c", program], timeout=timeout)
    except ExecutionTimeoutError as timed_out:
        return Unobserved(reason=Failure.TIMED_OUT, evidence=str(timed_out), exit_code=None)
    return read(result)


def compare_outputs(before: JsonValue, after: JsonValue) -> Divergence | None:
    """AC 2's second half. Strict, and there is no way to ask for anything else.

    The ordered comparison decides. The unordered one runs only to say whether the
    difference is *only* order — `bench/diffing.py` makes order-insensitivity
    opt-in because whoever knows whether the query had an `ORDER BY` should
    choose, and the Adversary is by construction the party who does not know. A
    parameter here would be the one knob that turns a real difference into a
    clean bill.
    """
    strict = diff(before, after)
    if strict.identical:
        return None
    unordered = diff(before, after, ignore_order=True)
    return Divergence(differences=strict.differences, order_only=unordered.identical)


def attack(  # noqa: PLR0913 - the probe, the two revisions, the inputs and the
    # two execution settings are six independent facts. Bundling the last three
    # would invent a type whose only purpose is to be unpacked one line later.
    probe: Probe,
    *,
    original: DiagnosticSession,
    patched: CandidateSession,
    inputs: Sequence[AdversarialInput] | None = None,
    interpreter: str = "python",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Equivalence:
    """Run every adversarial input against both revisions. AC 1 to AC 3.

    **The session types carry the meaning of *before*.** `DiagnosticSession` has
    no `apply_patch` and no `diff` (S-2.3), so the revision this compares against
    cannot contain the change — the same construction S-10.2 used for its gate,
    inverted by S-10.6's `verify`, and used here for the third time.

    **The two worktrees must sit on the same commit**, and that is checked rather
    than assumed. A difference measured between two different base revisions is
    somebody else's change reported as this patch's, and it looks exactly like a
    broken patch.

    This is expensive in runs, not in tokens: two container runs per input, and
    two more wherever a difference has to be confirmed. `Equivalence.runs` reports
    what it cost.

    Raises:
        EquivalenceError: the two sessions are on different commits, or `inputs`
            is empty.
        SessionClosedError: a worktree is gone, so there is nothing to run in.
    """
    if original.worktree.revision != patched.worktree.revision:
        message = (
            f"the two revisions are checked out at different commits "
            f"({original.worktree.revision} and {patched.worktree.revision}). Every difference "
            "between them would include whatever changed in between, reported as this patch's"
        )
        raise EquivalenceError(message)

    chosen = tuple(catalogue() if inputs is None else inputs)
    if not chosen:
        message = (
            "an equivalence attack with no inputs finds no difference. That is the shape of a "
            "patch surviving, produced without attacking it"
        )
        raise EquivalenceError(message)

    runner = _Runner(probe=probe, interpreter=interpreter, timeout=timeout)
    probed: list[Probed] = []
    reproducing: list[ReproducingInput] = []
    runs = 0

    for adversarial in chosen:
        record, found, spent = _attack_one(runner, adversarial, original, patched)
        probed.append(record)
        if found is not None:
            reproducing.append(found)
        runs += spent

    return Equivalence(
        workload=probe.workload,
        probed=tuple(probed),
        reproducing=tuple(reproducing),
        runs=runs,
    )


@dataclass(frozen=True)
class _Runner:
    """The probe and the two execution settings, so the helpers take four arguments."""

    probe: Probe
    interpreter: str
    timeout: float

    def on(
        self,
        session: DiagnosticSession | CandidateSession,
        adversarial: AdversarialInput,
    ) -> Observed | Unobserved:
        return run_on(
            session,
            self.probe,
            adversarial,
            interpreter=self.interpreter,
            timeout=self.timeout,
        )


def _attack_one(
    runner: _Runner,
    adversarial: AdversarialInput,
    original: DiagnosticSession,
    patched: CandidateSession,
) -> tuple[Probed, ReproducingInput | None, int]:
    """One input against both revisions, confirmed if it found anything."""
    before = runner.on(original, adversarial)
    after = runner.on(patched, adversarial)
    runs = 2

    if isinstance(before, Unobserved):
        return Probed(adversarial, Outcome.NOT_COMPARED, before.reason.value), None, runs

    divergence: Divergence | None = None
    if isinstance(after, Observed):
        divergence = compare_outputs(before.payload, after.payload)
        if divergence is None:
            return Probed(adversarial, Outcome.MATCHED, Outcome.MATCHED.value), None, runs

    # Something to report, so it gets run again before anybody acts on it. The
    # control is the original against *itself*: a payload carrying a clock or a
    # fresh uuid differs from its own second run, and reported without this it is
    # a broken patch that no amount of rewriting will fix.
    again_before = runner.on(original, adversarial)
    again_after = runner.on(patched, adversarial)
    runs += 2

    unstable = _unstable(before, again_before, after, again_after)
    if unstable is not None:
        return Probed(adversarial, Outcome.NONDETERMINISTIC, unstable), None, runs

    found = ReproducingInput(
        input=adversarial,
        before=before.payload,
        after=after,
        divergence=divergence,
        program=harness(runner.probe.script, adversarial.payload),
    )
    outcome = Outcome.DIFFERED if divergence is not None else Outcome.PATCH_BROKE_THE_PROBE
    return Probed(adversarial, outcome, found.summary), found, runs


def _unstable(
    before: Observed,
    again_before: Observed | Unobserved,
    after: Observed | Unobserved,
    again_after: Observed | Unobserved,
) -> str | None:
    """Whether the difference is the patch's or the subject's. `None` means the patch's.

    The original is checked first and against itself, because if it disagrees with
    its own second run then nothing measured on this input is attributable to
    anything — and a check that only re-ran the *pair* would confirm a difference
    that a uuid manufactures afresh every time.
    """
    return _original_varied(before, again_before) or _patched_varied(after, again_after)


def _original_varied(before: Observed, again: Observed | Unobserved) -> str | None:
    """The control. A revision that disagrees with itself measures nothing."""
    if isinstance(again, Unobserved):
        return (
            "the original revision produced output on the first run and "
            f"{again.reason.value} on the second"
        )
    if compare_outputs(before.payload, again.payload) is not None:
        return (
            "the original revision produced two different outputs for this input, so no "
            "difference measured against it belongs to the patch"
        )
    return None


def _patched_varied(after: Observed | Unobserved, again: Observed | Unobserved) -> str | None:
    """Whether the patched revision gave the same answer twice, whatever that answer was."""
    if isinstance(after, Observed) and isinstance(again, Observed):
        if compare_outputs(after.payload, again.payload) is not None:
            return "the patched revision produced two different outputs for this input"
        return None
    if isinstance(after, Unobserved) and isinstance(again, Unobserved):
        if after.reason is not again.reason:
            return (
                f"the patched revision failed two different ways — {after.reason.value}, "
                f"then {again.reason.value}"
            )
        return None
    return "the patched revision produced output on one run and not on the other"


def _marker_line(stdout: str) -> str | None:
    """The payload the harness printed, or `None` if it never got that far.

    The last marker line wins. The harness prints it and exits, so anything after
    it came from a thread the subject left running, and the one this function is
    looking for is the last thing written by the main line of execution.
    """
    for line in reversed(stdout.splitlines()):
        if line.startswith(MARKER):
            return line[len(MARKER) :]
    return None


def _evidence(result: ExecutionResult) -> str:
    joined = "\n".join(
        part for part in (result.stdout.strip(), result.stderr.strip()) if part
    ).strip()
    if len(joined) <= MAXIMUM_EVIDENCE_CHARS:
        return joined
    return "[…] " + joined[-MAXIMUM_EVIDENCE_CHARS:]


def _render(value: JsonValue, limit: int = 200) -> str:
    text = json.dumps(value, ensure_ascii=True)
    return text if len(text) <= limit else text[:limit] + "…"


def _rows(*names: str | None) -> list[JsonValue]:
    return [{"id": index, "name": name} for index, name in enumerate(names, start=1)]


_STANDARD: tuple[AdversarialInput, ...] = (
    AdversarialInput(Shape.EMPTY, "an empty list", []),
    AdversarialInput(Shape.EMPTY, "an empty object", {}),
    AdversarialInput(Shape.EMPTY, "an empty string", ""),
    # A patch that adds a `.get()` with a default, or that switches an iteration
    # for a lookup, changes `null` into something else without changing anything
    # a populated fixture would show.
    AdversarialInput(Shape.NULL, "null in place of the whole input", None),
    AdversarialInput(Shape.NULL, "a null field inside a record", _rows("a", None)),
    # A prefetch or a join changes the multiplicity of a result set. Repeated
    # rows are where that shows and a distinct set of rows is where it hides.
    AdversarialInput(Shape.DUPLICATES, "the same record three times", _rows("a", "a", "a")),
    AdversarialInput(
        Shape.DUPLICATES,
        "two records sharing a name under different ids",
        _rows("a", "b", "a"),
    ),
    # Sorting on a key that repeats is not a total order, so a patch that moves
    # the sort into the database — or swaps a stable sort for an unstable one —
    # reorders the tied rows and nothing else.
    AdversarialInput(
        Shape.TIES,
        "every sort key equal",
        [{"id": 3, "rank": 1}, {"id": 1, "rank": 1}, {"id": 2, "rank": 1}],
    ),
    # **Built from code points, and that is not a style choice.** The first pair is
    # two spellings of one word that render identically in every editor and in
    # every diff — which is exactly why a normalisation change survives review —
    # so written as characters a reader could not see that the two entries differ
    # at all, and a later edit could collapse them into one with nothing looking
    # wrong. An escape is not enough either: an editor that normalises source puts
    # the invisible form straight back.
    AdversarialInput(
        Shape.UNICODE,
        "the same name composed and decomposed (NFC against NFD)",
        _rows(COMPOSED, DECOMPOSED),
    ),
    AdversarialInput(
        Shape.UNICODE,
        "a character outside the basic multilingual plane",
        _rows(ASTRAL),
    ),
    AdversarialInput(
        Shape.UNICODE,
        "a character whose upper case is two characters",
        _rows(SHARP_S),
    ),
    AdversarialInput(Shape.BOUNDARY, "one row", _rows("a")),
    AdversarialInput(Shape.BOUNDARY, "two rows", _rows("a", "b")),
    AdversarialInput(
        Shape.BOUNDARY,
        "integers at the edges of the columns that hold them",
        list(DEFAULT_BOUNDARY_INTEGERS),
    ),
    # A pair rather than one input. A patch whose output depends on the order it
    # received its input — a dict keyed by id, a set — matches the original on
    # the sorted one and differs on the permutation.
    AdversarialInput(
        Shape.UNORDERED,
        "records in ascending id order",
        [{"id": 1}, {"id": 2}, {"id": 3}],
    ),
    AdversarialInput(
        Shape.UNORDERED,
        "the same records permuted",
        [{"id": 3}, {"id": 1}, {"id": 2}],
    ),
)
