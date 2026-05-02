"""
AxiomTask — data types for the TaskPlannerAgent.

A TaskPlan is an ordered list of AxiomTasks produced by TaskPlannerNode.
TaskExecutorNode runs them in dependency order and stores results in TaskPlan.results.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional


class TaskState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class AgentType(str, Enum):
    SQL = "SQL"           # Query a specific data source and return rows
    SYNTHESIS = "SYNTHESIS"  # Correlate/combine results from prior tasks — no DB call


@dataclass
class AxiomTask:
    id: str
    content: str                      # Natural language description of what to do
    agent_type: AgentType
    source_id: Optional[str] = None  # Which data source to query (SQL tasks only)
    depends_on: list[str] = field(default_factory=list)
    state: TaskState = TaskState.PENDING
    result: Optional[str] = None     # JSON (SQL) or narrative text (SYNTHESIS)
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["agent_type"] = self.agent_type.value
        d["state"] = self.state.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AxiomTask":
        return cls(
            id=d["id"],
            content=d["content"],
            agent_type=AgentType(d["agent_type"]),
            source_id=d.get("source_id"),
            depends_on=d.get("depends_on", []),
            state=TaskState(d.get("state", TaskState.PENDING)),
            result=d.get("result"),
            error=d.get("error"),
        )


@dataclass
class TaskPlan:
    tasks: list[AxiomTask]

    # ── Topological execution helpers ───────────────────────────────────────

    def ready(self) -> list[AxiomTask]:
        """Return tasks whose dependencies are all DONE and which are still PENDING."""
        done_ids = {t.id for t in self.tasks if t.state == TaskState.DONE}
        return [
            t for t in self.tasks
            if t.state == TaskState.PENDING
            and all(dep in done_ids for dep in t.depends_on)
        ]

    def is_complete(self) -> bool:
        return all(t.state in (TaskState.DONE, TaskState.FAILED, TaskState.SKIPPED) for t in self.tasks)

    def results_context(self, for_task: AxiomTask) -> str:
        """Build a context block of all dependency results for a given task."""
        dep_set = set(for_task.depends_on)
        parts: list[str] = []
        for t in self.tasks:
            if t.id in dep_set and t.result:
                parts.append(f"### Result from '{t.content}':\n{t.result}")
        return "\n\n".join(parts)

    def to_state(self) -> list[dict[str, Any]]:
        return [t.to_dict() for t in self.tasks]

    @classmethod
    def from_state(cls, raw: list[dict[str, Any]]) -> "TaskPlan":
        return cls(tasks=[AxiomTask.from_dict(d) for d in raw])
