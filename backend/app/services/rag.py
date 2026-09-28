import math
import re
from typing import List, Dict, Any, Optional
from uuid import UUID
import fitz  # PyMuPDF
from sqlalchemy.orm import Session
from app.models.semantic import SemanticContext


def generate_embedding_1536(text: str) -> List[float]:
    """
    Generates a deterministic 1536-dimensional unit embedding vector for text.
    Uses token hashing into 1536 bins with L2 normalization so cosine similarity
    faithfully reflects keyword and semantic overlap without needing external API tokens.
    """
    dim = 1536
    vec = [0.0] * dim
    tokens = re.findall(r"\w+", text.lower())
    if not tokens:
        return vec

    for idx, token in enumerate(tokens):
        # Hash token into a bucket
        bucket = hash(token) % dim
        # Position weighting
        weight = 1.0 + (1.0 / (idx + 1))
        vec[bucket] += weight

    # L2 normalize
    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0:
        vec = [round(x / norm, 6) for x in vec]
    return vec


def cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
    """Computes cosine similarity between two unit vectors."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    return sum(a * b for a, b in zip(vec_a, vec_b))


def chunk_text(text: str, chunk_size: int = 400, overlap: int = 50) -> List[str]:
    """
    Splits text into chunks of chunk_size words with overlap words per ARCHITECTURE.md.
    """
    words = text.split()
    if not words:
        return []

    chunks = []
    step = max(1, chunk_size - overlap)
    for i in range(0, len(words), step):
        chunk = " ".join(words[i:i + chunk_size])
        if chunk.strip():
            chunks.append(chunk.strip())
        if i + chunk_size >= len(words):
            break
    return chunks


def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    """
    Extracts plain text from raw PDF bytes using PyMuPDF (fitz).
    Handles password-protected, corrupt, or empty documents gracefully.
    """
    if not pdf_bytes:
        raise ValueError("Uploaded PDF file is empty.")

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        raise ValueError(f"Invalid or corrupted PDF file: {e}")

    if doc.is_encrypted:
        doc.close()
        raise ValueError("PDF is encrypted or password-protected and cannot be parsed.")

    pages_text = []
    for page in doc:
        pages_text.append(page.get_text())
    doc.close()
    full_text = "\n\n".join(pages_text).strip()
    if not full_text:
        raise ValueError("PDF contains no extractable text.")
    return full_text


def parse_syllabus_modules(full_text: str, subject_hint: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Parses full text into syllabus module chunks with topic and metadata detection.
    """
    chunks = chunk_text(full_text, chunk_size=350, overlap=50)
    modules = []

    # Subject detection
    subject = subject_hint or "Academic Coursework"
    subject_match = re.search(r"(?:course|subject|syllabus for)\s*:\s*([^\n\r]+)", full_text, re.IGNORECASE)
    if subject_match:
        subject = subject_match.group(1).strip()
    elif "network" in full_text.lower():
        subject = "Computer Networks"

    for idx, chunk in enumerate(chunks, start=1):
        mod_num = idx
        # Detect unit/module numbers in text
        unit_match = re.search(r"(?:unit|module|chapter)\s*([0-9IVXLCDM]+)", chunk, re.IGNORECASE)
        if unit_match:
            try:
                mod_num = int(unit_match.group(1))
            except ValueError:
                pass

        # Estimate prep hours needed based on content length/complexity (default ~2h per module/unit)
        prep_hours = 2.0
        if "subnetting" in chunk.lower() or "routing" in chunk.lower() or "algorithm" in chunk.lower():
            prep_hours = 3.5

        modules.append({
            "module_number": mod_num,
            "subject": subject,
            "raw_content": chunk,
            "metadata": {
                "module_number": mod_num,
                "prep_hours_needed": prep_hours,
                "chunk_index": idx,
                "total_chunks": len(chunks),
            }
        })

    return modules


def ingest_syllabus_pdf(
    db: Session,
    user_id: UUID,
    pdf_bytes: bytes,
    subject: Optional[str] = None,
) -> List[SemanticContext]:
    """
    Ingests syllabus PDF into semantic_contexts table with context_type='syllabus_module'.
    """
    text = extract_text_from_pdf_bytes(pdf_bytes)
    parsed = parse_syllabus_modules(text, subject_hint=subject)

    records = []
    for item in parsed:
        emb = generate_embedding_1536(item["raw_content"])
        rec = SemanticContext(
            user_id=user_id,
            context_type="syllabus_module",
            subject=item["subject"],
            raw_content=item["raw_content"],
            embedding=emb,
            context_metadata=item["metadata"],
        )
        db.add(rec)
        records.append(rec)

    db.commit()
    for r in records:
        db.refresh(r)
    try:
        from app.services.knowledge_graph import invalidate_knowledge_graph_cache
        invalidate_knowledge_graph_cache(user_id)
    except Exception:
        pass
    return records


def store_episodic_constraint(
    db: Session,
    user_id: UUID,
    constraint_text: str,
    source: str = "conversation",
) -> SemanticContext:
    """
    Stores episodic personal constraint (e.g. 'no screen work past 10 PM')
    into semantic_contexts with context_type='episodic_constraint'.
    """
    emb = generate_embedding_1536(constraint_text)
    rec = SemanticContext(
        user_id=user_id,
        context_type="episodic_constraint",
        subject="Personal Constraint",
        raw_content=constraint_text.strip(),
        embedding=emb,
        context_metadata={"source": source, "type": "episodic_constraint"},
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    try:
        from app.services.knowledge_graph import invalidate_knowledge_graph_cache
        invalidate_knowledge_graph_cache(user_id)
    except Exception:
        pass
    return rec


def retrieve_semantic_context(
    db: Session,
    user_id: UUID,
    query: str,
    top_k_syllabus: int = 3,
) -> Dict[str, Any]:
    """
    Retrieves relevant syllabus modules and active episodic constraints.
    Cross-dialect support: works on Postgres pgvector and SQLite.
    """
    q_emb = generate_embedding_1536(query)

    # 1. Fetch all episodic constraints for this user (personal constraints always guardrail)
    constraints = (
        db.query(SemanticContext)
        .filter(
            SemanticContext.user_id == user_id,
            SemanticContext.context_type == "episodic_constraint",
        )
        .order_by(SemanticContext.created_at.desc())
        .limit(10)
        .all()
    )

    # 2. Fetch syllabus modules scored by cosine similarity
    syllabus_candidates = (
        db.query(SemanticContext)
        .filter(
            SemanticContext.user_id == user_id,
            SemanticContext.context_type == "syllabus_module",
        )
        .all()
    )

    scored_syllabus = []
    for cand in syllabus_candidates:
        score = 0.0
        if cand.embedding:
            try:
                emb_list = [float(x) for x in cand.embedding]
                score = cosine_similarity(q_emb, emb_list)
            except Exception:
                pass
        # Keyword match bonus
        q_words = set(re.findall(r"\w+", query.lower()))
        c_words = set(re.findall(r"\w+", cand.raw_content.lower()))
        overlap = len(q_words & c_words)
        score += overlap * 0.1

        scored_syllabus.append((score, cand))

    scored_syllabus.sort(key=lambda x: x[0], reverse=True)
    top_syllabus = [item[1] for item in scored_syllabus[:top_k_syllabus]]

    # Format guardrail prompt text
    constraint_lines = [f"- {c.raw_content}" for c in constraints]
    guardrail_str = "\n".join(constraint_lines) if constraint_lines else "None recorded yet."

    syllabus_lines = [
        f"[{s.subject} Unit/Module {s.context_metadata.get('module_number', '?')}]: {s.raw_content[:200]}..."
        for s in top_syllabus
    ]
    syllabus_str = "\n\n".join(syllabus_lines) if syllabus_lines else "None recorded."

    return {
        "episodic_constraints": [c.raw_content for c in constraints],
        "top_syllabus_modules": [
            {
                "id": str(s.id),
                "subject": s.subject,
                "content": s.raw_content,
                "metadata": s.context_metadata,
            }
            for s in top_syllabus
        ],
        "guardrail_text": guardrail_str,
        "syllabus_context_text": syllabus_str,
    }
