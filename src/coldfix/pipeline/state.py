"""What the six nodes hand between them.

E24. The channels are v3's, and the machinery under them is the machinery the
project already has: `AppendOnly` refuses a rewrite of history, and `node`
validates every write on every transition.

**Two channels are append-only and it is not tidiness.** `measurements` and
`candidates` grow through a run and are read back as a prompt prefix; rewriting
either changes bytes the model provider has already cached, and a broken cache
multiplies the cost of a run several times over. A reducer that raises is
cheaper than a convention nobody can enforce.

**Two are keyed mappings rather than sequences.** `findings` and `resolved` are
updated per entry -- one finding is audited, one opportunity is marked shipped --
and a flat list of opaque entries cannot be addressed one at a time. `screening`
in v1 learned this the hard way when re-screening only what a patch touched
turned out to have a correct answer and nowhere to put it.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, JsonValue

from coldfix.state.checkpoint import AppendOnly


class PipelineState(BaseModel):
    """One run, as it is handed from node to node and written to a checkpoint."""

    project: dict[str, JsonValue] = Field(default_factory=dict)
    """Fingerprint, image, and the tier the environment reached."""

    runnable: JsonValue | None = None
    """How to start the subject, the driver, and the digest of both."""

    measurements: Annotated[list[JsonValue], AppendOnly("measurements")] = Field(
        default_factory=list
    )
    """Every number the harness took, by id. **Append-only.**"""

    findings: dict[str, JsonValue] = Field(default_factory=dict)
    """Attested findings, keyed so one can be audited without rewriting the rest."""

    resolved: dict[str, JsonValue] = Field(default_factory=dict)
    """What became of each: proven, disproven, unfixable, shipped."""

    candidates: Annotated[list[JsonValue], AppendOnly("candidates")] = Field(default_factory=list)
    """Every patch attempted and its measured score, winners and losers.
    **Append-only**, so a later round cannot re-propose something already beaten."""

    coverage: dict[str, JsonValue] = Field(default_factory=dict)
    """What was driven, what was not, and what could not be measured."""

    repaired: JsonValue | None = None
    audited: JsonValue | None = None

    verdict: str | None = None
    route: str | None = None
    """Written by a node, read by a router. Never written by a router."""

    budget: dict[str, JsonValue] = Field(default_factory=dict)
    """Checked *before* each priced call, so a halt writes a checkpoint rather
    than dying part-way through one."""
