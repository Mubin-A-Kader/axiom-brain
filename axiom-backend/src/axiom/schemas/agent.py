from pydantic import Field

from axiom.schemas import APIModel


class AgentRunRequest(APIModel):
    message: str = Field(min_length=1, max_length=10_000)
    system_prompt: str | None = None
    enabled_tools: list[str] | None = None


class AgentStepOut(APIModel):
    iteration: int
    kind: str
    payload: dict
    duration_ms: float


class AgentRunResponse(APIModel):
    answer: str
    outcome: str
    iterations: int
    trace: list[AgentStepOut]
