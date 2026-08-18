"""S-10.4 — writing the change, once the test that would catch a fake one exists.

Two properties carry the file, and both are about what the type will not let a
caller do rather than about what the code does in order.

**A patch cannot be generated without S-10.2's proof.** `falsified` has no
default, so a caller who skipped the must-fail gate has nothing to pass —
`03-agents.md` §5.3's mandatory ordering as a signature rather than as a list an
agent is asked to follow.

**Scope is the chain's.** A diff reaching outside the evidence is refused before
it is applied, and the model is never asked what it may touch. S-2.4's finding is
that a rule a model is *told* is a rule something can be argued out of.
"""

from __future__ import annotations

import inspect
import json
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from coldfix.bench.stats import Growth
from coldfix.cost.accounting import Agent, ExchangeRate, Phase
from coldfix.cost.routing import StepType
from coldfix.cost.session import Session
from coldfix.diagnosis.chain import (
    EvidenceChain,
    Implicated,
    LocalizationLink,
    Site,
    Symptom,
)
from coldfix.diagnosis.exclusions import Conditions, Exclusion
from coldfix.diagnosis.log import ExperimentLog, Verdict
from coldfix.llm.client import Recording, ReplayingClient
from coldfix.primitives.scaling import Distribution
from coldfix.repair import patch as patch_module
from coldfix.repair.falsification import Cheat, CostClaim, FalsificationTest, Guard
from coldfix.repair.mustfail import Falsified
from coldfix.repair.patch import (
    MAX_OUTPUT_TOKENS,
    SURGEON_TEMPERATURE,
    Applied,
    Patch,
    PatchError,
    apply,
    attempts_differ,
    check_scope,
    generate,
    parse,
    render_brief,
    scope_of,
    summarize,
)
from coldfix.sandbox.modes import CandidateSession, DiagnosticSession
from coldfix.sandbox.patching import ProtectedPathError, touched_paths

RATE = ExchangeRate(Decimal("0.92"), date(2026, 8, 18))
SOURCE = "shop/serializers.py::BookSerializer"
FINDING = "n.plus.one"

SITE = "shop/serializers.py"
IMPLICATED = "shop/models.py"
OUTSIDE = "shop/urls.py"

UNIFORM_AT_1000 = Conditions.of(
    fixture_shape=Distribution.UNIFORM.value,
    platform="x86_64-linux",
    concurrency=1,
    scales=[10, 100, 1000],
)


def a_chain() -> EvidenceChain:
    log = ExperimentLog()
    excluded = log.append(
        hypothesis="the database is the bottleneck",
        primitive="scaling.volume",
        rationale="queries have not been counted against volume yet",
        target="shop.books.list",
        design="scaling.volume(scales=[10, 100, 1000])",
        measurement={"db.query": 7.0},
        verdict=Verdict.REJECTED,
        outcome="queries flat at 7 across a 100x sweep",
    )
    confirmed = log.append(
        hypothesis="the serializer re-renders the author for every book",
        primitive="ablation.stub",
        rationale="the serializer is the only component not yet stubbed",
        target="BookSerializer.to_representation",
        design="ablation.stub(attribute='to_representation')",
        measurement={"seconds": 8.24, "seconds_ablated": 0.9, "rows": 1000.0},
        verdict=Verdict.CONFIRMED,
        outcome="stubbing the serializer removed 89% of wall time",
    )
    return EvidenceChain.assemble(
        symptom=Symptom(metric="seconds", magnitude=8.24, at_scale=1000),
        exclusions=[Exclusion(experiment=excluded, conditions=UNIFORM_AT_1000)],
        localization=[
            LocalizationLink(
                scope="BookSerializer.to_representation",
                experiment=confirmed,
                share_of_cost=0.89,
                basis="8.24s baseline against 0.90s ablated",
            )
        ],
        mechanism="the serializer re-renders the author for every book",
        complexity={"rows": Growth.LINEAR},
        site=Site(path=SITE, first_line=41, last_line=52),
        context=[Implicated(path=IMPLICATED, reason="declares the Author relation")],
    )


def a_falsified() -> Falsified:
    test = FalsificationTest(
        claim="the list endpoint stops re-rendering the author for every book",
        script="assert measure()['seconds'] < 2.0",
        equivalence="the same books in the same order",
        cost=CostClaim(
            metric="seconds",
            baseline=8.24,
            at_most=2.0,
            guards=(Guard(metric="rows", baseline=1000.0, at_most=1000.0),),
        ),
        catches=(Cheat.CACHED_STATE,),
    )
    return Falsified(test=test, evidence="AssertionError: 8.24 < 2.0", wall_seconds=9.1)


def a_diff(*paths: str) -> str:
    parts = []
    for path in paths or (SITE,):
        parts.extend(
            [
                f"--- a/{path}",
                f"+++ b/{path}",
                "@@ -41,1 +41,1 @@",
                "-        return AuthorSerializer(obj.author).data",
                "+        return self._authors[obj.author_id]",
            ]
        )
    return "\n".join([*parts, ""])


def a_patch(**overrides: Any) -> Patch:
    fields: dict[str, Any] = {
        "diff": a_diff(),
        "approach": "prefetch the authors once and index them",
        "rationale": "the serializer walked the relation per book; one query now serves all",
    }
    fields.update(overrides)
    return Patch(**fields)


def a_reply(**overrides: Any) -> str:
    payload: dict[str, Any] = {
        "diff": a_diff(),
        "approach": "prefetch the authors once and index them",
        "rationale": "the serializer walked the relation per book; one query now serves all",
    }
    payload.update(overrides)
    return json.dumps(payload)


def a_session() -> Session:
    return Session(
        system=patch_module._SYSTEM,
        playbook="Django: prefetch_related for a relation walked per row.",
        source=SOURCE,
        rate=RATE,
    )


def recorded(session: Session, question: str, reply: str, *, stop: str = "end_turn") -> Recording:
    model = session.router.route(StepType.PATCH, Phase.REPAIR)
    return Recording.of(
        model=model,
        system=patch_module._SYSTEM,
        messages=[{"role": "user", "content": question}],
        max_tokens=MAX_OUTPUT_TOKENS,
        temperature=SURGEON_TEMPERATURE,
        response={
            "id": "msg",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": reply}] if stop != "refusal" else [],
            "stop_reason": stop,
            "stop_sequence": None,
            "usage": {
                "input_tokens": 900,
                "output_tokens": 300,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 8000,
            },
        },
    )


class FakeCandidate(CandidateSession):
    """A candidate session without a container or a worktree."""

    def __init__(self, *, refuse: Exception | None = None) -> None:
        self._refuse = refuse
        self.applied: list[str] = []

    def apply_patch(self, diff: str) -> frozenset[str]:
        if self._refuse is not None:
            raise self._refuse
        self.applied.append(diff)
        return touched_paths(diff)


# ============ the gate chain: no patch without S-10.2's proof


def test_a_patch_cannot_be_generated_without_proof_the_test_failed() -> None:
    """**`03-agents.md` §5.3's ordering as a signature.** Only S-10.2's gate
    constructs a `Falsified`, and it refuses to represent a passing run — so a
    caller who skipped the gate has nothing to pass."""
    parameter = inspect.signature(generate).parameters["falsified"]

    assert parameter.default is inspect.Parameter.empty
    assert parameter.annotation == "Falsified"


def test_the_brief_shows_the_test_the_patch_must_satisfy() -> None:
    """A Surgeon that cannot see the test writes against its own idea of the
    goal, which is the weak-test problem S-10.3 exists for, one step later.

    **Every part of the block is asserted, not just the claim sentence.** A
    sabotage removed the heading alone and nothing failed — the claim and the
    footer were still there — so the threshold, the guards and the behaviour to
    preserve are checked too. Those are what the patch is actually written
    against; the claim is the one-liner.
    """
    brief = render_brief(a_chain(), a_falsified())

    assert "THE TEST YOUR CHANGE MUST MAKE PASS" in brief
    assert "stops re-rendering the author" in brief
    assert "must come in below 2" in brief
    assert "rows was 1000" in brief
    assert "the same books in the same order" in brief
    assert "cached_state" in brief
    assert "already failed on the unpatched code" in brief


# ==================== AC 1: scope is the chain's, not the agent's


def test_the_scope_is_the_site_plus_the_implicated_files() -> None:
    assert scope_of(a_chain()) == {SITE, IMPLICATED}


def test_a_patch_inside_the_evidence_is_accepted() -> None:
    """**The control.** A scope check that refused everything would satisfy every
    assertion below while making repair unreachable."""
    assert check_scope(a_patch(), a_chain()) is None
    assert check_scope(a_patch(diff=a_diff(IMPLICATED)), a_chain()) is None


def test_a_patch_reaching_outside_the_evidence_is_refused() -> None:
    """An agent that decided a fourth file also needs changing has decided
    something no experiment in this investigation supports."""
    with pytest.raises(PatchError, match="does not implicate"):
        parse(a_reply(diff=a_diff(SITE, OUTSIDE)), a_chain())


def test_the_refusal_names_both_what_was_touched_and_what_was_allowed() -> None:
    objection = check_scope(a_patch(diff=a_diff(OUTSIDE)), a_chain())

    assert objection is not None
    assert OUTSIDE in objection
    assert SITE in objection and IMPLICATED in objection


def test_a_diff_touching_nothing_is_refused() -> None:
    """A patch that changes nothing passes every gate downstream by having
    nothing to object to."""
    objection = check_scope(a_patch(diff="no hunks here"), a_chain())

    assert objection is not None
    assert "touches no file" in objection


def test_the_model_is_never_asked_what_it_may_touch() -> None:
    """S-2.4's rule: the rejection is server-side. The chain is shown as
    **evidence** — where the cost was measured, and why each file is implicated —
    which is a different thing from a permission list, and the check runs whether
    or not the model was told."""
    brief = render_brief(a_chain(), a_falsified())

    assert "IMPLICATED FILES" in brief
    assert "declares the Author relation" in brief
    assert "you may not touch" not in brief.lower()
    assert "forbidden" not in brief.lower()


def test_the_brief_states_the_scope_once_rather_than_twice() -> None:
    """**A sabotage found a duplicate block.** `EvidenceChain.render` already
    emits `SITE` and `IMPLICATED FILES` with reasons; a second scope block
    repeated them, cost tokens on every call, and was untestable — deleting its
    reasons changed no assertion, because the chain's copy still carried them.

    Two statements of one fact is the shape S-8.5 refused for `invalidated_if`
    and S-10.4 refused for `files`. Here it was in a prompt.
    """
    brief = render_brief(a_chain(), a_falsified())

    assert brief.count(IMPLICATED) == 1
    assert brief.count("declares the Author relation") == 1
    assert brief.count(SITE) == 1


# ============================= AC 2: multi-file patches


def test_a_patch_may_touch_every_implicated_file_at_once() -> None:
    multi = a_patch(diff=a_diff(SITE, IMPLICATED))

    assert multi.files == {SITE, IMPLICATED}
    assert check_scope(multi, a_chain()) is None


def test_one_file_outside_a_multi_file_patch_still_refuses_the_whole_patch() -> None:
    """A diff is applied or it is not. Accepting the in-scope hunks would leave
    the worktree in a state nobody wrote."""
    with pytest.raises(PatchError, match="does not implicate"):
        parse(a_reply(diff=a_diff(SITE, IMPLICATED, OUTSIDE)), a_chain())


# ================== files is derived, never reported


def test_the_patch_has_no_files_field_to_disagree_with_its_own_diff() -> None:
    """**A correction to `03-agents.md` §5.4**, the third of this shape in Epic 10
    after `failed_on_unpatched`. A reported list that disagreed with its diff
    would have the scope check passing against a claim rather than a change."""
    assert "files" not in Patch.model_fields

    with pytest.raises(ValidationError):
        Patch(  # type: ignore[call-arg]
            diff=a_diff(),
            approach="a",
            rationale="b",
            files=[SITE],
        )


def test_files_comes_from_the_diff_including_the_adversarial_case() -> None:
    """`touched_paths` handles the line a naive parser reads as a header: inside
    a hunk, a removed line beginning `-- a/x` renders as `--- a/x`."""
    sneaky = "\n".join(
        [
            f"--- a/{SITE}",
            f"+++ b/{SITE}",
            "@@ -1,2 +1,1 @@",
            "-- a/etc/passwd",
            "-old",
            "+new",
            "",
        ]
    )

    assert a_patch(diff=sneaky).files == {SITE}


@pytest.mark.parametrize("field", ["diff", "approach", "rationale"])
def test_a_blank_field_is_refused(field: str) -> None:
    """S-10.1 found this: `min_length=1` is satisfied by a space."""
    with pytest.raises(ValidationError, match="blank"):
        a_patch(**{field: "   "})


# ================ AC 3: candidate mode only


def test_applying_takes_a_candidate_session_and_nothing_else() -> None:
    """**The mirror of S-10.2.** That gate takes the diagnostic session because a
    patch must not be able to exist there; this takes the candidate one because a
    patch has to. `CandidateSession` is the only class with `apply_patch`."""
    annotation = inspect.signature(apply).parameters["session"].annotation

    assert annotation == "CandidateSession"
    assert hasattr(CandidateSession, "apply_patch")
    assert not hasattr(DiagnosticSession, "apply_patch")


def test_a_patch_in_scope_reaches_the_worktree() -> None:
    session = FakeCandidate()
    applied = apply(a_patch(), a_chain(), session)

    assert isinstance(applied, Applied)
    assert applied.written == {SITE}
    assert session.applied == [a_patch().diff]


def test_an_out_of_scope_patch_never_reaches_the_applier() -> None:
    """The scope check runs first because it is the narrower rule and the cheaper
    one — and because a rejected diff that had already been written would need
    reverting."""
    session = FakeCandidate()

    with pytest.raises(PatchError, match="does not implicate"):
        apply(a_patch(diff=a_diff(OUTSIDE)), a_chain(), session)
    assert session.applied == []


# ================ AC 4: the applier rejects protected paths


def test_the_protected_path_check_is_the_appliers_and_is_not_reimplemented() -> None:
    """S-2.4 put it inside `apply_patch` because that is *the only route by which
    a diff becomes a file*, and a second copy here would be a check something
    could be routed around.

    Asserted over what the module **imports**, not over its source text. The
    `Raises:` section of `apply` names `ProtectedPathError` — correctly, because
    that is what a caller has to catch — and a substring check cannot tell a
    docstring from an implementation. Recorded at S-7.11, again at S-9.3, again
    at S-10.1, and walked into a fourth time here.
    """
    imported = set(vars(patch_module))
    assert not imported & {
        "PatchPolicy",
        "DEFAULT_PATCH_POLICY",
        "audit",
        "ProtectedPathError",
        "apply_patch",
    }

    parameters = {
        name
        for _, function in inspect.getmembers(patch_module, inspect.isfunction)
        for name in inspect.signature(function).parameters
    }
    assert not parameters & {"policy", "protected", "worktree"}


def test_a_protected_file_is_refused_even_when_the_evidence_implicates_it() -> None:
    """**Defence in depth, and the case that needs it.** A chain whose context
    listed a test file would put that file *in scope* — and the applier still
    refuses it, because in-scope is a narrower rule sitting on top of a safety
    rule rather than replacing it."""
    chain = a_chain().model_copy(
        update={"context": (Implicated(path="tests/test_books.py", reason="asserts the count"),)}
    )
    patch = a_patch(diff=a_diff("tests/test_books.py"))
    session = FakeCandidate(refuse=ProtectedPathError("tests/test_books.py", "tests/**"))

    assert check_scope(patch, chain) is None
    with pytest.raises(ProtectedPathError):
        apply(patch, chain, session)


# ============================ generation on the wire


def test_a_patch_comes_back_from_a_replayed_call() -> None:
    session = a_session()
    chain = a_chain()
    question = f"{render_brief(chain, a_falsified())}\n\n{patch_module.QUESTION}"
    client = ReplayingClient([recorded(session, question, a_reply())])

    outcome = generate(
        session,
        client,
        chain=chain,
        falsified=a_falsified(),
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
        finding_id=FINDING,
    )

    assert outcome.value.files == {SITE}
    assert outcome.step.phase is Phase.REPAIR
    assert outcome.step.agent is Agent.SURGEON
    assert outcome.step.step_type is StepType.PATCH


def test_a_refusal_is_not_read_as_a_finding_that_needed_no_change() -> None:
    session = a_session()
    chain = a_chain()
    question = f"{render_brief(chain, a_falsified())}\n\n{patch_module.QUESTION}"

    with pytest.raises(PatchError, match="declined"):
        generate(
            session,
            ReplayingClient([recorded(session, question, "", stop="refusal")]),
            chain=chain,
            falsified=a_falsified(),
            measured_prefix_tokens=100,
            measured_prompt_tokens=900,
        )


def test_a_truncated_diff_is_refused() -> None:
    """git would either refuse a half-written diff or apply half a change, and
    neither is the patch that was written."""
    session = a_session()
    chain = a_chain()
    question = f"{render_brief(chain, a_falsified())}\n\n{patch_module.QUESTION}"

    with pytest.raises(PatchError, match="cut off"):
        generate(
            session,
            ReplayingClient([recorded(session, question, a_reply()[:60], stop="max_tokens")]),
            chain=chain,
            falsified=a_falsified(),
            measured_prefix_tokens=100,
            measured_prompt_tokens=900,
        )


def test_prior_attempts_are_shown_so_a_retry_can_differ() -> None:
    """S-10.5 owns the retry discipline; this makes the context available."""
    session = a_session()
    chain = a_chain()
    prior = [a_patch(approach="add an lru_cache on the author lookup")]
    brief = render_brief(chain, a_falsified())
    attempts = patch_module._render_prior(prior)
    question = f"{brief}\n\n{attempts}{patch_module.QUESTION}"

    assert "add an lru_cache" in question
    assert "must differ in approach, not only in wording" in question

    client = ReplayingClient([recorded(session, question, a_reply())])
    outcome = generate(
        session,
        client,
        chain=chain,
        falsified=a_falsified(),
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
        prior=prior,
    )
    assert outcome.value.approach == "prefetch the authors once and index them"


def test_the_first_attempt_runs_at_the_documented_temperature() -> None:
    """§5.1: 0.2 first, 0.6 on retries. A parameter because S-10.5 decides when
    to raise it, and a default because this story is the first attempt."""
    assert SURGEON_TEMPERATURE == 0.2
    assert inspect.signature(generate).parameters["temperature"].default == SURGEON_TEMPERATURE


# ======================= what S-10.5 will build on


def test_two_identical_diffs_do_not_count_as_differing_attempts() -> None:
    """F12: *the agent writes its own `approach` label and can rename the same
    idea.* Comparing diffs is the structural version — and this is deliberately
    the crudest one, because S-10.5 owns the real check and a similarity
    threshold invented here would be a number with no evidence behind it."""
    first = a_patch(approach="prefetch the authors")
    renamed = a_patch(approach="batch-load the author relation")

    assert not attempts_differ(renamed, [first])
    assert attempts_differ(a_patch(diff=a_diff(IMPLICATED)), [first])


def test_the_approach_string_is_context_rather_than_evidence() -> None:
    """The two patches above differ in every word of `approach` and not at all in
    what they do. A check reading the label would call them different."""
    first = a_patch(approach="prefetch the authors")
    renamed = a_patch(approach="batch-load the author relation")

    assert first.approach != renamed.approach
    assert first.diff == renamed.diff


def test_attempts_are_summarized_by_file() -> None:
    counts = summarize([a_patch(), a_patch(diff=a_diff(SITE, IMPLICATED))])

    assert counts == {SITE: 2, IMPLICATED: 1}


def test_a_patch_describes_what_it_touches_and_why() -> None:
    described = a_patch().describe()

    assert SITE in described
    assert "one query now serves all" in described
