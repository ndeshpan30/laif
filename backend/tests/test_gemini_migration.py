import pytest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.schemas.extraction import TelemetryDataPoint, UniversalExtraction
from app.services.gemini_client import (
    extract_telemetry,
    generate_grill_response,
)
from app.services.conversation import (
    synthesize_telemetry_confirmation,
    run_conversational_turn,
)
from app.database import Base, engine, SessionLocal


@pytest.fixture
def db_session():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_synthesize_telemetry_confirmation_examples():
    """
    Verifies that the response synthesizer safely iterates through data_points
    to construct confirmation strings like 'Logged: Water Intake (2 liters), Stress (3)'.
    """
    points = [
        TelemetryDataPoint(entity="water_intake", value=2, unit="liters", category="volume"),
        TelemetryDataPoint(entity="stress", value=3, unit=None, category="metric"),
    ]
    conf = synthesize_telemetry_confirmation(points)
    assert conf == "Logged: Water Intake (2 liters), Stress (3)."

    # Test with habits and notes
    points2 = [
        TelemetryDataPoint(entity="morning_run", value=True, unit=None, category="binary_habit"),
        TelemetryDataPoint(entity="dessert", value=False, unit=None, category="binary_habit"),
        TelemetryDataPoint(entity="vent", value="tough day", unit=None, category="journal_note"),
    ]
    conf2 = synthesize_telemetry_confirmation(points2)
    assert "Morning Run ✓" in conf2
    assert "Dessert: skipped" in conf2
    assert "1 note" in conf2


def test_extract_telemetry_with_gemini_structured_output():
    """
    Verifies extract_telemetry when Gemini API returns valid JSON structured output via google-genai.
    """
    mock_json = """{
        "is_telemetry": true,
        "data_points": [
            {"entity": "water_intake", "value": 2, "unit": "liters", "category": "volume"},
            {"entity": "stress", "value": 3, "unit": null, "category": "metric"}
        ]
    }"""

    mock_response = MagicMock()
    mock_response.text = mock_json

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch("app.services.gemini_client.get_client", return_value=mock_client):
        res = extract_telemetry("Drank 2 liters of water and stress is 3")

        assert res.is_telemetry is True
        assert len(res.data_points) == 2
        assert res.data_points[0].entity == "water_intake"
        assert res.data_points[0].value == 2
        assert res.data_points[0].unit == "liters"
        assert res.data_points[1].entity == "stress"
        assert res.data_points[1].value == 3
        assert res.data_points[1].unit is None


def test_extract_telemetry_fallback_no_unit_conversion():
    """
    Verifies that the deterministic parser extracts without unit conversions.
    """
    with patch("app.services.gemini_client.get_client", return_value=None):
        res = extract_telemetry("Drank 2 liters of water, stress 3")
        assert res.is_telemetry is True
        entities = [dp.entity for dp in res.data_points]
        assert "water_intake" in entities
        water_dp = next(dp for dp in res.data_points if dp.entity == "water_intake")
        assert water_dp.value == 2
        assert water_dp.unit == "liters"


def test_generate_grill_response_gemini():
    """
    Verifies generate_grill_response standard text generation with Gemini via google-genai.
    """
    mock_response = MagicMock()
    mock_response.text = "[GRILL MODE] What exact chapter will you finish and what is your deadline?"

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch("app.services.gemini_client.get_client", return_value=mock_client):
        reply = generate_grill_response(
            context=["No screen work past 10 PM"],
            user_input="I want to study networks"
        )
        assert "[GRILL MODE]" in reply


def test_conversation_routing_grill_mode_toggle(db_session):
    """
    Verifies that run_conversational_turn routes:
    - to generate_grill_response when grill_mode is True
    - to extract_telemetry when grill_mode is False
    """
    user_id = uuid4()

    # Grill Mode True -> routes to generate_grill_response
    with patch("app.services.conversation.generate_grill_response") as mock_grill:
        mock_grill.return_value = "[GRILL MODE] What exact measurable deliverables prove you will complete this?"

        result_grill = run_conversational_turn(
            db=db_session,
            user_id=user_id,
            user_message="I want to study Networks",
            grill_mode=True,
        )

        assert mock_grill.called
        assert "[GRILL MODE]" in result_grill["reply"]
        assert result_grill["audio_signal"] == "NONE"
        assert result_grill["universal_extraction"] is None

    # Grill Mode False -> routes to extract_telemetry
    with patch("app.services.conversation.extract_telemetry") as mock_extract:
        mock_extract.return_value = UniversalExtraction(
            is_telemetry=True,
            data_points=[
                TelemetryDataPoint(entity="water_intake", value=2, unit="liters", category="volume"),
                TelemetryDataPoint(entity="stress", value=3, unit=None, category="metric"),
            ]
        )

        result_telemetry = run_conversational_turn(
            db=db_session,
            user_id=user_id,
            user_message="Drank 2 liters of water and stress is 3",
            grill_mode=False,
        )

        assert mock_extract.called
        assert "Water Intake (2 liters)" in result_telemetry["reply"]
        assert "Stress (3)" in result_telemetry["reply"]
        assert result_telemetry["audio_signal"] == "SUCCESS_JINGLE"
        assert result_telemetry["universal_extraction"] is not None
