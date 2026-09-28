import datetime
from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["Health"])


@router.get("/health")
def get_health():
    return {
        "status": "healthy",
        "service": "Autonomous Cognitive Offloader API",
        "solver": "Google OR-Tools CP-SAT (Native C++)",
        "discretization": "15-minute ticks (672 ticks/week)",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
