import datetime
import difflib
import logging
from typing import Dict, Any, List, Tuple, Optional
from uuid import UUID
from sqlalchemy.orm import Session

from app.models.tracker import TrackerDefinition
from app.models.telemetry import TelemetryLog
from app.schemas.extraction import TelemetryDataPoint, UniversalExtraction

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.85


def infer_data_type(category: str, value: Any) -> str:
    """
    UNIVERSAL_TRACKING_SPEC.md Section 4.2 Data-type inference rules.
    Dededuces storage data_type from category and value.
    """
    if category == "binary_habit":
        return "boolean"
    if category == "journal_note":
        return "text"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "float"
    return "text"


def find_existing_tracker(
    db: Session,
    user_id: UUID,
    entity: str,
    active_trackers: Optional[List[TrackerDefinition]] = None,
) -> Optional[TrackerDefinition]:
    """
    UNIVERSAL_TRACKING_SPEC.md Section 4.4 Entity de-duplication safeguard:
    1. Exact match on normalized entity name.
    2. High-similarity fuzzy match (>= 0.85) against existing user trackers.
    """
    normalized = entity.strip().lower().replace(" ", "_").replace("-", "_")

    # 1. Exact match in DB
    exact = db.query(TrackerDefinition).filter(
        TrackerDefinition.user_id == user_id,
        TrackerDefinition.name == normalized,
    ).first()
    if exact:
        return exact

    # 2. Query all user trackers if not provided
    trackers = active_trackers
    if trackers is None:
        trackers = db.query(TrackerDefinition).filter(TrackerDefinition.user_id == user_id).all()

    if not trackers:
        return None

    # Check for near matches / plurals
    best_match = None
    best_ratio = 0.0

    for trk in trackers:
        # Singular/plural match e.g. pushup vs pushups
        if normalized.rstrip("s") == trk.name.rstrip("s"):
            return trk

        ratio = difflib.SequenceMatcher(None, normalized, trk.name).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = trk

    if best_ratio >= SIMILARITY_THRESHOLD and best_match:
        logger.info(f"Fuzzy-matched entity '{normalized}' to existing tracker '{best_match.name}' (ratio={best_ratio:.2f})")
        return best_match

    return None


def get_or_create_tracker(
    db: Session,
    user_id: UUID,
    dp: TelemetryDataPoint,
    active_trackers: Optional[List[TrackerDefinition]] = None,
) -> Tuple[TrackerDefinition, bool]:
    """
    UNIVERSAL_TRACKING_SPEC.md Section 4.1 & 4.2:
    JIT Auto-Registration: queries tracker_definitions, infers type if not found,
    and returns (tracker, is_new).
    """
    normalized = dp.entity.strip().lower().replace(" ", "_").replace("-", "_")
    existing = find_existing_tracker(db, user_id, normalized, active_trackers)
    if existing:
        return existing, False

    inferred_type = infer_data_type(dp.category, dp.value)

    val_min = None
    val_max = None
    if dp.unit and "/10" in dp.unit:
        val_min = 0.0
        val_max = 10.0

    new_tracker = TrackerDefinition(
        user_id=user_id,
        name=normalized,
        category=dp.category,
        data_type=inferred_type,
        unit=dp.unit,
        val_min=val_min,
        val_max=val_max,
    )
    db.add(new_tracker)
    db.flush()  # Flush within transaction to populate generated ID
    return new_tracker, True


def write_telemetry_log(
    db: Session,
    user_id: UUID,
    raw_message: str,
    dp: TelemetryDataPoint,
    tracker: TrackerDefinition,
) -> TelemetryLog:
    """
    UNIVERSAL_TRACKING_SPEC.md Section 4.3:
    Inserts a row into telemetry_logs with raw value stored in metadata JSONB.
    """
    log = TelemetryLog(
        user_id=user_id,
        entry_type=dp.category,
        content=raw_message,
        log_metadata={
            "tracker_name": tracker.name,
            "value": dp.value,
            "unit": dp.unit,
            "category": dp.category,
        },
        logged_date=datetime.date.today(),
    )
    db.add(log)
    return log


def format_telemetry_confirmation(
    logged_results: List[Dict[str, Any]],
    note_count: int = 0,
) -> str:
    """
    UNIVERSAL_TRACKING_SPEC.md Section 5:
    Dynamic Conversational Feedback Synthesizer.
    Converts extracted points into user-facing confirmation sentence:
    - Lists entities in extraction order
    - Entity Display Name (value unit)
    - Boolean false renders as 'Entity: skipped'
    - Boolean true renders as 'Entity ✓'
    - Journal notes counted ('...and 1 note.')
    - First-time JIT registered trackers get '(started tracking this)'
    """
    clauses: List[str] = []

    for item in logged_results:
        tracker: TrackerDefinition = item["tracker"]
        dp: TelemetryDataPoint = item["data_point"]
        is_new: bool = item["is_new"]

        display_name = tracker.name.replace("_", " ").title()

        if dp.category == "binary_habit":
            val_bool = bool(dp.value) if not isinstance(dp.value, str) else dp.value.lower() not in ("false", "0", "skipped", "none")
            if not val_bool:
                clause = f"{display_name}: skipped"
            else:
                clause = f"{display_name} ✓"
        else:
            val_display = int(dp.value) if isinstance(dp.value, float) and dp.value.is_integer() else dp.value
            if dp.unit:
                unit_clean = dp.unit.strip()
                if unit_clean.startswith("/"):
                    clause = f"{display_name} ({val_display}{unit_clean})"
                else:
                    clause = f"{display_name} ({val_display} {unit_clean})"
            else:
                clause = f"{display_name} ({val_display})"

        if is_new:
            clause += " (started tracking this)"

        clauses.append(clause)

    note_str = ""
    if note_count == 1:
        note_str = "1 note"
    elif note_count > 1:
        note_str = f"{note_count} notes"

    if clauses and note_count > 0:
        return f"Logged: {', '.join(clauses)}, and {note_str}."
    elif clauses and note_count == 0:
        return f"Logged: {', '.join(clauses)}."
    elif not clauses and note_count > 0:
        return f"Logged: {note_str}."
    else:
        return "Logged activity."


def process_universal_telemetry(
    db: Session,
    user_id: UUID,
    raw_message: str,
    extraction: UniversalExtraction,
) -> Dict[str, Any]:
    """
    Deterministic backend flow (Section 4):
    Processes all TelemetryDataPoints atomically within a single transaction.
    """
    if not extraction.data_points:
        return {
            "confirmation": "No loggable data points found.",
            "logged_count": 0,
            "trackers": [],
        }

    active_trackers = db.query(TrackerDefinition).filter(TrackerDefinition.user_id == user_id).all()
    logged_results = []
    journal_note_logs: List[Tuple[TrackerDefinition, TelemetryLog]] = []
    note_count = 0

    try:
        for dp in extraction.data_points:
            if dp.category == "journal_note":
                note_count += 1
                tracker, is_new = get_or_create_tracker(db, user_id, dp, active_trackers)
                if is_new:
                    active_trackers.append(tracker)
                log = write_telemetry_log(db, user_id, raw_message, dp, tracker)
                journal_note_logs.append((tracker, log))
            else:
                tracker, is_new = get_or_create_tracker(db, user_id, dp, active_trackers)
                if is_new:
                    active_trackers.append(tracker)
                log = write_telemetry_log(db, user_id, raw_message, dp, tracker)
                logged_results.append({
                    "tracker": tracker,
                    "data_point": dp,
                    "is_new": is_new,
                })

        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to atomically process universal telemetry: {e}")
        raise

    # Post-commit Knowledge Graph synchronization (D1, D3, D6)
    try:
        from app.services.knowledge_graph import sync_node
        synced_tracker_ids = set()

        # 1. Sync JIT registered trackers or trackers updated with numeric/boolean logs (rolling summary)
        for res in logged_results:
            trk = res["tracker"]
            if trk.id not in synced_tracker_ids:
                sync_node(db, user_id, "tracker_definitions", trk.id)
                synced_tracker_ids.add(trk.id)

        # 2. Sync journal note logs (creates telemetry_entry node & structural edge to tracker)
        for j_tracker, j_log in journal_note_logs:
            if j_tracker.id not in synced_tracker_ids:
                sync_node(db, user_id, "tracker_definitions", j_tracker.id)
                synced_tracker_ids.add(j_tracker.id)
            sync_node(db, user_id, "telemetry_logs", j_log.id)
    except Exception as e:
        logger.warning(f"Error during post-commit knowledge graph synchronization: {e}")

    confirmation = format_telemetry_confirmation(logged_results, note_count=note_count)

    return {
        "confirmation": confirmation,
        "logged_count": len(logged_results) + note_count,
        "trackers": [r["tracker"].name for r in logged_results],
        "is_new_trackers": [r["tracker"].name for r in logged_results if r["is_new"]],
    }
