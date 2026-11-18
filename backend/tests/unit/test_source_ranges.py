import pytest

from src.source_ranges import (
    SKIP_RANGES_CODE,
    SOURCE_RANGE_CODE,
    SourceRangeError,
    filter_segments,
    filter_transcript_text,
    format_timestamp,
    normalize_ranges,
    overlaps,
    parse_range,
    parse_skip_ranges,
    parse_source_range,
    parse_timestamp,
    ranges_fingerprint,
    ranges_from_json,
    ranges_to_json,
    source_range_from_json,
    to_local,
)


@pytest.mark.parametrize(
    "value, expected",
    [
        (754, 754.0),
        ("754", 754.0),
        ("12:34", 754.0),
        ("90:00", 5400.0),
        ("1:02:03", 3723.0),
        ("01:10:00", 4200.0),
        ("1h2m3s", 3723.0),
        ("1h 10m", 4200.0),
        ("45s", 45.0),
        ("10m", 600.0),
        (" 0:30 ", 30.0),
    ],
)
def test_parse_timestamp_accepts_common_forms(value, expected):
    assert parse_timestamp(value) == expected


@pytest.mark.parametrize("value", ["", "abc", "1:75", "1:61:00", "-5", "1:2:3:4", True, None, "12:3x", float("inf"), -1])
def test_parse_timestamp_rejects_bad_input(value):
    with pytest.raises(SourceRangeError):
        parse_timestamp(value)


@pytest.mark.parametrize(
    "value",
    ["12:30-18:45", "12:30 - 18:45", "12:30 to 18:45", "12:30–18:45", ["12:30", "18:45"], {"start": "12:30", "end": 1125}],
)
def test_parse_range_forms(value):
    assert parse_range(value) == (750.0, 1125.0)


def test_parse_range_hours_with_hyphen():
    assert parse_range("1:02:00-1:10:00") == (3720.0, 4200.0)
    assert parse_range("1h2m-1h10m") == (3720.0, 4200.0)


@pytest.mark.parametrize("value", ["12:30", "18:45-12:30", "10:00-10:00", "a-b", ["1:00"], {"start": "1:00"}, 5])
def test_parse_range_rejects_bad_input(value):
    with pytest.raises(SourceRangeError):
        parse_range(value)


def test_source_range_blank_means_whole_video():
    assert parse_source_range(None) is None
    assert parse_source_range("") is None
    assert parse_source_range({}) is None


def test_source_range_must_be_long_enough():
    assert parse_source_range("0:00-0:45") == (0.0, 45.0)
    with pytest.raises(SourceRangeError) as error:
        parse_source_range("0:00-0:10")
    assert error.value.code == SOURCE_RANGE_CODE


def test_skip_ranges_text_list_is_sorted_and_merged():
    parsed = parse_skip_ranges("1:02:00-1:10:00, 12:30-18:45\n18:00-20:00; 5:00-5:30")
    assert parsed == [(300.0, 330.0), (750.0, 1200.0), (3720.0, 4200.0)]


def test_skip_ranges_list_input():
    assert parse_skip_ranges([["1:00", "2:00"], {"start": 90, "end": 150}, "10:00-11:00"]) == [
        (60.0, 150.0),
        (600.0, 660.0),
    ]


def test_skip_ranges_blank_and_errors():
    assert parse_skip_ranges(None) == []
    assert parse_skip_ranges("  ") == []
    with pytest.raises(SourceRangeError) as error:
        parse_skip_ranges("12:30-oops")
    assert error.value.code == SKIP_RANGES_CODE
    with pytest.raises(SourceRangeError):
        parse_skip_ranges(["1:00-2:00"] * 201)
    with pytest.raises(SourceRangeError):
        parse_skip_ranges(42)


def test_normalize_merges_touching_spans():
    assert normalize_ranges([(10, 20), (20, 30), (50, 60), (25, 26)]) == [(10.0, 30.0), (50.0, 60.0)]


def test_to_local_clips_to_window_and_shifts():
    # Batch covers 1:10:10 - 2:20:00; skips given on the stream's own timeline.
    start, end = 4210.0, 8400.0
    skips = [(0.0, 100.0), (4000.0, 4300.0), (5000.0, 5400.0), (8300.0, 9000.0), (9500.0, 9600.0)]
    assert to_local(skips, start, end) == [(0.0, 90.0), (790.0, 1190.0), (4090.0, 4190.0)]


def test_overlaps_has_a_tolerance_for_grazing():
    skips = [(100.0, 200.0)]
    assert overlaps(150, 180, skips)
    assert overlaps(90, 110, skips)
    assert not overlaps(50, 100.3, skips)
    assert not overlaps(200.2, 260, skips)


def test_format_timestamp():
    assert format_timestamp(3723) == "1:02:03"
    assert format_timestamp(59.6) == "0:01:00"
    assert format_timestamp(-4) == "0:00:00"


def test_filter_transcript_text_drops_touching_lines_only():
    transcript = "\n".join(
        [
            "[00:00 - 00:10] hello there",
            "[00:10 - 00:20] Speaker A: waiting in queue",
            "[00:20 - 00:30] still waiting",
            "[00:30 - 00:40] and we are back",
            "not a transcript line",
        ]
    )
    filtered = filter_transcript_text(transcript, [(10.0, 30.0)])
    assert filtered.splitlines() == [
        "[00:00 - 00:10] hello there",
        "[00:30 - 00:40] and we are back",
        "not a transcript line",
    ]


def test_filter_transcript_text_handles_hour_labels_and_no_skips():
    transcript = "[1:10:00 - 1:10:12] boss fight\n[70:20 - 70:30] more"
    assert filter_transcript_text(transcript, []) == transcript
    assert filter_transcript_text(transcript, [(4200.0, 4212.0)]) == "[70:20 - 70:30] more"


def test_filter_segments_removes_overlapping_and_keeps_unreadable():
    segments = [
        {"start_time": "00:05", "end_time": "00:35"},
        {"start_time": "01:00", "end_time": "01:30"},
        {"start_time": "??", "end_time": "02:00"},
    ]
    kept = filter_segments(segments, [(0.0, 20.0)])
    assert [s["start_time"] for s in kept] == ["01:00", "??"]
    assert filter_segments(segments, []) == segments


def test_fingerprint_changes_with_spans():
    assert ranges_fingerprint([(1, 2)]) == ranges_fingerprint([(1.2, 2.4)])
    assert ranges_fingerprint([(1, 2)]) != ranges_fingerprint([(1, 3)])
    assert ranges_fingerprint([]) == ranges_fingerprint([])


def test_json_round_trip_ignores_garbage():
    spans = [(10.0, 20.0), (30.0, 40.0)]
    assert ranges_from_json(ranges_to_json(spans)) == spans
    assert ranges_from_json("nope") == []
    assert ranges_from_json([[1, 2], ["x", 3], [5], [9, 4], None]) == [(1.0, 2.0)]
    assert source_range_from_json([60, 4260]) == (60.0, 4260.0)
    assert source_range_from_json(None) is None
