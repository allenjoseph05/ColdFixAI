"""Finding what is worth investigating, using zero model calls.

Epic 4. Screening is the largest cost gate in the system — `04-cost.md` §9 puts
it at roughly 70% of workloads eliminated before any agent runs — and it is
deterministic by construction: counting, fitting and ranking are code, and
`CLAUDE.md` forbids a model call where a function would do.

`08-audit.md` F8 cut what screening can claim. Bound comparison applies
opportunistically rather than as a universal pre-check, so in the general case
screening is **scaling plus flat-cost detection** and nothing more.
"""
