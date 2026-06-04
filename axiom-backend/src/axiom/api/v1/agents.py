"""Agent endpoints.

POST /v1/agents/run       — Execute a single-turn agent run.
GET  /v1/agents/runs/{id} — Fetch a persisted run (with full trace).
GET  /v1/agents/tools     — List available tool names and schemas.
"""

import uuid
from dataclasses import asdict

from fastapi import APIRouter, Depends
from sqlalchemy import select

from axiom.agents.executor import AgentInput, run_agent
from axiom.agents.tools import registry
from axiom.api.deps import DBSession
from axiom.core.exceptions import NotFoundError
from axiom.core.security import CurrentPrincipal, enforce_rate_limit
from axiom.db.models import AgentRun
from axiom.schemas.agent import AgentRunRequest, AgentRunResponse, AgentStepOut

router = APIRouter(dependencies=[Depends(enforce_rate_limit)])


@router.post("/run", response_model=AgentRunResponse)
async def run(
    body: AgentRunRequest,
    session: DBSession,
    principal: CurrentPrincipal,
) -> AgentRunResponse:
    inp = AgentInput(
        user_message=body.message,
        system_prompt=body.system_prompt or AgentInput.__dataclass_fields__["system_prompt"].default,
        enabled_tools=body.enabled_tools,
    )
    result = await run_agent(input_=inp, principal_id=principal.id, request_id="req-inline")

    # Persist the run
    run_row = AgentRun(
        status="succeeded" if result.outcome == "answered" else "failed",
        outcome=result.outcome,
        iterations=result.iterations,
        input={"message": body.message},
        output={"answer": result.answer},
        trace=[asdict(s) for s in result.trace],
    )
    session.add(run_row)
    await session.flush()

    return AgentRunResponse(
        answer=result.answer,
        outcome=result.outcome,
        iterations=result.iterations,
        trace=[
            AgentStepOut(
                iteration=s.iteration, kind=s.kind, payload=s.payload, duration_ms=s.duration_ms
            )
            for s in result.trace
        ],
    )


@router.get("/runs/{run_id}", response_model=AgentRunResponse)
async def get_run(run_id: uuid.UUID, session: DBSession, _: CurrentPrincipal) -> AgentRunResponse:
    row = await session.get(AgentRun, run_id)
    if row is None:
        raise NotFoundError(f"Agent run {run_id} not found.")
    return AgentRunResponse(
        answer=row.output.get("answer", ""),
        outcome=row.outcome or "unknown",
        iterations=row.iterations,
        trace=[AgentStepOut(**s) for s in row.trace],
    )


@router.get("/tools")
async def list_tools(_: CurrentPrincipal) -> dict:
    return {
        "tools": [
            {"name": spec.name, "description": spec.description, "parameters": spec.parameters}
            for spec in registry.specs()
        ]
    }
