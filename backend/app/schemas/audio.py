from typing import Optional
from pydantic import BaseModel, Field


class AudioTranscriptionResponse(BaseModel):
    transcript: str = Field("", description="Cleaned or fallback transcription")
    text: str = Field("", description="Alias for transcript consumed by frontend")
    raw: str = Field("", description="Raw Whisper transcription before cleanup")
    cleaned: bool = Field(False, description="Whether LLM cleanup succeeded")
    fallback_reason: Optional[str] = Field(None, description="Reason for fallback if cleaned is False")
