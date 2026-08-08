"""Where things run, and what they are structurally unable to do while running.

Every workload and every experiment executes inside a container with no route
off the host, a read-only root, one writable directory, and finite CPU, memory
and process budgets. The isolation is a property of the object you must
construct in order to run anything, not a convention an agent is asked to
observe.

Epic 2. See ADR 004 for why containers, and ADR 020 for what the flags are.
"""

from coldfix.sandbox.runner import (
    DEFAULT_LIMITS,
    WORKSPACE_MOUNTPOINT,
    ContainerNotDestroyedError,
    DockerUnavailableError,
    MemoryLimitExceededError,
    ResourceLimits,
    Sandbox,
    SandboxError,
    SandboxStartError,
    WorkspaceError,
    docker_available,
    docker_run_argv,
)

__all__ = [
    "DEFAULT_LIMITS",
    "WORKSPACE_MOUNTPOINT",
    "ContainerNotDestroyedError",
    "DockerUnavailableError",
    "MemoryLimitExceededError",
    "ResourceLimits",
    "Sandbox",
    "SandboxError",
    "SandboxStartError",
    "WorkspaceError",
    "docker_available",
    "docker_run_argv",
]
