import logging
from fastapi import APIRouter, File, UploadFile, HTTPException
from app.schemas.audio import AudioTranscriptionResponse
from app.services.whimpr_engine import dictate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Audio"])


@router.post("/transcribe", response_model=AudioTranscriptionResponse)
async def transcribe(file: UploadFile = File(...)):
    """
    Local-first STT endpoint: faster-whisper local ASR -> layout normalization ->
    Gemini text cleanup -> safety gates -> post-processing.
    Returns transcript and text for frontend compatibility, plus debug metadata.
    """
    try:
        audio_bytes = await file.read()
        if not audio_bytes:
            return AudioTranscriptionResponse(
                transcript="",
                text="",
                raw="",
                cleaned=False,
                fallback_reason="empty_audio",
            )

        res = await dictate(audio_bytes)
        final_text = res.get("text", "")
        return AudioTranscriptionResponse(
            transcript=final_text,
            text=final_text,
            raw=res.get("raw", ""),
            cleaned=res.get("cleaned", False),
            fallback_reason=res.get("fallback_reason"),
        )
    except Exception as e:
        logger.error(f"Audio transcription endpoint error: {e}")
        raise HTTPException(status_code=500, detail=f"Audio transcription error: {str(e)}")
