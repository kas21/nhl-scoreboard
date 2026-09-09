"""Shared helpers: word wrap and ISO timestamp parsing."""
from datetime import UTC, datetime, timedelta, timezone

from scoreboard.isotime import parse_iso
from scoreboard.nhl.boards.common import local_time
from scoreboard.render import load_font
from scoreboard.render.text import text_size, wrap_text


def test_wrap_text_is_greedy_and_measured_with_the_drawing_font():
    font = load_font("pixel", 6)
    lines = wrap_text("a severe thunderstorm capable of producing a tornado", font, 60)
    assert len(lines) > 1 and " ".join(lines) == "a severe thunderstorm capable of producing a tornado"
    assert all(text_size(line, font)[0] <= 60 for line in lines)
    assert wrap_text("supercalifragilistic", font, 10) == ["supercalifragilistic"]      # a word never splits
    assert wrap_text("one two three four five six", font, 30, max_lines=2) == wrap_text("one two three four five six", font, 30)[:2]
    assert wrap_text("", font, 30) == [] and wrap_text("   ", font, 30) == []


def test_parse_iso_accepts_z_and_naive_and_rejects_garbage():
    assert parse_iso("2026-09-08T16:45:00Z") == datetime(2026, 9, 8, 16, 45, tzinfo=UTC)
    assert parse_iso("2026-09-08T16:45:00.000Z") == datetime(2026, 9, 8, 16, 45, tzinfo=UTC)
    assert parse_iso("2026-09-08T12:45:00-04:00") == datetime(2026, 9, 8, 12, 45, tzinfo=timezone(timedelta(hours=-4)))
    assert parse_iso("2026-09-08T16:45:00") == datetime(2026, 9, 8, 16, 45, tzinfo=UTC)
    assert parse_iso(None) is None and parse_iso("") is None and parse_iso("garbage") is None
    assert local_time("2026-09-08T16:45:00Z", UTC) == datetime(2026, 9, 8, 16, 45, tzinfo=UTC)
    assert local_time("", UTC) is None and local_time("garbage", UTC) is None
