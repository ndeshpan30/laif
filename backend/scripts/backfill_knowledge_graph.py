#!/usr/bin/env python3
"""
Backfill Migration Utility for Knowledge Graph Ingestion Engine
===============================================================
Iterates over all active users in `user_profiles` and executes `sync_node` across:
- All `tracker_definitions` (indexed as 'life_habit' with rolling summaries)
- All `telemetry_logs` where category == 'journal_note' (indexed as 'telemetry_entry'
  with structural edges linked to their parent tracker definitions)
- All qualifying `schedule_items` (exams, labs, projects, goals, milestones indexed as 'project_goal')

Strictly Idempotent:
Consecutive executions produce identical node and edge counts, skipping re-embedding
when SHA-256 content hashes match and preventing duplicate edge insertions.

Usage:
    python scripts/backfill_knowledge_graph.py [--user-id <UUID>] [--verbose]
"""

import os
import sys
import uuid
import logging
import argparse
from typing import Dict, Any, List, Optional
from collections import Counter
from sqlalchemy.orm import Session

# Ensure backend directory is in python search path
CURRENT_DIR = os.path.dirname(os.path.realpath(__file__))
BACKEND_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if not os.path.isdir(os.path.join(BACKEND_DIR, "app")):
    candidate = os.path.join(BACKEND_DIR, "backend")
    if os.path.isdir(os.path.join(candidate, "app")):
        BACKEND_DIR = candidate

if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.database import SessionLocal, init_db
from app.models.user import UserProfile
from app.models.tracker import TrackerDefinition
from app.models.telemetry import TelemetryLog
from app.models.schedule import ScheduleItem
from app.models.semantic import SemanticContext, ContextEdge
from app.services.knowledge_graph import (
    sync_node,
    get_canonical_group,
    invalidate_knowledge_graph_cache,
)

logger = logging.getLogger("backfill_knowledge_graph")

QUALIFYING_SCHEDULE_CATEGORIES = {"exam", "lab", "project", "goal", "milestone"}


def format_summary_report(summary: Dict[str, Any]) -> str:
    """Formats a clean, human-readable summary report for migration auditing."""
    lines = [
        "",
        "=" * 78,
        "             KNOWLEDGE GRAPH INGESTION BACKFILL SUMMARY REPORT",
        "=" * 78,
        f"Execution Status:        {summary['status'].upper()}",
        f"Active Users Processed:  {summary['users_processed']}",
        "",
        "--- Scanned Application Entities ---",
        f"  • Tracker Definitions:        {summary['scanned']['trackers']}",
        f"  • Journal Notes:              {summary['scanned']['journal_notes']}",
        f"  • Qualifying Schedule Items:  {summary['scanned']['schedule_items']}",
        f"  • Total Entities Scanned:     {summary['scanned']['total_scanned']}",
        "",
        f"--- Knowledge Graph Nodes ({summary['total_nodes']} Total) ---",
        "  By Canonical Group:",
    ]

    for group, count in sorted(summary["nodes_by_group"].items()):
        lines.append(f"    • {group:<24}: {count}")

    lines.append("")
    lines.append("  By Node Context Type:")
    for ctype, count in sorted(summary["nodes_by_type"].items()):
        lines.append(f"    • {ctype:<24}: {count}")

    lines.append("")
    lines.append(f"--- Knowledge Graph Edges ({summary['total_edges']} Total) ---")
    lines.append("  By Edge Kind:")
    for kind, count in sorted(summary["edges_by_kind"].items()):
        lines.append(f"    • {kind:<24}: {count}")

    lines.append("=" * 78)
    return "\n".join(lines)


def backfill_knowledge_graph(
    db: Optional[Session] = None,
    user_id: Optional[uuid.UUID] = None,
) -> Dict[str, Any]:
    """
    Backfills semantic_contexts and context_edges for all active users or a specified user.
    Strictly idempotent: re-running produces zero node/edge count inflation.
    """
    session_created = False
    if db is None:
        db = SessionLocal()
        session_created = True

    try:
        user_query = db.query(UserProfile)
        if user_id:
            user_query = user_query.filter(UserProfile.user_id == user_id)
        users = user_query.order_by(UserProfile.created_at.asc()).all()

        total_trackers_scanned = 0
        total_notes_scanned = 0
        total_items_scanned = 0
        nodes_synced_count = 0

        logger.info("Starting Knowledge Graph backfill for %d user(s)...", len(users))

        for user in users:
            uid = user.user_id
            logger.debug("Processing user %s (%s)...", uid, user.email)

            # 1. Sync all tracker_definitions
            trackers = (
                db.query(TrackerDefinition)
                .filter(TrackerDefinition.user_id == uid)
                .order_by(TrackerDefinition.created_at.asc())
                .all()
            )
            for tracker in trackers:
                total_trackers_scanned += 1
                ctx = sync_node(db, uid, "tracker_definitions", tracker.id)
                if ctx:
                    nodes_synced_count += 1

            # 2. Sync all telemetry_logs where category == 'journal_note'
            logs = (
                db.query(TelemetryLog)
                .filter(TelemetryLog.user_id == uid)
                .order_by(TelemetryLog.logged_at.asc())
                .all()
            )
            for log in logs:
                meta = log.log_metadata if isinstance(log.log_metadata, dict) else {}
                category = meta.get("category") or log.entry_type
                if category == "journal_note":
                    total_notes_scanned += 1
                    ctx = sync_node(db, uid, "telemetry_logs", log.id)
                    if ctx:
                        nodes_synced_count += 1

            # 3. Sync all qualifying schedule_items (exams, projects, goals, labs, milestones)
            schedule_items = (
                db.query(ScheduleItem)
                .filter(ScheduleItem.user_id == uid)
                .order_by(ScheduleItem.created_at.asc())
                .all()
            )
            for item in schedule_items:
                cat = (item.category or "").strip().lower()
                if cat in QUALIFYING_SCHEDULE_CATEGORIES:
                    total_items_scanned += 1
                    ctx = sync_node(db, uid, "schedule_items", item.id)
                    if ctx:
                        nodes_synced_count += 1

            # Invalidate any cached graph projections for this user
            invalidate_knowledge_graph_cache(uid)

        # Aggregate total graph counts and breakdowns across processed users
        user_ids = [u.user_id for u in users]
        if user_ids:
            all_nodes = (
                db.query(SemanticContext)
                .filter(SemanticContext.user_id.in_(user_ids))
                .all()
            )
            all_edges = (
                db.query(ContextEdge)
                .filter(ContextEdge.user_id.in_(user_ids))
                .all()
            )
        else:
            all_nodes = []
            all_edges = []

        group_counts = Counter(get_canonical_group(n.context_type) for n in all_nodes)
        type_counts = Counter(n.context_type for n in all_nodes)
        edge_counts = Counter(e.kind for e in all_edges)

        summary = {
            "status": "success",
            "users_processed": len(users),
            "total_nodes": len(all_nodes),
            "total_edges": len(all_edges),
            "nodes_synced": nodes_synced_count,
            "nodes_by_group": dict(group_counts),
            "nodes_by_type": dict(type_counts),
            "edges_by_kind": dict(edge_counts),
            "scanned": {
                "trackers": total_trackers_scanned,
                "journal_notes": total_notes_scanned,
                "schedule_items": total_items_scanned,
                "total_scanned": total_trackers_scanned + total_notes_scanned + total_items_scanned,
            },
        }

        report = format_summary_report(summary)
        logger.info("%s", report)

        return summary

    finally:
        if session_created:
            db.close()


def main():
    parser = argparse.ArgumentParser(
        description="Backfill Migration Utility for Knowledge Graph Ingestion"
    )
    parser.add_argument(
        "--user-id",
        type=str,
        default=None,
        help="Optional UUID of a specific user to backfill",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable detailed DEBUG logging",
    )
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    init_db()
    target_user = uuid.UUID(args.user_id) if args.user_id else None
    summary = backfill_knowledge_graph(user_id=target_user)
    print(format_summary_report(summary))


if __name__ == "__main__":
    main()
