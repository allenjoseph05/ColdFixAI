"""Framework-specific layer. Django + Postgres first.

An adapter declares hook points, framework-internal stack frames, protected
paths, and its ORM dialect. Everything above this layer is framework-agnostic.

**Importing this package registers every adapter's grounding support. S-14.6.**
`explorer/registry.py` is pushed into by each adapter at import, so the contents
of that registry depend on what a process happened to import — ADR 050's finding
for the primitive registry, arriving a second time. A framework whose adapter
nobody imported is not *withheld*, it does not exist, and *absent* reads exactly
like *unsupported* at the fingerprint gate.

So every adapter module is imported here, and a test reads this directory for
`register(` and asserts each module it finds is reachable — from the filesystem
rather than from a list, because a list in a test is forgotten at the same moment
as the import it was meant to guard.

Epic 14.
"""

from coldfix.adapters import django as _django  # noqa: F401 - registers Django's grounding
from coldfix.adapters import flask as _flask  # noqa: F401 - and Flask's, when it has any
from coldfix.adapters.interface import (
    ADAPTER_CAPABILITIES,
    HARNESS_CAPABILITIES,
    ROW_COUNTING_VENDORS,
    Declarations,
    FrameworkAdapter,
    Subject,
    installed,
)

__all__ = [
    "ADAPTER_CAPABILITIES",
    "HARNESS_CAPABILITIES",
    "ROW_COUNTING_VENDORS",
    "Declarations",
    "FrameworkAdapter",
    "Subject",
    "installed",
]
