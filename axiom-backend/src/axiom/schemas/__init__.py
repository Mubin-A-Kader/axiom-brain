"""API schemas — Pydantic v2 request/response models.

These live separately from ORM models because:
- API stability shouldn't be coupled to DB schema.
- Output models can selectively hide internal fields.
- Validation rules belong at the boundary.
"""

from pydantic import BaseModel, ConfigDict


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")
