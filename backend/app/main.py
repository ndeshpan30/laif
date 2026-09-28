from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db
from app.api import (
    health_router,
    user_router,
    schedule_router,
    tracker_router,
    telemetry_router,
    conversation_router,
    semantic_router,
    knowledge_graph_router,
    audio_router,
)


import logging
import os
import time
from starlette.concurrency import run_in_threadpool
from app.services.whimpr_engine import get_whisper_model

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database tables on startup
    init_db()

    # Warm up local Whisper model on background threadpool (skip during test suite)
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        try:
            t0 = time.perf_counter()
            logger.info("Warming faster-whisper base.en model...")
            await run_in_threadpool(get_whisper_model)
            warmup_sec = time.perf_counter() - t0
            logger.info(f"faster-whisper model warmed up in {warmup_sec:.3f}s")
        except Exception as e:
            logger.warning(f"Could not warm up faster-whisper model: {e}")

    yield


app = FastAPI(
    title="Autonomous Cognitive Offloader API",
    description="Neuro-Symbolic Life Scheduler & Conversational BuJo Logger",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS configuration for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.ENVIRONMENT == "development" else settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routers
app.include_router(health_router)
app.include_router(user_router)
app.include_router(schedule_router)
app.include_router(tracker_router)
app.include_router(telemetry_router)
app.include_router(conversation_router)
app.include_router(semantic_router)
app.include_router(knowledge_graph_router)
app.include_router(audio_router)


import os
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse

STATIC_HTML_PATH = os.path.join(os.path.dirname(__file__), "static", "app.html")


@app.get("/")
def root(request: Request):
    accept = request.headers.get("accept", "")
    if "text/html" in accept and os.path.exists(STATIC_HTML_PATH):
        return FileResponse(STATIC_HTML_PATH, media_type="text/html")
    return {
        "service": "Autonomous Cognitive Offloader API",
        "status": "online",
        "app_ui": "/app",
        "docs_url": "/docs",
        "redoc_url": "/redoc",
    }


@app.get("/app")
def serve_app_ui():
    if os.path.exists(STATIC_HTML_PATH):
        return FileResponse(STATIC_HTML_PATH, media_type="text/html")
    return JSONResponse(status_code=404, content={"detail": "App UI not found"})
