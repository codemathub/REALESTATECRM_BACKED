import sys
from pathlib import Path

# Ensure project root is in sys.path so backend.app.* imports resolve anywhere
_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.app.core.config import settings
from backend.app.core.database import connect_to_mongo, close_mongo_connection
from backend.app.api.v1.router import api_v1_router

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s"
)
logger = logging.getLogger("estateflow.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Connect to MongoDB
    logger.info("Initializing application lifespan...")
    try:
        await connect_to_mongo()
    except Exception as e:
        logger.error(f"Failed to connect to MongoDB during startup: {e}")
    yield
    # Shutdown: Close connections
    logger.info("Shutting down application lifespan...")
    await close_mongo_connection()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version="1.0.0",
    description="Backend API for G² Brothers CRM (Admin, Executive, Super Admin)",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS Middleware (Compliant with CORS standard: no wildcard with credentials)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_origin_regex=r"^https?://.*" if settings.ENVIRONMENT == "development" else None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount versioned API routes
app.include_router(api_v1_router, prefix=settings.API_V1_PREFIX)


@app.get("/", tags=["Root"])
async def root():
    return {
        "app": settings.PROJECT_NAME,
        "version": "1.0.0",
        "docs": "/docs",
        "health": f"{settings.API_V1_PREFIX}/health"
    }


# ---------------------------------------------------------------------------
# Direct execution: bind to 0.0.0.0 so mobile devices on the same Wi-Fi can
# reach the API.  Usage:  python -m backend.app.main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    logger.info(
        f"Starting G² Brothers CRM API on http://{settings.HOST}:{settings.PORT}  "
        f"(accessible to LAN devices at http://<your-pc-ip>:{settings.PORT})"
    )
    uvicorn.run(
        "backend.app.main:app",
        host=settings.HOST,   # 0.0.0.0
        port=settings.PORT,   # 8000
        reload=True,
        log_level="info",
    )
