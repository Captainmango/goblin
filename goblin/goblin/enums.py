from enum import StrEnum


class FailureAction(StrEnum):
    SKIP = "skip"
    RETRY = "retry"
    ABORT = "abort"


class PhaseType(StrEnum):
    PROMPT = "prompt"
    LOAD_PROJECT = "load_project"
    SWARM = "swarm"


class SwarmDBTable(StrEnum):
    TASKS = "tasks"
    ARTIFACTS = "artifacts"
    SIGNALS = "signals"


class LoadProjectStatus(StrEnum):
    LOADED = "loaded"


class SwarmState(StrEnum):
    SETUP = "setup"
    COORDINATOR = "coordinator"
    AGENTS_RUNNING = "agents_running"
    EVALUATING = "evaluating"
    PAUSED = "paused"
    TEARDOWN = "teardown"


class TaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"


class AgentStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class CleanupPolicy(StrEnum):
    ON_SUCCESS = "on_success"
    ALWAYS = "always"
    NEVER = "never"


class RunStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class PhaseStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
