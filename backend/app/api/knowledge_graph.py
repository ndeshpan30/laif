from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, Query, Path, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import UserProfile
from app.services.knowledge_graph import compute_knowledge_graph, recompute_edges_for

router = APIRouter(prefix="/api/knowledge-graph", tags=["Knowledge Graph"])


@router.get("")
def get_knowledge_graph(
    user_id: Optional[UUID] = Query(None, description="Target user UUID"),
    types: Optional[str] = Query(None, description="Comma-separated context types to filter by"),
    subject: Optional[str] = Query(None, description="Filter by subject"),
    min_similarity: float = Query(0.60, ge=0.0, le=1.0, description="Minimum cosine similarity threshold for edges"),
    threshold: Optional[float] = Query(None, ge=0.0, le=1.0, description="Alias for min_similarity (backward compatibility)"),
    top_k: int = Query(6, ge=1, le=20, description="Maximum nearest neighbors to connect per node"),
    limit: int = Query(300, ge=1, le=1000, description="Maximum nodes to return"),
    db: Session = Depends(get_db),
):
    """
    Upgraded Knowledge Graph Exploration Endpoint (Design Decisions D5, Phase 2).
    - Query parameters: types (comma-separated), subject, min_similarity (default 0.60), limit (default 300).
    - If N >= 1, returns existing nodes (even if isolated) with calculated edges.
    - If N == 0, returns empty lists.
    - Enriched response schema with canonical nodes, edges (cross_domain indicator),
      types_present registry, and truncated status flag.
    """
    target_user_id = user_id
    if not target_user_id:
        # Fall back to first user in profiles if user_id is omitted
        first_user = db.query(UserProfile).order_by(UserProfile.created_at.asc()).first()
        if first_user:
            target_user_id = first_user.user_id
        else:
            return {
                "nodes": [],
                "edges": [],
                "types_present": [],
                "truncated": False,
                "total_nodes": 0,
                "total_edges": 0,
            }

    effective_similarity = threshold if threshold is not None else min_similarity

    return compute_knowledge_graph(
        db=db,
        user_id=target_user_id,
        min_similarity=effective_similarity,
        threshold=threshold,
        top_k=top_k,
        limit=limit,
        types=types,
        subject=subject,
    )


@router.post("/nodes/{node_id}/recompute-edges")
def post_recompute_edges(
    node_id: UUID = Path(..., description="Target semantic context node UUID"),
    db: Session = Depends(get_db),
):
    """
    Trigger dynamic edge generation for a specific node per Design Decision D5 Phase 2.
    Purges stale semantic edges and creates top-6 nearest neighbors (cosine similarity >= 0.72)
    plus up to 2 cross-domain neighbors (similarity >= 0.60, cross_domain=True).
    """
    edges = recompute_edges_for(db, node_id)
    return {
        "node_id": str(node_id),
        "edges_count": len(edges),
        "edges": [
            {
                "source": str(e.source_id),
                "target": str(e.target_id),
                "weight": e.weight,
                "kind": e.kind,
                "cross_domain": e.cross_domain,
            }
            for e in edges
        ],
    }
