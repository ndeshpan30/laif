from typing import List, Literal, Optional, Dict, Any
from pydantic import BaseModel, Field


class TopicAnswer(BaseModel):
    topic_id: str = Field(..., description="Matches exact Question Bank ID (e.g., 'A1', 'B1', 'K1')")
    status: Literal["answered", "partial", "declined", "not_applicable"]
    facts: List[str] = Field(default_factory=list, description="Extracted atomic factual statements")


class ExtractedEntity(BaseModel):
    target_store: Literal["user_profiles", "schedule_items", "tracker_definitions", "semantic_contexts"]
    payload: Dict[str, Any] = Field(..., description="Normalized attributes ready for direct write-through")


class OnboardingExtraction(BaseModel):
    distress_signal: bool = Field(False, description="True if input signals severe mental crisis or hopelessness")
    topics: List[TopicAnswer] = Field(default_factory=list)
    entities: List[ExtractedEntity] = Field(default_factory=list)
