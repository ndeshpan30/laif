from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.knowledge_graph import compute_knowledge_graph

router = APIRouter(prefix="/api/knowledge-graph", tags=["Knowledge Graph"])


@router.get("")
def get_knowledge_graph(
    user_id: UUID = Query(..., description="Target user UUID"),
    threshold: float = Query(0.75, ge=0.0, le=1.0, description="Minimum cosine similarity threshold for edges"),
    top_k: int = Query(5, ge=1, le=20, description="Maximum nearest neighbors to connect per node"),
    limit: int = Query(300, ge=1, le=1000, description="Maximum nodes to return"),
    db: Session = Depends(get_db),
):
    """
    Returns an Obsidian-style Knowledge Graph over the user's semantic_contexts:
    - Nodes: syllabus_module and episodic_constraint entries
    - Edges: semantic similarity connections computed server-side via pgvector/embeddings
    - Includes precomputed degree, labels, snippets, and edge weights.
    """
    return compute_knowledge_graph(
        db=db,
        user_id=user_id,
        threshold=threshold,
        top_k=top_k,
        limit=limit,
    )
