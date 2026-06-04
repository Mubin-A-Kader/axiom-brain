"""Shared FastAPI dependency type aliases."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from axiom.db.session import get_db_session

DBSession = Annotated[AsyncSession, Depends(get_db_session)]
