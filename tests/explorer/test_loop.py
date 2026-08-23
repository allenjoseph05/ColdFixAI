"""S-7.14 — the Explorer loop: propose a command, run it, measure, ask again.

`00-BRIEF.md` §5 step 5 calls grounding *the step the project's viability turns
on*, and until this story it was the one phase with no agent behind it. What these
tests prove is that a repository the harness reports as blocked is handed to the
model as a bounded question, that the command it names is run, and that the nine
predicates decide whether it worked — and what they cannot prove is that a real
model would name a useful command. That is what `00-BRIEF.md` §8's three-repo
experiment is for; the loop it would run is this one and not a second copy.

**The three bounds are attacked rather than described.** AC 4 names them, and two
of the three were reachable only after a defect at the Epic 5 join was fixed: a
model call per turn used to record a second grounding step, which halved the cap
and *cleared* the stall history every turn. Both tests here fail if that line
comes back.
"""

from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from anthropic.types import MessageParam

from coldfix.cost.accounting import Agent, ExchangeRate, Phase, StepClass
from coldfix.cost.budget import PHASE_CAPS
from coldfix.cost.routing import STEP_KINDS, Router, StepType, Tier
from coldfix.cost.session import Session
from coldfix.explorer import loop as loop_module
from coldfix.explorer import proposal as proposal_module
from coldfix.explorer import run as run_module
from coldfix.explorer.auth import Reply
from coldfix.explorer.compose import Grounded, NotGroundableError, Plan, ground_workload
from coldfix.explorer.fingerprint import Detected, Fingerprint, Framework, fingerprint
from coldfix.explorer.loop import (
    ESTABLISHED_BY_THE_SEQUENCE,
    REPAIRABLE,
    Effect,
    Exploration,
    LoopError,
    blocking,
    explore,
)
from coldfix.explorer.proposal import (
    EXPLORER_TEMPERATURE,
    HISTORY_WINDOW,
    MAX_OUTPUT_TOKENS,
    GiveUp,
    Move,
    ProposalError,
    Tried,
    parse,
    propose,
    render_question,
)
from coldfix.explorer.run import GROUNDING_STALL_AFTER, GroundingError
from coldfix.explorer.stages import Grounding, Outcome, Progress, Stage, Verdict, evaluate
from coldfix.llm.client import ModelResponse, Recording, ReplayingClient
from coldfix.sandbox.reset import ResetMechanism, ResetStrategy
from coldfix.sandbox.verification import VerificationReport, VerifiedReset

# ================================================================ the double

CHEAP = "claude-haiku-4-5"


def payload(text: str, *, model: str = CHEAP) -> dict[str, object]:
    """A response the vendor's own model parses, which is S-0.7b's whole rule."""
    return {
        "id": "msg_explorer",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}] if text else [],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": 800,
            "output_tokens": 60,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 5_000,
        },
    }


class PlannedExplorer:
    """A model that answers the Explorer's prompt from a plan, through the real replay.

    **It is a `ReplayingClient` whose recording is made at the moment of the
    call**, and the reason it is not one built up front is the shape of this
    loop: a replay is found by hashing the question, and the question carries the
    stage report the harness measured a moment earlier — nine verdicts whose
    detail strings hold row counts, a Django version and whatever a failed
    migration printed. Pre-computing them means running the predicates twice and
    asserting the two runs agree, which is a test of the fixture.

    So the digest is not what is asserted here; the *content* is, by the tests
    that read `asked`. Everything else is the real path: the reply goes through
    the same `Message` validation, the same `translate`, and the same `parse` a
    live answer would.
    """

    def __init__(self, replies: Sequence[str]) -> None:
        self._replies = list(replies)
        self.asked: list[str] = []
        self.models: list[str] = []

    def complete(  # noqa: PLR0913 - `ModelClient`'s shape, which is a request's identity
        self,
        *,
        model: str,
        system: str,
        messages: Sequence[MessageParam],
        max_tokens: int,
        temperature: float,
        cache_ttl: str = "5m",
    ) -> ModelResponse:
        self.asked.append(str(messages[-1]["content"]))
        self.models.append(model)
        reply = self._replies[min(len(self.asked) - 1, len(self._replies) - 1)]
        client = ReplayingClient(
            [
                Recording.of(
                    model=model,
                    system=system,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    response=payload(reply, model=model),
                )
            ]
        )
        return client.complete(
            model=model,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            cache_ttl=cache_ttl,
        )


def says(command: Sequence[str], why: str = "this should satisfy the predicate") -> str:
    return json.dumps({"command": list(command), "why": why})


def a_session(**overrides: object) -> Session:
    fields: dict[str, object] = {
        "system": proposal_module._SYSTEM,
        "playbook": "Django: migrations are applied with manage.py migrate.",
        "source": "an unfamiliar repository",
        "rate": ExchangeRate(euros_per_dollar=Decimal("0.92"), as_of=date(2026, 8, 23)),
        # `GroundingRun` refuses any other value, which is AC 4's first bound.
        "stall_after": GROUNDING_STALL_AFTER,
    }
    fields.update(overrides)
    return Session(**fields)  # type: ignore[arg-type]


# ================================================================ the subject


MANAGE_PY = """\
import os
import sys

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)
"""

SETTINGS = """\
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SECRET_KEY = "not-a-secret"
DEBUG = True
ALLOWED_HOSTS = ["*"]
ROOT_URLCONF = "config.urls"
USE_TZ = True

INSTALLED_APPS = ["django.contrib.contenttypes", "django.contrib.auth", "shop"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.path.join(BASE_DIR, "db.sqlite3"),
    }
}
"""

MODELS = """\
from django.db import models


class Author(models.Model):
    name = models.CharField(max_length=100)


class Book(models.Model):
    title = models.CharField(max_length=200)
    author = models.ForeignKey(Author, on_delete=models.CASCADE, related_name="books")
"""

# A planted N+1: one query for the books, one per book for its author.
URLS = """\
from django.http import JsonResponse
from django.urls import path

from shop.models import Book


def books(request):
    return JsonResponse(
        {"books": [{"title": b.title, "author": b.author.name} for b in Book.objects.all()]}
    )


urlpatterns = [path("books/", books)]
"""

FACTORIES = """\
import factory
from factory.django import DjangoModelFactory

from shop.models import Author, Book


class AuthorFactory(DjangoModelFactory):
    class Meta:
        model = Author

    name = factory.Sequence(lambda n: "author-%s" % n)


class BookFactory(DjangoModelFactory):
    class Meta:
        model = Book

    title = factory.Sequence(lambda n: "book-%s" % n)
    author = factory.SubFactory(AuthorFactory)
"""

PYPROJECT = """\
[project]
name = "shop"
version = "0"
dependencies = ["django>=5.0"]
"""


def write_project(root: Path) -> Path:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "shop").mkdir(parents=True, exist_ok=True)
    (root / "manage.py").write_text(MANAGE_PY, encoding="utf-8")
    (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (root / "config" / "__init__.py").write_text("", encoding="utf-8")
    (root / "config" / "settings.py").write_text(SETTINGS, encoding="utf-8")
    (root / "config" / "urls.py").write_text(URLS, encoding="utf-8")
    (root / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (root / "shop" / "models.py").write_text(MODELS, encoding="utf-8")
    (root / "shop" / "factories.py").write_text(FACTORIES, encoding="utf-8")
    _commit(root)
    return root


def _commit(root: Path) -> None:
    """A real checkout with one dated commit, because S-7.12 refuses a tarball.

    `anchor_for` reads the committer date and declines to default to today: a
    2019 repository handed a 2026 toolchain is the failure it exists to prevent.
    A tmp_path with files in it is not a checkout, and the sequence says so.
    """
    for command in (
        ["git", "init", "--quiet"],
        ["git", "config", "user.email", "test@example.invalid"],
        ["git", "config", "user.name", "Test"],
        ["git", "add", "-A"],
    ):
        subprocess.run(command, cwd=root, capture_output=True, timeout=300, check=True)
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "the repository as it stood"],
        cwd=root,
        capture_output=True,
        timeout=300,
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_DATE": "2024-05-06T10:00:00+00:00",
            "GIT_COMMITTER_DATE": "2024-05-06T10:00:00+00:00",
        },
    )


@pytest.fixture
def subject(tmp_path: Path) -> Path:
    """Unmigrated on purpose: `migrate` is the one stage a command has to move."""
    return write_project(tmp_path)


def hands_that_run(root: Path) -> Callable[[Move], Effect]:
    """The harness's half. **Supplied, because the loop runs nothing itself.**"""

    def run(move: Move) -> Effect:
        result = subprocess.run(
            list(move.command),
            cwd=root,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        return Effect(exit_code=result.returncode, output=result.stdout + result.stderr)

    return run


def hands_that_fail(_move: Move) -> Effect:
    """A command that runs and changes nothing, which is what a stall is made of."""
    return Effect(exit_code=1, output="could not do that")


def requester(root: Path) -> Callable[[str], Reply]:
    """Drive the subject the way the sandbox would, through its own test client."""

    def request(path: str) -> Reply:
        program = (
            "import json,os,sys;sys.path.insert(0,os.getcwd());"
            "import django;django.setup();"
            "from django.test import Client;"
            "r=Client().get(sys.argv[1]);"
            'print("<<<R>>>"+json.dumps({"status":r.status_code,"headers":dict(r.items())}))'
        )
        result = subprocess.run(
            [sys.executable, "-c", program, path],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
            env={**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings"},
        )
        line = next((row for row in result.stdout.splitlines() if row.startswith("<<<R>>>")), None)
        if line is None:
            pytest.fail(f"the subject did not answer for {path}:\n{result.stderr}")
        answer = json.loads(line.removeprefix("<<<R>>>"))
        return Reply(status=answer["status"], headers=answer["headers"])

    return request


class DoNothingReset(ResetMechanism):
    strategy = ResetStrategy.SNAPSHOT_RESTORE

    def prepare(self) -> None:
        """Nothing to capture."""

    def begin(self) -> None:
        """Nothing to open."""

    def reset(self) -> None:
        """Nothing to restore."""


def proof() -> VerifiedReset:
    return VerifiedReset(
        mechanism=DoNothingReset(),
        report=VerificationReport(strategy=ResetStrategy.SNAPSHOT_RESTORE, cycles=10),
    )


def sequence_for(root: Path) -> Callable[[], Grounded]:
    """S-7.13's `ground_workload`, bound to this repository."""

    def ground() -> Grounded:
        return ground_workload(
            root,
            python=[sys.executable],
            request=requester(root),
            plan=Plan(
                workload_id="shop.books",
                description="the book list endpoint",
                entity="shop.Book",
                factory_module="shop.factories",
                target="shop.Book",
                reset=ResetStrategy.SNAPSHOT_RESTORE,
                reset_between=[sys.executable, "manage.py", "flush", "--no-input"],
                repeats=1,
            ),
            reset=proof(),
        )

    return ground


def never_grounds() -> Grounded:
    """A sequence that must not be reached. Every test using it asserts a failure."""
    message = "the sequence ran on a repository whose stages never completed"
    raise AssertionError(message)


# ================================================ the partition the design rests on


def test_the_two_stage_sets_partition_the_nine() -> None:
    """**The whole shape, checked rather than described.** Six stages are the
    loop's to repair and three are established by the sequence it calls; a tenth
    stage dropped out of both would be one nobody ever works on and nobody ever
    settles, and the run would end at a predicate no question was ever asked
    about."""
    assert set(Stage) == REPAIRABLE | ESTABLISHED_BY_THE_SEQUENCE
    assert not REPAIRABLE & ESTABLISHED_BY_THE_SEQUENCE


def report(**verdicts: Verdict) -> Progress:
    return Progress(
        outcomes=tuple(
            Outcome(stage, verdicts.get(stage.name.lower(), Verdict.HOLDS), "measured")
            for stage in Stage
        )
    )


def test_the_agent_is_never_asked_to_seed_a_database_the_sweep_will_seed() -> None:
    """**The sharpest half of the partition.**

    `seed` sits ahead of `endpoint` in ADR 009's ordinary order, so a repository
    that is migrated and deliberately empty — which is exactly what the composed
    subject is — reports `seed` as its `first_incomplete`. A loop reading that
    number would spend its budget filling a database `verify_work` fills
    correctly a moment later, and would then measure a scale nobody asked for.
    """
    progress = report(seed=Verdict.FAILS, endpoint=Verdict.FAILS)

    blocked = blocking(progress)

    assert blocked is not None
    assert blocked.stage is Stage.ENDPOINT
    assert progress.first_incomplete is not None
    assert progress.first_incomplete.stage is Stage.SEED, "which is the number not to read"


def test_a_report_with_every_repairable_stage_holding_asks_nothing() -> None:
    """The exit condition: `None` means run the sequence, not *keep going*."""
    assert blocking(report(auth=Verdict.UNKNOWN, work=Verdict.UNKNOWN)) is None


# ================================================ the reply is a command


def test_a_command_is_read_as_argv() -> None:
    move = parse(says(["python", "manage.py", "migrate"], "applies them"), Stage.MIGRATE)

    assert isinstance(move, Move)
    assert move.command == ("python", "manage.py", "migrate")
    assert move.why == "applies them"


def test_a_command_given_as_one_string_is_refused_rather_than_split() -> None:
    """Whoever split it would be deciding what the quoting meant, and that is a
    decision with no owner."""
    with pytest.raises(ProposalError, match="argv is a list of strings"):
        parse(json.dumps({"command": "python manage.py migrate"}), Stage.MIGRATE)


def test_a_reply_that_is_not_json_is_reported_with_what_came_back() -> None:
    with pytest.raises(ProposalError, match="no action could be read"):
        parse("I would run migrate next.", Stage.MIGRATE)


def test_giving_up_is_an_answer_and_carries_its_reason() -> None:
    """`00-BRIEF.md` §9. A prompt whose only legal answer is another command
    cannot express the honest outcome."""
    answer = parse(json.dumps({"give_up": "no database driver for this platform"}), Stage.CONNECT)

    assert isinstance(answer, GiveUp)
    assert "driver" in answer.reason


def test_answering_both_ways_at_once_is_refused() -> None:
    """A caller picking one would be deciding what the agent meant."""
    with pytest.raises(ProposalError, match="opposite outcomes"):
        parse(json.dumps({"command": ["ls"], "give_up": "actually no"}), Stage.CLONE)


def test_answering_neither_way_is_refused() -> None:
    with pytest.raises(ProposalError, match="Stopping is an answer"):
        parse(json.dumps({"why": "I am thinking about it"}), Stage.CLONE)


def test_an_answer_about_another_stage_is_not_recorded_against_this_one() -> None:
    """Recording it against the stage that was asked would put a command that
    cannot help into that stage's history as a thing that was tried."""
    with pytest.raises(ProposalError, match="was about 'migrate'"):
        parse(json.dumps({"stage": "connect", "command": ["ls"]}), Stage.MIGRATE)


# ================================================ what the question carries


def a_tried(stage: Stage = Stage.MIGRATE, command: str = "migrate") -> Tried:
    return Tried(
        stage=stage,
        move=Move(command=("python", "manage.py", command), why="because"),
        exit_code=1,
        output="django.db.utils.OperationalError: no such table",
    )


def test_the_question_carries_the_predicate_the_measurement_and_what_was_tried() -> None:
    question = render_question(
        blocked=Outcome(Stage.MIGRATE, Verdict.FAILS, "3 migration(s) have not been applied"),
        progress=report(migrate=Verdict.FAILS),
        tried=[a_tried()],
        attempts_left=6,
        steps_left=54,
    )

    assert "migrate" in question
    assert Stage.MIGRATE.definition in question, "done means, not just not done"
    assert "3 migration(s) have not been applied" in question
    assert "no such table" in question, "the observation, not only the action"
    assert "exit 1" in question
    assert "6 attempt(s) left" in question
    assert "54 step(s) left" in question


def test_the_history_is_the_last_twenty_at_this_stage_and_nothing_from_others() -> None:
    """`03-agents.md` §2.1's sliding window, and the stage filter beside it: what
    was tried at `connect` is not why `migrate` is stuck."""
    tried = [a_tried(command=f"attempt-{index}") for index in range(HISTORY_WINDOW + 5)]
    tried.append(a_tried(stage=Stage.CONNECT, command="another-stage-entirely"))

    question = render_question(
        blocked=Outcome(Stage.MIGRATE, Verdict.FAILS, "not applied"),
        progress=report(migrate=Verdict.FAILS),
        tried=tried,
        attempts_left=1,
        steps_left=1,
    )

    assert "another-stage-entirely" not in question
    assert "attempt-4" not in question, "the twenty-first from the end has fallen out"
    assert "attempt-5" in question
    assert question.count("exit 1") == HISTORY_WINDOW


# ================================================ how the call is made


def test_the_explorer_action_is_mechanical_and_routed_to_the_cheap_tier() -> None:
    """`03-agents.md` §2.1 and `04-cost.md` §12.3: the steps are many and
    individually simple, and paying frontier rates to run `ls` is the waste the
    engineered case is about."""
    assert STEP_KINDS[StepType.EXPLORER_ACTION].step_class is StepClass.MECHANICAL
    assert Router().tier_for(StepClass.MECHANICAL, Phase.GROUND) is Tier.CHEAP


def test_the_call_is_made_at_the_temperature_the_design_calls_for() -> None:
    assert EXPLORER_TEMPERATURE == 0.3


def test_there_is_no_way_to_request_a_cascade_from_this_call_site() -> None:
    """Not for S-8.1's reason — this step *has* a mechanical check — but because
    the check is the exit code, which is not known when the reply arrives. A
    signature with nowhere to pass a validator cannot ask for one anyway."""
    parameters = inspect.signature(propose).parameters

    assert "validate" not in parameters
    assert not any("valid" in name for name in parameters)


def test_the_loop_runs_no_command_of_its_own() -> None:
    """**`Hands` is supplied, never taken.** `03-agents.md` §2.5 puts the
    denylist, the blocked egress and the workspace confinement on the container a
    command runs in, and a loop holding its own executor would be a second place
    all three have to exist. Asserted by inspection so it fails the moment
    somebody imports one to make a demo work."""
    source = Path(inspect.getfile(explore)).read_text(encoding="utf-8")

    assert "subprocess" not in source
    assert "bench.execute" not in source


def test_a_recorded_answer_replays_through_the_real_client() -> None:
    """The other double, driven once: the digest path, with the question rendered
    by the module that will send it."""
    blocked = Outcome(Stage.MIGRATE, Verdict.FAILS, "3 migration(s) have not been applied")
    progress = report(migrate=Verdict.FAILS)
    question = render_question(
        blocked=blocked, progress=progress, tried=(), attempts_left=8, steps_left=60
    )
    client = ReplayingClient(
        [
            Recording.of(
                model=CHEAP,
                system=proposal_module._SYSTEM,
                messages=[{"role": "user", "content": question}],
                max_tokens=MAX_OUTPUT_TOKENS,
                temperature=EXPLORER_TEMPERATURE,
                response=payload(says(["python", "manage.py", "migrate"])),
            )
        ]
    )

    outcome = propose(
        a_session(),
        client,
        blocked=blocked,
        progress=progress,
        tried=(),
        attempts_left=8,
        steps_left=60,
        measured_prefix_tokens=500,
        measured_prompt_tokens=800,
    )

    assert isinstance(outcome.value, Move)
    assert outcome.value.command == ("python", "manage.py", "migrate")
    assert outcome.step.agent is Agent.EXPLORER
    assert outcome.routed_model == CHEAP


# ================================================ the result type


def test_an_exploration_is_a_workload_or_a_failure_and_never_both() -> None:
    """A result carrying both would let a caller read the workload past the reason
    it should not; neither is a run that ended without saying how."""
    with pytest.raises(LoopError, match="never both or neither"):
        Exploration(steps=0, attempts=(), tried=())


# ================================================ the loop, against a repository


@pytest.mark.slow
def test_an_unmigrated_repository_becomes_an_emitted_workload(subject: Path) -> None:
    """**The story's sentence.** The harness says `migrate` is blocking, the model
    names two commands, the harness runs them and re-measures, and only then does
    S-7.13's sequence get to run.

    What this cannot show is that a model would name those two commands. The
    measurements are the harness's and the reply is replayed, which is
    `CLAUDE.md`'s rule — and `00-BRIEF.md` §8's three-repo experiment runs this
    same function against a real client rather than a second implementation.
    """
    client = PlannedExplorer(
        [
            says([sys.executable, "manage.py", "makemigrations", "shop"], "writes them"),
            says([sys.executable, "manage.py", "migrate"], "applies them"),
        ]
    )
    session = a_session()

    exploration = explore(
        session,
        client,
        root=subject,
        python=[sys.executable],
        ground=sequence_for(subject),
        hands=hands_that_run(subject),
        measured_prefix_tokens=500,
        measured_prompt_tokens=800,
    )

    assert exploration.failure is None, exploration.report()
    assert exploration.emitted is not None
    assert exploration.emitted.work_verified
    assert exploration.steps == 2, "two commands, two steps — **AC 3**"
    assert [entry.move.command[-1] for entry in exploration.tried] == ["shop", "migrate"]
    assert all(entry.stage is Stage.MIGRATE for entry in exploration.tried)


@pytest.mark.slow
def test_the_calls_are_billed_to_the_explorer_and_to_no_finding(subject: Path) -> None:
    """**AC 2.** `Agent.EXPLORER` carried `attributed=False` for five epics with a
    note saying either the loop was not built or its calls were billed to nobody.
    Grounding is shared across a repository (§11), so its spend is attributed to
    the run rather than to any one finding."""
    client = PlannedExplorer([says([sys.executable, "manage.py", "makemigrations", "shop"])])
    session = a_session()

    explore(
        session,
        client,
        root=subject,
        python=[sys.executable],
        ground=never_grounds,
        hands=hands_that_run(subject),
        measured_prefix_tokens=500,
        measured_prompt_tokens=800,
        stage_attempts=2,
    )

    billed = {call.agent for call in session.ledger.calls}
    assert billed == {Agent.EXPLORER}
    assert {call.phase for call in session.ledger.calls} == {Phase.GROUND}
    assert session.ledger.by_finding() == {}


@pytest.mark.slow
def test_the_model_may_stop_and_the_report_names_the_stage_that_never_completed(
    subject: Path,
) -> None:
    """S-7.11's acceptance: *reports failure rather than claiming success on empty
    data.* A refusal is a result, so it comes back rather than being raised."""
    client = PlannedExplorer([json.dumps({"give_up": "this project has no migrations to apply"})])

    exploration = explore(
        a_session(),
        client,
        root=subject,
        python=[sys.executable],
        ground=never_grounds,
        hands=hands_that_fail,
        measured_prefix_tokens=500,
        measured_prompt_tokens=800,
    )

    assert exploration.emitted is None
    assert exploration.failure is not None
    assert exploration.failure.stopped_at is not None
    assert exploration.failure.stopped_at.stage is Stage.MIGRATE
    assert "no migrations to apply" in exploration.failure.reason
    assert exploration.steps == 0, "it stopped before spending a step, and says so"


@pytest.mark.slow
def test_a_stage_that_never_moves_spends_its_own_budget_and_stops(subject: Path) -> None:
    """AC 4's third bound, through the loop. S-0.3's runs took five to nineteen
    minutes each, and detecting at stage five that this repository will not ground
    saves the four stages after it."""
    client = PlannedExplorer([says([sys.executable, "-c", "raise SystemExit(1)"])])

    exploration = explore(
        a_session(),
        client,
        root=subject,
        python=[sys.executable],
        ground=never_grounds,
        hands=hands_that_fail,
        measured_prefix_tokens=500,
        measured_prompt_tokens=800,
        stage_attempts=3,
    )

    assert exploration.failure is not None
    assert "whole budget" in exploration.failure.reason
    assert exploration.steps == 3


@pytest.mark.slow
def test_fifteen_turns_that_change_nothing_stall_the_run(subject: Path) -> None:
    """**AC 4's second bound, and it did not work until the Epic 5 join was fixed.**

    A model call carrying no conclusion *clears* the run of repeats, so while
    `Session.run` recorded a grounding step of its own, one call between two
    attempts reset the counter every turn and fifteen identical stage reports
    could never accumulate. Restoring that line makes this run to the per-stage
    budget instead, and the reason changes from *the same conclusion* to *its
    whole budget* — which is what fails here.

    The per-stage budget is raised above fifteen on purpose: with the default of
    eight it is the tighter instrument and the stall is never reached, which
    `test_run.py` already records as the relationship between the two bounds.
    """
    client = PlannedExplorer([says([sys.executable, "-c", "raise SystemExit(1)"])])

    exploration = explore(
        a_session(),
        client,
        root=subject,
        python=[sys.executable],
        ground=never_grounds,
        hands=hands_that_fail,
        measured_prefix_tokens=500,
        measured_prompt_tokens=800,
        stage_attempts=GROUNDING_STALL_AFTER + 5,
    )

    assert exploration.failure is not None
    assert "same conclusion" in exploration.failure.reason
    assert exploration.steps == GROUNDING_STALL_AFTER


@pytest.mark.slow
def test_one_turn_costs_one_of_the_sixty_steps(subject: Path) -> None:
    """**AC 4's first bound, and the other half of the same defect.** A model call
    that recorded a step of its own made a turn cost two of sixty, so the cap the
    project costed at 60 calls stopped a run at 30. With the cap tightened to
    three, three turns are taken and the fourth is refused."""
    client = PlannedExplorer([says([sys.executable, "-c", "raise SystemExit(1)"])])
    session = a_session()
    session.budget.tighten(Phase.GROUND, 3)

    exploration = explore(
        session,
        client,
        root=subject,
        python=[sys.executable],
        ground=never_grounds,
        hands=hands_that_fail,
        measured_prefix_tokens=500,
        measured_prompt_tokens=800,
        stage_attempts=GROUNDING_STALL_AFTER + 5,
    )

    assert exploration.failure is not None
    assert "out of budget" in exploration.failure.reason
    assert exploration.steps == 3
    assert PHASE_CAPS[Phase.GROUND].limit == 60, "and the compiled cap is still S-5.4's"


@pytest.mark.slow
def test_a_budget_with_another_phases_progress_check_is_refused(subject: Path) -> None:
    """AC 4 again, from the constructor. Not a correction but a refusal: silently
    substituting fifteen would hide that the caller asked for something else, and
    a run escalating after three unchanged reports abandons a repository
    mid-install."""
    with pytest.raises(GroundingError, match="stall_after=15"):
        explore(
            a_session(stall_after=3),
            PlannedExplorer([]),
            root=subject,
            python=[sys.executable],
            ground=never_grounds,
            hands=hands_that_fail,
            measured_prefix_tokens=500,
            measured_prompt_tokens=800,
        )


@pytest.mark.slow
def test_the_question_the_model_is_actually_asked_is_the_harness_report(subject: Path) -> None:
    """`08-audit.md` F6 one layer up. The fix there was that *does real work* is
    computed rather than claimed; the same rule applied to the other eight stages
    is what makes this question answerable at all. A question asking *how is it
    going* would be asking the agent to grade itself and then acting on the grade.
    """
    client = PlannedExplorer([json.dumps({"give_up": "enough"})])

    explore(
        a_session(),
        client,
        root=subject,
        python=[sys.executable],
        ground=never_grounds,
        hands=hands_that_fail,
        measured_prefix_tokens=500,
        measured_prompt_tokens=800,
    )

    asked = client.asked[0]
    measured = evaluate(fingerprint(subject), Grounding(root=subject, python=[sys.executable]))
    assert "BLOCKED AT\n  migrate" in asked
    assert measured.outcome(Stage.MIGRATE).detail in asked
    assert "nothing tried here yet" in asked


@pytest.mark.slow
def test_a_sequence_that_refuses_ends_the_run_rather_than_starting_a_repair(
    subject: Path,
) -> None:
    """Every repairable predicate holds, so what `ground_workload` is refusing is
    about the repository's *content* — and no command at `connect` or `migrate`
    produces a drivable route or a credential that works."""
    subprocess.run(
        [sys.executable, "manage.py", "makemigrations", "shop"],
        cwd=subject,
        capture_output=True,
        timeout=300,
        check=True,
    )
    subprocess.run(
        [sys.executable, "manage.py", "migrate"],
        cwd=subject,
        capture_output=True,
        timeout=300,
        check=True,
    )

    def refuses() -> Grounded:
        message = "/books/ needs a credential that could not be resolved"
        raise NotGroundableError(message)

    client = PlannedExplorer([])
    exploration = explore(
        a_session(),
        client,
        root=subject,
        python=[sys.executable],
        ground=refuses,
        hands=hands_that_fail,
        measured_prefix_tokens=500,
        measured_prompt_tokens=800,
    )

    assert client.asked == [], "no question was asked, because nothing was blocking"
    assert exploration.failure is not None
    assert "needs a credential" in exploration.failure.reason


# ================================================ one measurement per turn


def a_fingerprint(root: Path) -> Fingerprint:
    return Fingerprint(
        root=root,
        framework=Detected(value=Framework.DJANGO, evidence="pyproject.toml"),
        declared_version=None,
        orm=None,
        database=None,
        test_runner=None,
    )


def test_a_turn_measures_the_nine_predicates_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """**A stage report costs a `django.setup()`, a `manage.py check` and a route
    enumeration**, and a driver that asked which stage to work on next by
    re-measuring would pay for all three twice per turn with nothing having
    happened in between. `GroundingRun.measured` is what the attempt was judged
    against, so the loop routes on the reading the bounds were enforced against
    rather than on a second one that could disagree with it.

    `CLAUDE.md` asks that a reuse like this be noted rather than slipped in, so it
    is counted here: three turns cost four evaluations — one to open the run, and
    one per attempt — where re-measuring costs seven.
    """
    calls: list[int] = []
    blocked = report(migrate=Verdict.FAILS)

    def counted(*_args: object, **_kwargs: object) -> Progress:
        calls.append(1)
        return blocked

    monkeypatch.setattr(run_module, "evaluate", counted)
    monkeypatch.setattr(loop_module, "fingerprint", a_fingerprint)

    exploration = explore(
        a_session(),
        PlannedExplorer([says(["true"])]),
        root=Path("/nowhere"),
        python=["python"],
        ground=never_grounds,
        hands=hands_that_fail,
        measured_prefix_tokens=500,
        measured_prompt_tokens=800,
        stage_attempts=3,
    )

    assert exploration.steps == 3
    assert len(calls) == 4, "one to open the run, then one per attempt"


@pytest.mark.slow
def test_a_workload_whose_data_no_longer_exists_is_not_a_grounded_repository(
    subject: Path,
) -> None:
    """**`finish` is the only way a run succeeds, and this is what it adds.**

    `ground_workload` computes the stage report and hands it over; a caller can
    read past it. `GroundingRun.finish` cannot — it observes the verification,
    re-measures all nine, and refuses a run with any stage incomplete. So the
    sequence here does its real work and then flushes the database it seeded, and
    the run is refused even though a valid document was emitted a moment earlier.

    Returning `grounded.emitted` directly would pass this file's success test and
    fail here, which is the point: two ways to succeed is one more than S-7.10 AC
    5 allows.
    """
    real = sequence_for(subject)

    def then_empty_it() -> Grounded:
        grounded = real()
        subprocess.run(
            [sys.executable, "manage.py", "flush", "--no-input"],
            cwd=subject,
            capture_output=True,
            timeout=300,
            check=True,
            env={**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings"},
        )
        return grounded

    client = PlannedExplorer(
        [
            says([sys.executable, "manage.py", "makemigrations", "shop"]),
            says([sys.executable, "manage.py", "migrate"]),
        ]
    )

    exploration = explore(
        a_session(),
        client,
        root=subject,
        python=[sys.executable],
        ground=then_empty_it,
        hands=hands_that_run(subject),
        measured_prefix_tokens=500,
        measured_prompt_tokens=800,
    )

    assert exploration.emitted is None
    assert exploration.failure is not None
    assert "a stage still incomplete" in exploration.failure.reason
    assert exploration.failure.stopped_at is not None
    assert exploration.failure.stopped_at.stage is Stage.SEED


def test_a_reply_that_is_not_a_command_is_raised_rather_than_reported_as_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**A `Failure` means the repository will not ground.** A model that stopped
    answering in the agreed shape is a different problem, and reporting the second
    as the first would file a working repository under *not groundable* — so it
    travels, which is `CLAUDE.md`'s rule about not swallowing an exception to keep
    a run going."""
    monkeypatch.setattr(run_module, "evaluate", lambda *_a, **_k: report(migrate=Verdict.FAILS))
    monkeypatch.setattr(loop_module, "fingerprint", a_fingerprint)

    with pytest.raises(ProposalError, match="no action could be read"):
        explore(
            a_session(),
            PlannedExplorer(["I would run migrate next."]),
            root=Path("/nowhere"),
            python=["python"],
            ground=never_grounds,
            hands=hands_that_fail,
            measured_prefix_tokens=500,
            measured_prompt_tokens=800,
        )
