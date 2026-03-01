from enum import StrEnum


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
