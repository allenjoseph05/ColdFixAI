"""Framework-specific layer. Django + Postgres first.

An adapter declares hook points, framework-internal stack frames, protected
paths, and its ORM dialect. Everything above this layer is framework-agnostic.

Epic 14.
"""

from coldfix.adapters.interface import (
    ADAPTER_CAPABILITIES,
    HARNESS_CAPABILITIES,
    Declarations,
    FrameworkAdapter,
    Subject,
    installed,
)

__all__ = [
    "ADAPTER_CAPABILITIES",
    "HARNESS_CAPABILITIES",
    "Declarations",
    "FrameworkAdapter",
    "Subject",
    "installed",
]
