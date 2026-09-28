from typing import List, Optional, Dict, Any
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict

from app.database import get_db
from app.models.semantic import SemanticContext
from app.services.rag import (
    ingest_syllabus_pdf,
    store_episodic_constraint,
    retrieve_semantic_context,
)

router = APIRouter(tags=["Semantic Memory & Syllabus RAG"])


class ConstraintCreate(BaseModel):
    user_id: UUID
    constraint_text: str
    source: Optional[str] = "explicit"


class SemanticContextResponse(BaseModel):
    id: UUID
    user_id: UUID
    context_type: str
    subject: Optional[str]
    raw_content: str
    metadata: Dict[str, Any]

    model_config = ConfigDict(from_attributes=True)


@router.post("/api/upload-syllabus")
@router.post("/api/semantic/upload-syllabus")
async def upload_syllabus(
    user_id: UUID = Form(...),
    subject: Optional[str] = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Ingests an academic syllabus, timetable, or lab schedule PDF.
    Extracts text using PyMuPDF, chunks into 350-word modules, generates 1536-dim embeddings,
    and stores them with context_type='syllabus_module'.
    Handles corrupt, encrypted, or empty PDFs cleanly.
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file uploaded")

    try:
        records = ingest_syllabus_pdf(
            db=db,
            user_id=user_id,
            pdf_bytes=content,
            subject=subject,
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to process PDF: {e}")

    return {
        "status": "success",
        "filename": file.filename,
        "modules_ingested": len(records),
        "subject": subject or (records[0].subject if records else "Academic Coursework"),
    }


@router.post("/api/semantic/constraints")
def add_constraint(
    payload: ConstraintCreate,
    db: Session = Depends(get_db),
):
    """
    Explicitly registers an episodic personal life constraint (e.g. 'no screen work past 10 PM').
    """
    record = store_episodic_constraint(
        db=db,
        user_id=payload.user_id,
        constraint_text=payload.constraint_text,
        source=payload.source or "explicit",
    )
    return {
        "id": str(record.id),
        "constraint": record.raw_content,
        "context_type": record.context_type,
    }


@router.get("/api/semantic/contexts")
def get_contexts(
    user_id: UUID,
    context_type: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    q = db.query(SemanticContext).filter(SemanticContext.user_id == user_id)
    if context_type:
        q = q.filter(SemanticContext.context_type == context_type)
    items = q.order_by(SemanticContext.created_at.desc()).limit(limit).all()

    return [
        {
            "id": str(it.id),
            "context_type": it.context_type,
            "subject": it.subject,
            "raw_content": it.raw_content,
            "metadata": it.context_metadata,
        }
        for it in items
    ]


@router.get("/api/semantic/query")
def query_semantic_context(
    user_id: UUID,
    query: str,
    db: Session = Depends(get_db),
):
    """
    Retrieves syllabus modules and episodic constraints matching query.
    """
    return retrieve_semantic_context(db, user_id=user_id, query=query)
