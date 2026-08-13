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

from coldfix.sandbox.modes import (
    CandidateSession,
    DiagnosticSession,
    ExecutionMode,
    Session,
    SessionClosedError,
    SessionError,
    Workbench,
)
from coldfix.sandbox.patching import (
    DEFAULT_PATCH_POLICY,
    DEFAULT_PROTECTED_PATTERNS,
    PatchDidNotApplyError,
    PatchError,
    PatchPolicy,
    ProtectedPathError,
    UnparsablePatchError,
    UnsafePathError,
    apply_patch,
    touched_paths,
)
from coldfix.sandbox.production import (
    DEFAULT_ALLOWED_HOSTS,
    DEFAULT_ALLOWED_NAME_PATTERNS,
    DEFAULT_ALLOWED_SCHEMES,
    DEFAULT_DATABASE_POLICY,
    DatabasePolicy,
    ProductionDatabaseError,
    ProductionGuardError,
    UnreadableDatabaseUrlError,
    VerifiedDatabase,
    redact,
)
from coldfix.sandbox.reset import (
    ContainerRestartReset,
    DatabaseNotReadyError,
    ResetError,
    ResetMechanism,
    ResetNotPreparedError,
    ResetStrategy,
    RollbackReset,
    SequenceValue,
    SnapshotRestoreReset,
)
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
    "DEFAULT_ALLOWED_HOSTS",
    "DEFAULT_ALLOWED_NAME_PATTERNS",
    "DEFAULT_ALLOWED_SCHEMES",
    "DEFAULT_DATABASE_POLICY",
    "DEFAULT_LIMITS",
    "DEFAULT_PATCH_POLICY",
    "DEFAULT_PROTECTED_PATTERNS",
    "WORKSPACE_MOUNTPOINT",
    "CandidateSession",
    "ContainerNotDestroyedError",
    "ContainerRestartReset",
    "DatabaseNotReadyError",
    "DatabasePolicy",
    "DiagnosticSession",
    "DirtyWorkingTreeError",
    "DockerUnavailableError",
    "ExecutionMode",
    "MemoryLimitExceededError",
    "NotARepositoryError",
    "PatchDidNotApplyError",
    "PatchError",
    "PatchPolicy",
    "ProductionDatabaseError",
    "ProductionGuardError",
    "ProtectedPathError",
    "Repository",
    "ResetError",
    "ResetMechanism",
    "ResetNotPreparedError",
    "ResetStrategy",
    "ResourceLimits",
    "RollbackReset",
    "Sandbox",
    "SandboxError",
    "SandboxStartError",
    "SequenceValue",
    "Session",
    "SessionClosedError",
    "SessionError",
    "SnapshotRestoreReset",
    "UnknownRevisionError",
    "UnparsablePatchError",
    "UnreadableDatabaseUrlError",
    "UnsafePathError",
    "VerifiedDatabase",
    "Workbench",
    "WorkspaceError",
    "Worktree",
    "WorktreeError",
    "WorktreeNotDestroyedError",
    "WorktreePathError",
    "apply_patch",
    "docker_available",
    "docker_run_argv",
    "redact",
    "touched_paths",
]
