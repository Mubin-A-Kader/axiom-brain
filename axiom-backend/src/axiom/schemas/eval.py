import uuid

from pydantic import Field

from axiom.schemas import APIModel


class CreateDatasetRequest(APIModel):
    name: str = Field(min_length=1, max_length=256)
    description: str | None = None


class DatasetOut(APIModel):
    id: str
    name: str
    description: str | None


class AddExamplesRequest(APIModel):
    examples: list[dict] = Field(min_length=1, max_length=1000)


class StartEvalRunRequest(APIModel):
    dataset_id: uuid.UUID
    target: str = Field(
        description="'rag' or 'agent' — which pipeline to eval.",
        pattern="^(rag|agent)$",
    )
    metrics: list[str] = Field(default=["llm_judge"])
    threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    concurrency: int = Field(default=4, ge=1, le=16)


class EvalRunOut(APIModel):
    id: str
    status: str
    summary: dict
