"""Evals endpoints.

POST /v1/evals/datasets              — Create a named dataset.
POST /v1/evals/datasets/{id}/examples — Bulk-add (input, expected) pairs.
POST /v1/evals/runs                  — Start an eval run (async via worker).
GET  /v1/evals/runs/{id}             — Poll run status and summary.
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select

from axiom.api.deps import DBSession
from axiom.core.exceptions import NotFoundError
from axiom.core.security import CurrentPrincipal, enforce_rate_limit
from axiom.db.models import EvalDataset, EvalExample, EvalRun
from axiom.schemas.eval import (
    AddExamplesRequest,
    CreateDatasetRequest,
    DatasetOut,
    EvalRunOut,
    StartEvalRunRequest,
)
from axiom.workers.tasks import enqueue_eval_run

router = APIRouter(dependencies=[Depends(enforce_rate_limit)])


@router.post("/datasets", response_model=DatasetOut, status_code=201)
async def create_dataset(
    body: CreateDatasetRequest,
    session: DBSession,
    _: CurrentPrincipal,
) -> DatasetOut:
    dataset = EvalDataset(name=body.name, description=body.description)
    session.add(dataset)
    await session.flush()
    return DatasetOut(id=str(dataset.id), name=dataset.name, description=dataset.description)


@router.post("/datasets/{dataset_id}/examples", status_code=201)
async def add_examples(
    dataset_id: uuid.UUID,
    body: AddExamplesRequest,
    session: DBSession,
    _: CurrentPrincipal,
) -> dict:
    dataset = await session.get(EvalDataset, dataset_id)
    if dataset is None:
        raise NotFoundError(f"Dataset {dataset_id} not found.")

    rows = [
        EvalExample(
            dataset_id=dataset_id,
            input=ex.get("input", ex),
            expected=ex.get("expected", {}),
        )
        for ex in body.examples
    ]
    session.add_all(rows)
    await session.flush()
    return {"added": len(rows)}


@router.post("/runs", response_model=EvalRunOut, status_code=202)
async def start_eval_run(
    body: StartEvalRunRequest,
    session: DBSession,
    _: CurrentPrincipal,
) -> EvalRunOut:
    dataset = await session.get(EvalDataset, body.dataset_id)
    if dataset is None:
        raise NotFoundError(f"Dataset {body.dataset_id} not found.")

    run = EvalRun(
        dataset_id=body.dataset_id,
        config={
            "target": body.target,
            "metrics": body.metrics,
            "threshold": body.threshold,
            "concurrency": body.concurrency,
        },
        status="pending",
    )
    session.add(run)
    await session.flush()

    # Dispatch to background worker
    await enqueue_eval_run(str(run.id))
    return EvalRunOut(id=str(run.id), status=run.status, summary=run.summary)


@router.get("/runs/{run_id}", response_model=EvalRunOut)
async def get_eval_run(run_id: uuid.UUID, session: DBSession, _: CurrentPrincipal) -> EvalRunOut:
    run = await session.get(EvalRun, run_id)
    if run is None:
        raise NotFoundError(f"Eval run {run_id} not found.")
    return EvalRunOut(id=str(run.id), status=run.status, summary=run.summary)
