from fastapi import APIRouter, HTTPException, status
from datetime import datetime, timezone
from backend.app.schemas.health import HealthCheckResponse
from backend.app.core.database import ping_database
from backend.app.core.config import settings

router = APIRouter()


@router.get(
    "/health",
    response_model=HealthCheckResponse,
    status_code=status.HTTP_200_OK,
    summary="Service & Database Health Check",
    tags=["System"]
)
async def health_check():
    db_connected = await ping_database()
    if not db_connected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "degraded",
                "database": "disconnected",
                "message": "Cannot establish connection to MongoDB Atlas"
            }
        )

    return HealthCheckResponse(
        status="ok",
        environment=settings.ENVIRONMENT,
        database="connected",
        timestamp=datetime.now(timezone.utc),
        version="1.0.0"
    )
