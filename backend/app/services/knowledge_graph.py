import datetime
import hashlib
import logging
from typing import Dict, Any, List, Optional, Tuple, Set
from uuid import UUID
from sqlalchemy import event, select, delete
from sqlalchemy.orm import Session

from app.models.semantic import SemanticContext, ContextEdge, compute_content_hash
from app.models.tracker import TrackerDefinition
from app.models.telemetry import TelemetryLog
from app.models.schedule import ScheduleItem
from app.services.rag import cosine_similarity, generate_embedding_1536

logger = logging.getLogger(__name__)

# =====================================================================
# Canonical Type Registry (Design Decisions D4 & D5)
# =====================================================================
CANONICAL_TYPE_REGISTRY: Dict[str, Dict[str, str]] = {
    "episodic_constraint": {
        "group": "CONSTRAINTS",
        "glyph": "!",
        "style": "fill var(--accent)",
    },
    "syllabus_module": {
        "group": "SYLLABUS",
        "glyph": "M",
        "style": "fill var(--text-ink), text var(--bg-paper)",
    },
    "life_habit": {
        "group": "TELEMETRY & HABITS",
        "glyph": "~",
        "style": "fill var(--ochre)",
    },
    "telemetry_entry": {
        "group": "TELEMETRY & HABITS",
        "glyph": "T",
        "style": "paper fill, ink outline",
    },
    "project_goal": {
        "group": "GOALS & PROJECTS",
        "glyph": "G",
        "style": "paper fill, double hairline",
    },
}

DEFAULT_TYPE_METADATA: Dict[str, str] = {
    "group": "OTHER",
    "glyph": "•",
    "style": "neutral ink outline",
}


def get_canonical_type_info(context_type: Optional[str]) -> Dict[str, str]:
    """
    Returns canonical type info (group, glyph, style) for a given context_type.
    Unknown types default to Group: OTHER, Glyph: '•', Style: 'neutral ink outline'.
    """
    if not context_type:
        return DEFAULT_TYPE_METADATA.copy()
    return CANONICAL_TYPE_REGISTRY.get(context_type.strip().lower(), DEFAULT_TYPE_METADATA).copy()


def get_canonical_group(context_type: Optional[str]) -> str:
    """Returns the canonical group name for a given context_type."""
    return get_canonical_type_info(context_type)["group"]


def get_canonical_glyph(context_type: Optional[str]) -> str:
    """Returns the canonical glyph icon for a given context_type."""
    return get_canonical_type_info(context_type)["glyph"]


def get_canonical_style(context_type: Optional[str]) -> str:
    """Returns the canonical CSS visual style for a given context_type."""
    return get_canonical_type_info(context_type)["style"]


def order_edge_endpoints(id_a: Any, id_b: Any) -> Tuple[Any, Any]:
    """
    Enforces canonical ordering source_id < target_id on edge endpoints.
    Swaps endpoints if needed; raises ValueError if endpoints are identical.
    """
    s_a = str(id_a)
    s_b = str(id_b)
    if s_a > s_b:
        return id_b, id_a
    elif s_a == s_b:
        raise ValueError(f"Self-referential edge not allowed: {id_a} == {id_b}")
    return id_a, id_b


def create_context_edge(
    db: Session,
    user_id: UUID,
    source_id: UUID,
    target_id: UUID,
    kind: str = "semantic",
    weight: float = 1.0,
    cross_domain: bool = False,
) -> ContextEdge:
    """
    Creates and persists a ContextEdge enforcing canonical ordering: source_id < target_id.
    """
    src, tgt = order_edge_endpoints(source_id, target_id)
    edge = ContextEdge(
        user_id=user_id,
        source_id=src,
        target_id=tgt,
        kind=kind,
        weight=weight,
        cross_domain=cross_domain,
    )
    db.add(edge)
    db.commit()
    db.refresh(edge)
    invalidate_knowledge_graph_cache(user_id)
    return edge


# In-memory graph cache: user_id -> {"timestamp": dt, "hash": str, "data": graph_dict}
_GRAPH_CACHE: Dict[str, Dict[str, Any]] = {}


def invalidate_knowledge_graph_cache(user_id: Optional[UUID] = None):
    """Clears the graph cache for a specific user, or all users if None."""
    global _GRAPH_CACHE
    if user_id:
        key_prefix = str(user_id)
        keys_to_remove = [k for k in _GRAPH_CACHE if k.startswith(key_prefix)]
        for k in keys_to_remove:
            _GRAPH_CACHE.pop(k, None)
    else:
        _GRAPH_CACHE.clear()


# =====================================================================
# Ingestion Pipeline: sync_node & delete_synced_node (D1, D3, D6)
# =====================================================================

def get_embedding(text: str) -> List[float]:
    """
    Mockable abstraction for generating 1536-dimensional unit embeddings
    via the configured embedding service.
    """
    return generate_embedding_1536(text)


def compute_tracker_stats(db: Session, user_id: UUID, tracker: TrackerDefinition) -> str:
    """
    Computes a rolling summary of recent telemetry logs for a given tracker.
    Returns strings such as '3 entries, avg: 2.0', '5/7 completed', or 'no entries logged yet'.
    """
    logs = (
        db.query(TelemetryLog)
        .filter(TelemetryLog.user_id == user_id)
        .order_by(TelemetryLog.logged_at.desc())
        .limit(100)
        .all()
    )

    matched = []
    for log in logs:
        meta = log.log_metadata if isinstance(log.log_metadata, dict) else {}
        if meta.get("tracker_name") == tracker.name or log.entry_type == tracker.category:
            matched.append(log)

    if not matched:
        return "no entries logged yet"

    if tracker.category == "binary_habit" or tracker.data_type == "boolean":
        completions = sum(
            1 for l in matched
            if (l.log_metadata if isinstance(l.log_metadata, dict) else {}).get("value")
            in (True, "true", "True", 1, "1")
        )
        return f"{completions}/{len(matched)} completed"

    if tracker.category in ("metric", "volume") or tracker.data_type in ("float", "integer"):
        vals = []
        for l in matched:
            meta = l.log_metadata if isinstance(l.log_metadata, dict) else {}
            val = meta.get("value")
            if isinstance(val, (int, float)):
                vals.append(float(val))
            elif isinstance(val, str):
                try:
                    vals.append(float(val))
                except ValueError:
                    pass
        if vals:
            avg_val = round(sum(vals) / len(vals), 1)
            unit_suffix = f" {tracker.unit}" if tracker.unit else ""
            return f"{len(matched)} entries, avg: {avg_val}{unit_suffix}"

    return f"{len(matched)} entries logged"


def _link_journal_note_to_tracker(
    db: Session,
    user_id: UUID,
    log: TelemetryLog,
    note_ctx: SemanticContext,
):
    """
    Finds the parent tracker definition for a journal note and creates
    a structural context edge between the journal note node and the tracker node.
    """
    try:
        meta = log.log_metadata if isinstance(log.log_metadata, dict) else {}
        tracker_name = meta.get("tracker_name")
        if not tracker_name:
            return

        parent_tracker = (
            db.query(TrackerDefinition)
            .filter(
                TrackerDefinition.user_id == user_id,
                TrackerDefinition.name == tracker_name,
            )
            .first()
        )
        if not parent_tracker:
            return

        # Check if parent tracker already has a SemanticContext
        tracker_ctx = (
            db.query(SemanticContext)
            .filter(
                SemanticContext.user_id == user_id,
                SemanticContext.source_table == "tracker_definitions",
                SemanticContext.source_id == parent_tracker.id,
            )
            .first()
        )
        if not tracker_ctx:
            tracker_ctx = sync_node(db, user_id, "tracker_definitions", parent_tracker.id)

        if tracker_ctx and note_ctx and tracker_ctx.id != note_ctx.id:
            src_id, tgt_id = order_edge_endpoints(note_ctx.id, tracker_ctx.id)
            existing_edge = (
                db.query(ContextEdge)
                .filter(
                    ContextEdge.user_id == user_id,
                    ContextEdge.source_id == src_id,
                    ContextEdge.target_id == tgt_id,
                    ContextEdge.kind == "structural",
                )
                .first()
            )
            if not existing_edge:
                edge = ContextEdge(
                    user_id=user_id,
                    source_id=src_id,
                    target_id=tgt_id,
                    kind="structural",
                    weight=1.0,
                    cross_domain=False,
                )
                db.add(edge)
                db.commit()
                db.refresh(edge)
                invalidate_knowledge_graph_cache(user_id)
    except Exception as e:
        logger.warning("Failed to link journal note to parent tracker: %s", e)


_LAST_TRACKER_SYNC: Dict[Tuple[str, str], datetime.datetime] = {}
DEBOUNCE_SECONDS: float = 2.0


def sync_node(
    db: Session,
    user_id: UUID,
    source_table: str,
    source_id: UUID,
) -> Optional[SemanticContext]:
    """
    Synchronizes an application entity (tracker_definitions, telemetry_logs, schedule_items)
    into semantic_contexts per Design Decisions D1, D3, and D6.

    - tracker_definitions: builds canonical text:
      "Habit tracker: {name}, {category} in {unit}. Rolling summary: {stats}."
      context_type='life_habit'.
    - telemetry_logs: only if category == 'journal_note', extracts note text,
      context_type='telemetry_entry', and adds a structural edge to parent tracker definition.
    - schedule_items: if category is exam/lab/project/goal/milestone,
      context_type='project_goal'.
    - Skips re-embedding if existing content_hash matches.
    - Idempotently upserts into semantic_contexts.
    - Isolated with try/except: logs failures without raising exceptions.
    """
    try:
        context_type: str = ""
        subject: Optional[str] = None
        canonical_text: str = ""
        metadata: Dict[str, Any] = {}
        log_entity: Optional[TelemetryLog] = None

        if source_table == "tracker_definitions":
            tracker = (
                db.query(TrackerDefinition)
                .filter(
                    TrackerDefinition.id == source_id,
                    TrackerDefinition.user_id == user_id,
                )
                .first()
            )
            if not tracker:
                logger.warning("TrackerDefinition %s not found for user %s", source_id, user_id)
                return None

            stats = compute_tracker_stats(db, user_id, tracker)
            unit_val = tracker.unit or tracker.data_type or "unit"
            canonical_text = f"Habit tracker: {tracker.name}, {tracker.category} in {unit_val}. Rolling summary: {stats}."
            context_type = "life_habit"
            subject = tracker.name.replace("_", " ").title()
            metadata = {
                "tracker_name": tracker.name,
                "category": tracker.category,
                "data_type": tracker.data_type,
                "unit": tracker.unit,
                "stats": stats,
            }

        elif source_table == "telemetry_logs":
            log = (
                db.query(TelemetryLog)
                .filter(
                    TelemetryLog.id == source_id,
                    TelemetryLog.user_id == user_id,
                )
                .first()
            )
            if not log:
                logger.warning("TelemetryLog %s not found for user %s", source_id, user_id)
                return None

            log_meta = log.log_metadata if isinstance(log.log_metadata, dict) else {}
            category = log_meta.get("category") or log.entry_type
            if category != "journal_note":
                # Numeric/boolean telemetry logs do not generate new nodes
                return None

            log_entity = log
            note_text = ""
            if log_meta.get("value"):
                note_text = str(log_meta["value"]).strip()
            elif log.content:
                note_text = str(log.content).strip()

            canonical_text = f"Journal note: {note_text}"
            context_type = "telemetry_entry"
            tracker_name = log_meta.get("tracker_name", "Journal Note")
            subject = tracker_name.replace("_", " ").title()
            metadata = {
                "tracker_name": tracker_name,
                "entry_type": log.entry_type,
                "logged_date": log.logged_date.isoformat() if log.logged_date else None,
                "raw_note": note_text,
            }

        elif source_table == "schedule_items":
            item = (
                db.query(ScheduleItem)
                .filter(
                    ScheduleItem.id == source_id,
                    ScheduleItem.user_id == user_id,
                )
                .first()
            )
            if not item:
                logger.warning("ScheduleItem %s not found for user %s", source_id, user_id)
                return None

            cat_lower = (item.category or "").strip().lower()
            valid_categories = {"exam", "lab", "project", "goal", "milestone"}
            if cat_lower not in valid_categories:
                return None

            deadline_str = item.deadline.isoformat() if item.deadline else "None"
            canonical_text = (
                f"Project goal: {item.title}. Category: {item.category}. "
                f"Priority: {item.priority}. Duration: {item.duration_minutes}m. Deadline: {deadline_str}."
            )
            context_type = "project_goal"
            subject = item.title
            metadata = {
                "title": item.title,
                "category": item.category,
                "priority": item.priority,
                "duration_minutes": item.duration_minutes,
                "deadline": deadline_str,
                "is_fixed": item.is_fixed,
            }

        else:
            logger.warning("Unsupported source_table: %s", source_table)
            return None

        content_hash = compute_content_hash(canonical_text)

        # Query existing row for idempotent upsert
        existing = (
            db.query(SemanticContext)
            .filter(
                SemanticContext.user_id == user_id,
                SemanticContext.source_table == source_table,
                SemanticContext.source_id == source_id,
            )
            .first()
        )

        now = datetime.datetime.now(datetime.timezone.utc)
        sync_key = (str(user_id), str(source_id))

        if existing:
            # 1. Skip re-embedding if existing content_hash matches
            if existing.content_hash == content_hash:
                logger.debug(
                    "Content hash unchanged for %s/%s; skipping re-embedding.",
                    source_table,
                    source_id,
                )
                if log_entity:
                    _link_journal_note_to_tracker(db, user_id, log_entity, existing)
                return existing

            # 2. Debounce tracker re-embedding if updated within debounce window
            last_sync = _LAST_TRACKER_SYNC.get(sync_key)
            if (
                source_table == "tracker_definitions"
                and last_sync
                and (now - last_sync).total_seconds() < DEBOUNCE_SECONDS
                and existing.embedding
            ):
                existing.raw_content = canonical_text
                existing.content_hash = content_hash
                existing.context_metadata = metadata
                db.commit()
                db.refresh(existing)
                invalidate_knowledge_graph_cache(user_id)
                return existing

            # 3. Content changed: generate embedding and update
            _LAST_TRACKER_SYNC[sync_key] = now
            emb = get_embedding(canonical_text)
            existing.raw_content = canonical_text
            existing.content_hash = content_hash
            existing.context_type = context_type
            existing.subject = subject
            existing.context_metadata = metadata
            existing.embedding = emb
            db.commit()
            db.refresh(existing)
            invalidate_knowledge_graph_cache(user_id)
            if log_entity:
                _link_journal_note_to_tracker(db, user_id, log_entity, existing)
            return existing

        # 4. Insert new record
        _LAST_TRACKER_SYNC[sync_key] = now
        emb = get_embedding(canonical_text)
        new_ctx = SemanticContext(
            user_id=user_id,
            context_type=context_type,
            subject=subject,
            raw_content=canonical_text,
            embedding=emb,
            context_metadata=metadata,
            source_table=source_table,
            source_id=source_id,
            content_hash=content_hash,
        )
        db.add(new_ctx)
        db.commit()
        db.refresh(new_ctx)
        invalidate_knowledge_graph_cache(user_id)

        if log_entity:
            _link_journal_note_to_tracker(db, user_id, log_entity, new_ctx)

        return new_ctx

    except Exception as e:
        logger.error(
            "sync_node failed for user %s, source %s/%s: %s",
            user_id,
            source_table,
            source_id,
            e,
            exc_info=True,
        )
        try:
            db.rollback()
        except Exception:
            pass
        return None


def delete_synced_node(
    db: Session,
    user_id: UUID,
    source_table: str,
    source_id: UUID,
) -> bool:
    """
    Drops the corresponding semantic_contexts record and its associated context_edges
    when an application entity is deleted.
    """
    try:
        ctx = (
            db.query(SemanticContext)
            .filter(
                SemanticContext.user_id == user_id,
                SemanticContext.source_table == source_table,
                SemanticContext.source_id == source_id,
            )
            .first()
        )
        if not ctx:
            return False

        # Drop associated edges
        db.query(ContextEdge).filter(
            (ContextEdge.source_id == ctx.id) | (ContextEdge.target_id == ctx.id)
        ).delete(synchronize_session="fetch")

        # Drop semantic context record
        db.delete(ctx)
        db.commit()
        invalidate_knowledge_graph_cache(user_id)
        return True
    except Exception as e:
        logger.error(
            "delete_synced_node failed for user %s, source %s/%s: %s",
            user_id,
            source_table,
            source_id,
            e,
            exc_info=True,
        )
        try:
            db.rollback()
        except Exception:
            pass
        return False


# =====================================================================
# Deletion Cascade Event Listeners
# =====================================================================

@event.listens_for(ScheduleItem, "after_delete")
def _on_schedule_item_delete(mapper, connection, target):
    try:
        sel = select(SemanticContext.id).where(
            SemanticContext.source_table == "schedule_items",
            SemanticContext.source_id == target.id,
        )
        res = connection.execute(sel).fetchall()
        for row in res:
            ctx_id = row[0]
            connection.execute(
                delete(ContextEdge).where(
                    (ContextEdge.source_id == ctx_id) | (ContextEdge.target_id == ctx_id)
                )
            )
        connection.execute(
            delete(SemanticContext).where(
                SemanticContext.source_table == "schedule_items",
                SemanticContext.source_id == target.id,
            )
        )
        if hasattr(target, "user_id") and target.user_id:
            invalidate_knowledge_graph_cache(target.user_id)
    except Exception as e:
        logger.warning("Error in ScheduleItem after_delete event hook: %s", e)


@event.listens_for(TrackerDefinition, "after_delete")
def _on_tracker_definition_delete(mapper, connection, target):
    try:
        sel = select(SemanticContext.id).where(
            SemanticContext.source_table == "tracker_definitions",
            SemanticContext.source_id == target.id,
        )
        res = connection.execute(sel).fetchall()
        for row in res:
            ctx_id = row[0]
            connection.execute(
                delete(ContextEdge).where(
                    (ContextEdge.source_id == ctx_id) | (ContextEdge.target_id == ctx_id)
                )
            )
        connection.execute(
            delete(SemanticContext).where(
                SemanticContext.source_table == "tracker_definitions",
                SemanticContext.source_id == target.id,
            )
        )
        if hasattr(target, "user_id") and target.user_id:
            invalidate_knowledge_graph_cache(target.user_id)
    except Exception as e:
        logger.warning("Error in TrackerDefinition after_delete event hook: %s", e)


# =====================================================================
# Knowledge Graph Calculation & Exploration (Obsidian Web UI)
# =====================================================================

def compute_node_similarity(ctx_a: SemanticContext, emb_a: List[float], ctx_b: SemanticContext, emb_b: List[float]) -> float:
    """
    Computes semantic similarity between two context nodes.
    Combines embedding cosine similarity with keyword/subject overlap,
    ensuring robust behavior across both full pgvector embeddings and local fallback vectors.
    """
    cos_sim = cosine_similarity(emb_a, emb_b)

    # Keyword / token overlap
    import re
    tokens_a = set(re.findall(r"\w+", ctx_a.raw_content.lower()))
    tokens_b = set(re.findall(r"\w+", ctx_b.raw_content.lower()))
    common = tokens_a & tokens_b
    jaccard = len(common) / max(1, len(tokens_a | tokens_b))

    subject_match = 1.0 if (ctx_a.subject and ctx_b.subject and ctx_a.subject.lower() == ctx_b.subject.lower()) else 0.0

    # Blended similarity scaled to [0.0, 1.0]
    raw = (cos_sim * 0.6) + (jaccard * 0.25) + (subject_match * 0.15)
    # Scale up so related topics comfortably reach standard 0.70-0.90 similarity
    scaled = min(1.0, raw * 1.5)
    return round(max(cos_sim, scaled), 3)


def generate_node_label(item: SemanticContext) -> str:
    """Generates a concise ~6 word label from topic/metadata or content."""
    meta = item.context_metadata or {}
    if meta.get("topic"):
        return str(meta["topic"]).strip()
    if meta.get("module_title"):
        return str(meta["module_title"]).strip()
    if meta.get("title"):
        return str(meta["title"]).strip()
    if meta.get("tracker_name"):
        return str(meta["tracker_name"]).replace("_", " ").title().strip()
    if item.context_type == "syllabus_module" and meta.get("module_number"):
        subject_name = item.subject or "Module"
        words = item.raw_content.split()[:4]
        return f"{subject_name} M{meta.get('module_number')}: {' '.join(words)}"

    words = item.raw_content.strip().split()
    label = " ".join(words[:6])
    if len(words) > 6:
        label += "..."
    return label


def compute_knowledge_graph(
    db: Session,
    user_id: UUID,
    threshold: float = 0.75,
    top_k: int = 5,
    limit: int = 300,
) -> Dict[str, Any]:
    """
    Computes the server-side Knowledge Graph for semantic_contexts:
    1. Fetches all context nodes for this user.
    2. Computes pairwise cosine similarity between embeddings.
    3. Retains top-K neighbors per node exceeding the similarity threshold.
    4. Deduplicates bidirectional edges.
    5. Precomputes node degree server-side.
    6. Attaches canonical type information (group, glyph, style).
    """
    # 1. Fetch user contexts
    contexts: List[SemanticContext] = (
        db.query(SemanticContext)
        .filter(SemanticContext.user_id == user_id)
        .order_by(SemanticContext.created_at.desc())
        .limit(limit)
        .all()
    )

    if not contexts:
        return {
            "nodes": [],
            "edges": [],
            "total_nodes": 0,
            "total_edges": 0,
        }

    # Cache check based on latest row update/count
    latest_dt = max((c.created_at for c in contexts if c.created_at), default=datetime.datetime.min)
    cache_key = f"{user_id}:{threshold}:{top_k}:{limit}:{len(contexts)}:{latest_dt}"
    cached = _GRAPH_CACHE.get(cache_key)
    if cached is not None:
        return cached

    # Parse embeddings
    parsed_embeddings: List[Tuple[SemanticContext, List[float]]] = []
    for ctx in contexts:
        if ctx.embedding:
            try:
                emb = [float(x) for x in ctx.embedding]
                parsed_embeddings.append((ctx, emb))
            except Exception as e:
                logger.warning(f"Failed to parse embedding for context {ctx.id}: {e}")

    # Compute top-K neighbors per node
    # Candidate edge set: (min_id, max_id) -> weight
    edge_map: Dict[Tuple[str, str], float] = {}

    for i, (ctx_a, emb_a) in enumerate(parsed_embeddings):
        # Score against all other nodes
        neighbor_scores: List[Tuple[float, str]] = []
        for j, (ctx_b, emb_b) in enumerate(parsed_embeddings):
            if i == j:
                continue
            sim = compute_node_similarity(ctx_a, emb_a, ctx_b, emb_b)
            if sim >= threshold:
                neighbor_scores.append((sim, str(ctx_b.id)))

        # Retain top-K nearest neighbors
        neighbor_scores.sort(key=lambda x: x[0], reverse=True)
        top_neighbors = neighbor_scores[:top_k]

        id_a = str(ctx_a.id)
        for sim, id_b in top_neighbors:
            edge_key = (min(id_a, id_b), max(id_a, id_b))
            if edge_key not in edge_map or sim > edge_map[edge_key]:
                edge_map[edge_key] = round(sim, 3)

    # Also load persisted structural edges from context_edges
    persisted_edges = (
        db.query(ContextEdge)
        .filter(ContextEdge.user_id == user_id)
        .all()
    )
    for pedge in persisted_edges:
        s_id = str(pedge.source_id)
        t_id = str(pedge.target_id)
        edge_key = (min(s_id, t_id), max(s_id, t_id))
        if edge_key not in edge_map:
            edge_map[edge_key] = pedge.weight

    # Calculate degrees
    degree_map: Dict[str, int] = {str(ctx.id): 0 for ctx in contexts}
    edges_list: List[Dict[str, Any]] = []

    for (src, tgt), weight in edge_map.items():
        edges_list.append({
            "source": src,
            "target": tgt,
            "weight": weight,
        })
        if src in degree_map:
            degree_map[src] += 1
        if tgt in degree_map:
            degree_map[tgt] += 1

    # Format nodes with canonical metadata
    nodes_list: List[Dict[str, Any]] = []
    for ctx in contexts:
        cid = str(ctx.id)
        snippet = ctx.raw_content[:200].strip()
        if len(ctx.raw_content) > 200:
            snippet += "..."

        type_info = get_canonical_type_info(ctx.context_type)

        nodes_list.append({
            "id": cid,
            "label": generate_node_label(ctx),
            "type": ctx.context_type,
            "group": type_info["group"],
            "glyph": type_info["glyph"],
            "style": type_info["style"],
            "subject": ctx.subject,
            "snippet": snippet,
            "raw_content": ctx.raw_content,
            "degree": degree_map.get(cid, 0),
            "created_at": ctx.created_at.isoformat() if ctx.created_at else datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "source_table": ctx.source_table,
            "source_id": str(ctx.source_id) if ctx.source_id else None,
            "content_hash": ctx.content_hash,
        })

    result = {
        "nodes": nodes_list,
        "edges": edges_list,
        "total_nodes": len(nodes_list),
        "total_edges": len(edges_list),
    }

    _GRAPH_CACHE[cache_key] = result
    return result
