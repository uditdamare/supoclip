import pytest
from fastapi import HTTPException

from src.api.routes import tasks


def test_range_is_clamped_to_the_video_length():
    assert tasks._range_in_source(4322.0, None) is None
    assert tasks._range_in_source(4322.0, (1800.0, 1980.0)) == (1800.0, 1980.0)
    # A range that runs past the end is cut at the end.
    assert tasks._range_in_source(4322.0, (4000.0, 9000.0)) == (4000.0, 4322.0)


def test_range_starting_after_the_end_is_rejected():
    with pytest.raises(HTTPException) as error:
        tasks._range_in_source(4322.0, (5.0 * 3600, 5.2 * 3600))
    assert error.value.status_code == 422
    assert error.value.detail["code"] == "INVALID_SOURCE_RANGE"
    assert "only 1:12:02 long" in error.value.detail["message"]


def test_too_long_error_for_a_range_points_at_the_range():
    error = tasks._too_long_error(7200.0, 5400, "video", (0.0, 7200.0))
    assert error.status_code == 422
    assert error.detail["code"] == "VIDEO_TOO_LONG"
    assert error.detail["max_duration_seconds"] == 5400
    assert "time range" in error.detail["message"]


def test_too_long_error_without_a_range_offers_a_range():
    error = tasks._too_long_error(36000.0, 5400, "Twitch video", None)
    assert error.detail["code"] == "VIDEO_TOO_LONG"
    assert "pick a time range" in error.detail["message"]
    assert error.detail["duration_seconds"] == 36000.0
