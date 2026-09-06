"""Contracts shared across the packages that would otherwise import each other.

This package is a leaf: it imports from `primitives`, `sandbox`, `bench` and
`cost`, and from nothing that imports it back. That property is the reason it
exists, and `tests/test_layering.py` asserts it rather than trusting review.

Three concepts were reached for across a seven-package cycle
(`audit, diagnosis, explorer, repair, replay, screening, state`):

- `workload` — what a screened unit of work *is*, plus the fixture recipe and
  observation types every later stage quotes;
- `surface` — the protocol a session exposes for running something;
- `sessions` — the guard that refuses a session belonging to another agent.

None of them belonged to the package that happened to define them first.
"""
