import datetime
from uuid import UUID
from typing import Optional, Literal
from pydantic import BaseModel, Field, ConfigDict


TrackerCategory = Literal["metric", "binary_habit", "volume", "journal_note", "metric_scale", "state"]
TrackerDataType = Literal["float", "boolean", "text", "integer", "string"]


class TrackerDefinitionBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., max_length=100)
    category: TrackerCategory
    data_type: TrackerDataType
    val_min: Optional[float] = None
    val_max: Optional[float] = None
    unit: Optional[str] = Field(None, max_length=30)


class TrackerDefinitionCreate(TrackerDefinitionBase):
    user_id: UUID


class TrackerDefinitionResponse(TrackerDefinitionBase):
    id: UUID
    user_id: UUID
    created_at: datetime.datetime
