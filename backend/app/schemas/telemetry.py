import datetime
from uuid import UUID
from typing import Optional, Any, Dict, Literal
from pydantic import BaseModel, Field, ConfigDict, AliasChoices


EntryType = Literal["task", "event", "note", "metric", "memory", "binary_habit", "volume", "journal_note"]


class TelemetryLogBase(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    entry_type: EntryType
    content: Optional[str] = None
    metadata_dict: Dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("log_metadata", "metadata"),
        serialization_alias="metadata",
    )
    logged_date: datetime.date = Field(default_factory=datetime.date.today)

    @property
    def metadata(self) -> Dict[str, Any]:
        return self.metadata_dict


class TelemetryLogCreate(TelemetryLogBase):
    user_id: UUID


class TelemetryLogResponse(TelemetryLogBase):
    id: UUID
    user_id: UUID
    logged_at: datetime.datetime
