"""Lecture-scoped occupancy analytics routes."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from cloud_backend.attendance.occupancy_analytics import fetch_lecture_occupancy_analytics
from cloud_backend.attendance.schemas.occupancy import OccupancyAnalyticsResponse
from cloud_backend.db.session import get_async_session

router = APIRouter(prefix="/attendance", tags=["attendance"])


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_async_session():
        yield session


@router.get(
    "/lectures/{lecture_id}/occupancy/analytics",
    response_model=OccupancyAnalyticsResponse,
)
async def get_lecture_occupancy_analytics(
    lecture_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> OccupancyAnalyticsResponse:
    """Derived occupancy metrics for operational consistency analytics."""
    result = await fetch_lecture_occupancy_analytics(session, lecture_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Lecture not found")
    return result
