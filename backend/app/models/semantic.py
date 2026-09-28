import datetime
import hashlib
from sqlalchemy import (
    Column,
    String,
    Text,
    DateTime,
    ForeignKey,
    Float,
    Boolean,
    CheckConstraint,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.base_types import (
    UniversalUUID,
    UniversalJSON,
    Vector1536,
    default_uuid,
)


def compute_content_hash(text: str) -> str:
    """Computes SHA-256 hex digest (64 chars) for content de-duplication."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class SemanticContext(Base):
    __tablename__ = "semantic_contexts"

    id = Column(UniversalUUID, primary_key=True, default=default_uuid)
    user_id = Column(
        UniversalUUID,
        ForeignKey("user_profiles.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    context_type = Column(String(30), nullable=False)  # 'syllabus_module' | 'episodic_constraint' | 'life_habit' | 'telemetry_entry' | 'project_goal'
    subject = Column(String(100), nullable=True)
    raw_content = Column(Text, nullable=False)
    embedding = Column(Vector1536, nullable=True)
    context_metadata = Column("metadata", UniversalJSON, default=dict)

    # Provenance columns per Design Decisions D1 & D2
    source_table = Column(String(40), nullable=True)  # 'schedule_items' | 'tracker_definitions' | 'telemetry_logs' | 'upload' | 'conversation'
    source_id = Column(UniversalUUID, nullable=True)
    content_hash = Column(String(64), nullable=True)  # CHAR(64) SHA-256 hex digest

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("user_id", "source_table", "source_id", name="uq_semantic_context_provenance"),
    )

    user = relationship("UserProfile", back_populates="semantic_contexts")
    outgoing_edges = relationship(
        "ContextEdge",
        foreign_keys="[ContextEdge.source_id]",
        back_populates="source",
        cascade="all, delete-orphan",
    )
    incoming_edges = relationship(
        "ContextEdge",
        foreign_keys="[ContextEdge.target_id]",
        back_populates="target",
        cascade="all, delete-orphan",
    )


class ContextEdge(Base):
    __tablename__ = "context_edges"

    id = Column(UniversalUUID, primary_key=True, default=default_uuid)
    user_id = Column(
        UniversalUUID,
        ForeignKey("user_profiles.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_id = Column(
        UniversalUUID,
        ForeignKey("semantic_contexts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_id = Column(
        UniversalUUID,
        ForeignKey("semantic_contexts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind = Column(String(20), nullable=False, default="semantic")  # 'semantic' | 'structural'
    weight = Column(Float, nullable=False, default=1.0)
    cross_domain = Column(Boolean, nullable=False, default=False)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )

    __table_args__ = (
        CheckConstraint("source_id < target_id", name="ck_canonical_edge_order"),
        UniqueConstraint("user_id", "source_id", "target_id", "kind", name="uq_context_edge"),
    )

    user = relationship("UserProfile", back_populates="context_edges")
    source = relationship("SemanticContext", foreign_keys=[source_id], back_populates="outgoing_edges")
    target = relationship("SemanticContext", foreign_keys=[target_id], back_populates="incoming_edges")
