"""Where things run, and what they are structurally unable to do while running.

Every workload and every experiment executes inside a container with no route
off the host, a read-only root, one writable directory, and finite CPU, memory
and process budgets. Each runs against its own git worktree, checked out
detached at a fixed commit and provably removed afterwards. The isolation is a
property of the objects you must construct in order to run anything, not a
convention an agent is asked to observe.

The two halves are deliberately unaware of each other. Binding a worktree to a
container is what makes ADR 004's separation structural, and that is S-2.3's
job — coupling them here would put the enforcement in the same place as the
mechanism it is supposed to constrain.

Epic 2. See ADR 004 for why containers and worktrees, ADR 020 for the container
flags, and ADR 021 for why every worktree is detached.
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
from coldfix.sandbox.worktrees import (
    DirtyWorkingTreeError,
    NotARepositoryError,
    Repository,
    UnknownRevisionError,
    Worktree,
    WorktreeError,
    WorktreeNotDestroyedError,
    WorktreePathError,
)

__all__ = [
    "DEFAULT_LIMITS",
    "WORKSPACE_MOUNTPOINT",
    "ContainerNotDestroyedError",
    "DirtyWorkingTreeError",
    "DockerUnavailableError",
    "MemoryLimitExceededError",
    "NotARepositoryError",
    "Repository",
    "ResourceLimits",
    "Sandbox",
    "SandboxError",
    "SandboxStartError",
    "UnknownRevisionError",
    "WorkspaceError",
    "Worktree",
    "WorktreeError",
    "WorktreeNotDestroyedError",
    "WorktreePathError",
    "docker_available",
    "docker_run_argv",
]
