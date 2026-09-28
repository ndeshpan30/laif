import asyncio
import io
from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import Base, engine
import app.services.gemini_client as gemini_client
import app.services.whimpr_engine as whimpr_engine
from app.services.whimpr_engine import (
    pre_normalize_layout,
    evaluate_gates,
    post_process_cleaned,
)


@pytest.fixture
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


# ===========================================================================
# 1. Pure Unit Tests: Layout Normalization & Noun-Phrase Guards
# ===========================================================================

def test_pre_normalize_layout_cues():
    """
    Spoken cues convert to sentinels with longest phrase winning.
    """
    assert pre_normalize_layout("line one new line line two") == "line one  [[NL]]  line two"
    assert pre_normalize_layout("para one new paragraph para two") == "para one  [[NP]]  para two"
    assert pre_normalize_layout("first line line break second line") == "first line  [[NL]]  second line"
    assert pre_normalize_layout("first point next line second point") == "first point  [[NL]]  second point"
    assert pre_normalize_layout("please start a new paragraph now") == "please  [[NP]]  now"


def test_pre_normalize_noun_phrase_guards():
    """
    Noun phrases discussing line breaks/paragraphs are untouched.
    """
    assert pre_normalize_layout("the next line of code") == "the next line of code"
    assert pre_normalize_layout("a line break down of the costs") == "a line break down of the costs"
    assert pre_normalize_layout("the new line item on the invoice") == "the new line item on the invoice"
    assert pre_normalize_layout("the next line break here") == "the next line break here"


# ===========================================================================
# 2. Pure Unit Tests: Post-Processing & Sentinels
# ===========================================================================

def test_post_process_sentinels_and_cues():
    """
    Restores near-miss sentinels and converts [[NP]] and [[NL]] to real newlines.
    """
    assert post_process_cleaned("line one [[ nl ]] line two") == "line one\nline two"
    assert post_process_cleaned("para one [[ np ]] para two") == "para one\n\npara two"
    assert post_process_cleaned("line one [[NL]] line two") == "line one\nline two"
    assert post_process_cleaned("para one [[NP]] para two") == "para one\n\npara two"
    # Leftover spoken cues converted to real breaks on fallback
    assert post_process_cleaned("line one new line line two") == "line one\nline two"
    assert post_process_cleaned("para one new paragraph para two") == "para one\n\npara two"
    # Guarded phrases stay intact
    assert post_process_cleaned("the next line of code") == "the next line of code"
    # Consecutive newline runs capped to 2
    assert post_process_cleaned("para one  \n\n\n\n  para two") == "para one\n\npara two"


def test_post_process_code_fence_stripped():
    """
    A ``` -wrapped Gemini response is stripped.
    """
    raw = "```markdown\nThis is cleaned text without fences\n```"
    assert post_process_cleaned(raw) == "This is cleaned text without fences"

    raw2 = "```\nSimple code block\n```"
    assert post_process_cleaned(raw2) == "Simple code block"


# ===========================================================================
# 3. Pure Unit Tests: Safety Gate Evaluation
# ===========================================================================

def test_evaluate_gates_pass_short_numbers():
    """
    Legitimate short numbers (1-3 digits) are not protected as lost entities.
    """
    passed, reason = evaluate_gates("meet at 2 actually 3", "meet at 3")
    assert passed is True
    assert reason is None


def test_evaluate_gates_banned_prefix():
    """
    Rejects assistant-style preamble/banned prefixes introduced by LLM.
    """
    passed, reason = evaluate_gates("meeting at 3", "Here is your text: meeting at 3")
    assert passed is False
    assert reason == "banned_prefix:here is"

    passed, reason = evaluate_gates("schedule review", "Sure! I can help you: schedule review")
    assert passed is False
    assert reason == "banned_prefix:sure!"


def test_evaluate_gates_lost_entity():
    """
    Fails when a 4+ digit number or URL/email is dropped from the transcript.
    """
    # 5-digit number dropped
    passed, reason = evaluate_gates("my tracking id is 54321 please verify", "my tracking id is please verify")
    assert passed is False
    assert reason == "lost_entity:54321"

    # URL dropped
    passed, reason = evaluate_gates("check https://example.com for details", "check for details")
    assert passed is False
    assert reason == "lost_entity:https://example.com"


def test_evaluate_gates_over_deletion():
    """
    Fails when output is shrunk by more than 55%.
    """
    raw = "this is a very long sentence containing a substantial amount of information that gets cut down"
    cleaned = "cut down"
    passed, reason = evaluate_gates(raw, cleaned)
    assert passed is False
    assert "over_deletion" in reason


def test_evaluate_gates_growth():
    """
    Fails when output grows by more than 60% (> 1.6x raw length).
    """
    raw = "short note"
    cleaned = "this is an excessively bloated and expanded version of the short note that adds way too many words"
    passed, reason = evaluate_gates(raw, cleaned)
    assert passed is False
    assert "growth" in reason


def test_evaluate_gates_novelty():
    """
    Fails when novel token fraction exceeds 0.34 (hallucinatory paraphrase).
    """
    raw = "the cat sat on the mat today"
    cleaned = "a dog ran in a fog every"
    passed, reason = evaluate_gates(raw, cleaned)
    assert passed is False
    assert "novelty" in reason


# ===========================================================================
# 4. Endpoint Integration Tests with Mocks
# ===========================================================================

def test_endpoint_transcribe_success(client, monkeypatch):
    """
    POST /api/transcribe returns cleaned text when Whisper produces a messy transcript
    and Gemini produces a clean one.
    """
    messy_whisper = "um so i think we should uh meet at 2 actually 3 period"
    clean_gemini = "So I think we should meet at 3."

    monkeypatch.setattr(
        "app.services.whimpr_engine.transcribe_local_whisper",
        lambda audio_bytes: messy_whisper,
    )

    mock_response = MagicMock()
    mock_response.text = clean_gemini
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    monkeypatch.setattr(gemini_client, "client", mock_client)
    monkeypatch.setattr(gemini_client, "get_client", lambda: mock_client)

    fake_audio = b"\x1a\x45\xdf\xa3" + b"x" * 200
    files = {"file": ("recording.webm", io.BytesIO(fake_audio), "audio/webm")}
    response = client.post("/api/transcribe", files=files)

    assert response.status_code == 200
    data = response.json()
    assert data["transcript"] == clean_gemini
    assert data["text"] == clean_gemini
    assert data["raw"] == messy_whisper
    assert data["cleaned"] is True
    assert data["fallback_reason"] is None


def test_endpoint_silent_audio_skips_gemini(client, monkeypatch):
    """
    Silent or empty audio returns empty transcript without calling Gemini.
    """
    monkeypatch.setattr(
        "app.services.whimpr_engine.transcribe_local_whisper",
        lambda audio_bytes: "",
    )

    mock_client = MagicMock()
    monkeypatch.setattr(gemini_client, "client", mock_client)
    monkeypatch.setattr(gemini_client, "get_client", lambda: mock_client)

    # Empty payload
    res_empty = client.post("/api/transcribe", files={"file": ("empty.webm", io.BytesIO(b""), "audio/webm")})
    assert res_empty.status_code == 200
    assert res_empty.json()["transcript"] == ""
    assert res_empty.json()["cleaned"] is False
    assert not mock_client.models.generate_content.called

    # Silent audio payload
    res_silent = client.post("/api/transcribe", files={"file": ("silent.webm", io.BytesIO(b"silent" * 30), "audio/webm")})
    assert res_silent.status_code == 200
    assert res_silent.json()["transcript"] == ""
    assert res_silent.json()["cleaned"] is False
    assert not mock_client.models.generate_content.called


def test_endpoint_under_two_words_skips_gemini(client, monkeypatch):
    """
    Input under 2 words skips the LLM call entirely and returns raw text.
    """
    monkeypatch.setattr(
        "app.services.whimpr_engine.transcribe_local_whisper",
        lambda audio_bytes: "Hello.",
    )

    mock_client = MagicMock()
    monkeypatch.setattr(gemini_client, "client", mock_client)
    monkeypatch.setattr(gemini_client, "get_client", lambda: mock_client)

    fake_audio = b"dummy" * 30
    res = client.post("/api/transcribe", files={"file": ("speech.webm", io.BytesIO(fake_audio), "audio/webm")})

    assert res.status_code == 200
    data = res.json()
    assert data["transcript"] == "Hello."
    assert data["text"] == "Hello."
    assert data["raw"] == "Hello."
    assert data["cleaned"] is False
    assert data["fallback_reason"] is None
    assert not mock_client.models.generate_content.called


def test_endpoint_gemini_timeout_fallback(client, monkeypatch):
    """
    Gemini timeout falls back to raw text with fallback_reason='timeout' and HTTP 200.
    """
    raw_whisper = "line one new line line two"
    monkeypatch.setattr(
        "app.services.whimpr_engine.transcribe_local_whisper",
        lambda audio_bytes: raw_whisper,
    )

    def mock_timeout_generate(*args, **kwargs):
        import time
        time.sleep(5.0)  # triggers the 4.0s asyncio.wait_for timeout
        return MagicMock(text="Too late")

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = mock_timeout_generate
    monkeypatch.setattr(gemini_client, "client", mock_client)
    monkeypatch.setattr(gemini_client, "get_client", lambda: mock_client)

    fake_audio = b"dummy" * 30
    res = client.post("/api/transcribe", files={"file": ("speech.webm", io.BytesIO(fake_audio), "audio/webm")})

    assert res.status_code == 200
    data = res.json()
    assert data["cleaned"] is False
    assert data["fallback_reason"] == "timeout"
    assert data["raw"] == raw_whisper
    # Layout sentinels in raw text were converted during post_processing on fallback
    assert data["transcript"] == "line one\nline two"
    assert data["text"] == "line one\nline two"


def test_endpoint_gemini_exception_fallback(client, monkeypatch):
    """
    Gemini exception (503 / network) falls back to raw text with fallback_reason='api_error'.
    """
    raw_whisper = "planning study schedule for next week"
    monkeypatch.setattr(
        "app.services.whimpr_engine.transcribe_local_whisper",
        lambda audio_bytes: raw_whisper,
    )

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = RuntimeError("503 Model High Demand Spikes")
    monkeypatch.setattr(gemini_client, "client", mock_client)
    monkeypatch.setattr(gemini_client, "get_client", lambda: mock_client)

    fake_audio = b"dummy" * 30
    res = client.post("/api/transcribe", files={"file": ("speech.webm", io.BytesIO(fake_audio), "audio/webm")})

    assert res.status_code == 200
    data = res.json()
    assert data["cleaned"] is False
    assert data["fallback_reason"] == "api_error"
    assert data["transcript"] == raw_whisper
    assert data["text"] == raw_whisper


def test_endpoint_gemini_empty_response_fallback(client, monkeypatch):
    """
    Gemini returning empty string falls back to raw text with fallback_reason='empty'.
    """
    raw_whisper = "review operating systems notes"
    monkeypatch.setattr(
        "app.services.whimpr_engine.transcribe_local_whisper",
        lambda audio_bytes: raw_whisper,
    )

    mock_response = MagicMock()
    mock_response.text = ""
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response
    monkeypatch.setattr(gemini_client, "client", mock_client)
    monkeypatch.setattr(gemini_client, "get_client", lambda: mock_client)

    fake_audio = b"dummy" * 30
    res = client.post("/api/transcribe", files={"file": ("speech.webm", io.BytesIO(fake_audio), "audio/webm")})

    assert res.status_code == 200
    data = res.json()
    assert data["cleaned"] is False
    assert data["fallback_reason"] == "empty"
    assert data["transcript"] == raw_whisper


def test_endpoint_gate_failure_fallback(client, monkeypatch):
    """
    Gate failure (e.g. banned prefix) falls back to raw text with gate fallback reason.
    """
    raw_whisper = "schedule gym session at 6 pm"
    monkeypatch.setattr(
        "app.services.whimpr_engine.transcribe_local_whisper",
        lambda audio_bytes: raw_whisper,
    )

    mock_response = MagicMock()
    mock_response.text = "Here is the transcription: schedule gym session at 6 pm"
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response
    monkeypatch.setattr(gemini_client, "client", mock_client)
    monkeypatch.setattr(gemini_client, "get_client", lambda: mock_client)

    fake_audio = b"dummy" * 30
    res = client.post("/api/transcribe", files={"file": ("speech.webm", io.BytesIO(fake_audio), "audio/webm")})

    assert res.status_code == 200
    data = res.json()
    assert data["cleaned"] is False
    assert data["fallback_reason"] == "gate:banned_prefix:here is"
    assert data["transcript"] == raw_whisper


def test_endpoint_contract_transcript_and_text(client, monkeypatch):
    """
    Guarantees request/response contract always preserves transcript and text fields identically.
    """
    raw_whisper = "drink two liters of water"
    monkeypatch.setattr(
        "app.services.whimpr_engine.transcribe_local_whisper",
        lambda audio_bytes: raw_whisper,
    )

    mock_response = MagicMock()
    mock_response.text = "Drink 2 liters of water."
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response
    monkeypatch.setattr(gemini_client, "client", mock_client)
    monkeypatch.setattr(gemini_client, "get_client", lambda: mock_client)

    fake_audio = b"dummy" * 30
    res = client.post("/api/transcribe", files={"file": ("speech.webm", io.BytesIO(fake_audio), "audio/webm")})

    assert res.status_code == 200
    data = res.json()
    assert "transcript" in data
    assert "text" in data
    assert data["transcript"] == data["text"]
    assert data["transcript"] == "Drink 2 liters of water."
