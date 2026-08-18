"""Hand-written workload descriptors, with only numbers somebody measured.

S-4.1 AC 3 asks for a hand-written workload for the target repository that
validates against the model. The numbers below come from `targets.toml` and
ADR 011, which record what S-0.3, S-0.4 and S-0.5 measured on `django-helpdesk`
at commit `3a22901`.

**One scale point, because one is what was measured.** ADR 011 gives
`GET /api/tickets/` at T=100 tickets and nothing at a second volume; the
`queries ≈ 1 + T + F + T` line beside it is a model, not an observation.
Inventing a second point from that formula would put a computed number where the
artifact promises a measured one, so this descriptor has one observation and
honestly reports that its work is not verified — which is the fail-closed
behaviour `08-audit.md` F6 asks for, seen on the project's own target.
"""

from __future__ import annotations

from coldfix.primitives.counters import DB_QUERY
from coldfix.primitives.measurement import SECONDS
from coldfix.primitives.scaling import Distribution
from coldfix.sandbox.reset import ResetStrategy
from coldfix.screening.workload import (
    RESPONSE_BYTES,
    FixtureRecipe,
    Observation,
    Workload,
)

# ADR 011, measured on the scaled dataset: 503 tickets / 3004 followups / 3002
# attachments, produced deterministically by `seeds/scale_helpdesk.py`. The
# shipped fixture is 3 tickets, which is why the caveat exists at all.
HELPDESK_TICKETS = Workload(
    id="api.tickets.list",
    description=(
        "django-helpdesk's ticket list endpoint, which carries the unplanted nested N+1 "
        "ADR 011 pinned this repository for"
    ),
    entry_point="GET /api/tickets/?page_size=100",
    fixture=FixtureRecipe(
        entity="ticket",
        # 3004 followups across 503 tickets, which the scaling script spreads
        # evenly — so this is a uniform fixture, and S-3.3's argument says that
        # is the blindest shape for a per-ticket cost. Recorded rather than
        # assumed, because the measurement below is only true of it.
        per_parent=6,
        distribution=Distribution.UNIFORM,
        source="seeds/scale_helpdesk.py, deterministic to 503 tickets / 3004 followups",
        seed=0,
    ),
    reset_method=ResetStrategy.SNAPSHOT_RESTORE,
    observations=(
        Observation(
            scale=100,
            metrics={
                DB_QUERY: 1193.0,
                RESPONSE_BYTES: 429_071.0,
                SECONDS: 1.455,
            },
        ),
    ),
)
