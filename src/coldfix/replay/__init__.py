"""Measurements recorded once and returned without re-running the experiment.

Epic 5. `04-cost.md` §6 puts the replay cache first in the build order for a
reason that is about development speed rather than about the token bill: a full
investigation is ninety minutes of grounding, seeding and sweeping, which is
about five cycles a day. Replayed from a recording it is seconds, which is about
fifty. Every agent downstream of here is debugged against recordings.

The thing that makes it safe is that a recalled measurement says it is one. A
cache that returned a bare result would let a report say *measured* about a run
that did not happen in this session, and `CLAUDE.md`'s first non-negotiable is
that there is no finding without a measurement.
"""
