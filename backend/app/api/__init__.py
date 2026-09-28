from app.api.health import router as health_router
from app.api.user import router as user_router
from app.api.schedule import router as schedule_router
from app.api.trackers import router as tracker_router
from app.api.telemetry import router as telemetry_router
from app.api.conversation import router as conversation_router
from app.api.semantic import router as semantic_router
from app.api.knowledge_graph import router as knowledge_graph_router
from app.api.audio import router as audio_router

__all__ = [
    "health_router",
    "user_router",
    "schedule_router",
    "tracker_router",
    "telemetry_router",
    "conversation_router",
    "semantic_router",
    "knowledge_graph_router",
    "audio_router",
]
