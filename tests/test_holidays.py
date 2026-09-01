import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image

from scoreboard.boards.base import BoardContext
from scoreboard.data import SnapshotStore
from scoreboard.extras.holidays.board import CountdownBoard, CountdownConfig
from scoreboard.extras.holidays.source import (
    IMAGES,
    CustomHoliday,
    HolidayOverride,
    HolidaysConfig,
    available,
    base_name,
    calendar_names,
    slug,
    upcoming,
)
from scoreboard.render.profiles import profile_for

XMAS = str(IMAGES / "christmas_day.png")


def named(items, name):
    return next(i for i in items if i["name"] == name)


# -- naming --------------------------------------------------------------------


def test_slug_keeps_apostrophe_words_whole():
    """An apostrophe used to split the word, so new_years_day.png was never found."""
    assert slug("New Year's Day") == "new_years_day"
    assert slug("Washington\u2019s Birthday") == "washingtons_birthday"   # a typed curly quote too
    assert slug("Saint Patrick's Day") == "saint_patricks_day"
    assert slug("Martin Luther King Jr. Day") == "martin_luther_king_jr_day"
    assert slug("F\u00eate du Canada") == "fete_du_canada"                # accents fold, not split


def test_base_name_drops_the_observed_suffix():
    assert base_name("Independence Day (observed)") == "Independence Day"
    assert base_name("Christmas Day") == "Christmas Day"


def test_every_bundled_image_is_reachable_from_a_real_holiday():
    """Guards both defects at once: a bad slug or a category we never ask for."""
    reachable = set()
    for country, subdiv in (("US", ""), ("US", "WV"), ("CA", "")):
        cfg = HolidaysConfig(country=country, subdivision=subdiv)
        for name in calendar_names(cfg, {2026}):
            reachable |= {slug(name), slug(base_name(name))}
    assert {p.stem for p in IMAGES.glob("*.png")} - reachable == set()


# -- upcoming ------------------------------------------------------------------


def test_upcoming_includes_public_and_custom_within_horizon():
    cfg = HolidaysConfig(country="US", horizon_days=40, custom=[CustomHoliday(name="Puck Drop", date="10-07"),
                                                                 CustomHoliday(name="Old", date="2020-01-01")])
    items = upcoming(cfg, date(2026, 12, 1))
    names = [i["name"] for i in items]
    assert "Christmas Day" in names
    assert [i["date"] for i in items] == sorted(i["date"] for i in items)
    xmas = named(items, "Christmas Day")
    assert xmas["days"] == 24 and xmas["image"] == XMAS
    assert "Puck Drop" not in names           # Oct 7 is outside a Dec 1 + 40 day window
    assert all(0 <= i["days"] <= 40 for i in items)
    assert "Old" not in names


def test_unofficial_holidays_are_included():
    """We ship art for Halloween and Groundhog Day; the public-only calendar never had them."""
    items = upcoming(HolidaysConfig(country="US", horizon_days=60), date(2026, 10, 1))
    assert Path(named(items, "Halloween")["image"]).name == "halloween.png"


def test_observed_variant_falls_back_to_the_base_image():
    items = upcoming(HolidaysConfig(country="US", horizon_days=10), date(2026, 7, 1))
    observed = named(items, "Independence Day (observed)")
    assert Path(observed["image"]).name == "independence_day.png"


def test_today_and_disabled_via_overrides():
    cfg = HolidaysConfig(country="US",
                         overrides={"Christmas Day": HolidayOverride(enabled=False)},
                         custom=[CustomHoliday(name="Game Day", date="12-25")])
    items = upcoming(cfg, date(2026, 12, 25))
    assert [i["name"] for i in items if i["days"] == 0] == ["Game Day"]


def test_disabled_custom_holiday_is_dropped():
    cfg = HolidaysConfig(country="US", custom=[CustomHoliday(name="Game Day", date="12-25", enabled=False)])
    assert "Game Day" not in [i["name"] for i in upcoming(cfg, date(2026, 12, 25))]


# -- overrides -----------------------------------------------------------------


def test_alternate_name_rides_alongside_the_canonical_one():
    """Renaming must not cost the holiday its picture, so the slug still comes from `name`."""
    cfg = HolidaysConfig(country="US", overrides={"Christmas Day": HolidayOverride(display="Xmas at the Rink")})
    xmas = named(upcoming(cfg, date(2026, 12, 1)), "Christmas Day")
    assert xmas["display"] == "Xmas at the Rink"
    assert xmas["image"] == XMAS


def test_display_defaults_to_the_real_name():
    xmas = named(upcoming(HolidaysConfig(country="US"), date(2026, 12, 1)), "Christmas Day")
    assert xmas["display"] == "Christmas Day"


def test_override_can_point_at_another_image():
    cfg = HolidaysConfig(country="US", overrides={"Christmas Day": HolidayOverride(image="halloween")})
    assert Path(named(upcoming(cfg, date(2026, 12, 1)), "Christmas Day")["image"]).name == "halloween.png"


def test_custom_holidays_can_share_one_image():
    cfg = HolidaysConfig(country="US", horizon_days=5, custom=[
        CustomHoliday(name="Lily's Birthday", date="12-02", image="christmas_day"),
        CustomHoliday(name="Ethan's Birthday", date="12-03", image="christmas_day")])
    items = upcoming(cfg, date(2026, 12, 1))
    assert [i["image"] for i in items if i["custom"]] == [XMAS, XMAS]


def test_a_custom_holiday_with_no_image_is_not_an_error():
    cfg = HolidaysConfig(country="US", horizon_days=5, custom=[CustomHoliday(name="Puck Drop", date="12-02")])
    assert named(upcoming(cfg, date(2026, 12, 1)), "Puck Drop")["image"] is None


def test_user_images_win_over_the_bundled_ones(tmp_path, monkeypatch):
    monkeypatch.setattr("scoreboard.extras.holidays.images.USER_IMAGES", tmp_path)
    Image.new("RGBA", (8, 8)).save(tmp_path / "christmas_day.png")
    xmas = named(upcoming(HolidaysConfig(country="US"), date(2026, 12, 1)), "Christmas Day")
    assert xmas["image"] == str(tmp_path / "christmas_day.png")


def test_an_unknown_country_yields_nothing_rather_than_raising():
    assert upcoming(HolidaysConfig(country="ZZ"), date(2026, 12, 1)) == []


# -- available -----------------------------------------------------------------


def test_available_lists_every_holiday_with_its_state():
    cfg = HolidaysConfig(country="US", overrides={"Christmas Day": HolidayOverride(enabled=False, display="Xmas")},
                         custom=[CustomHoliday(name="Puck Drop", date="10-07")])
    items = available(cfg, date(2026, 12, 1))
    by_name = {i["name"]: i for i in items}
    assert by_name["Christmas Day"] == {"name": "Christmas Day", "display": "Xmas", "enabled": False,
                                        "custom": False, "image": XMAS, "image_name": "christmas_day",
                                        "image_slug": "christmas_day", "uploaded": False}
    assert by_name["Halloween"]["enabled"] is True             # not in overrides -> on
    assert by_name["Puck Drop"]["custom"] is True              # custom dates are listed too
    assert len(by_name) == len(items)                          # one row per name, no year duplicates
    assert [i["display"] for i in items] == sorted(i["display"] for i in items)


def test_available_ignores_the_horizon():
    """The picker lists the whole year; the horizon only trims what the board counts down to."""
    cfg = HolidaysConfig(country="US", horizon_days=1)
    assert "Halloween" in {i["name"] for i in available(cfg, date(2026, 12, 1))}


# -- board ---------------------------------------------------------------------


def snapshot_with(**overrides):
    return SnapshotStore().publish("holidays.upcoming", [
        {"name": "Christmas Day", "display": "Christmas Day", "date": "2026-12-25", "days": 24,
         "image": XMAS, "custom": False, **overrides},
        {"name": "Game Day", "display": "Game Day", "date": "2026-12-01", "days": 0,
         "image": None, "custom": True},
    ])


def test_countdown_board_renders_and_cycles():
    snap = snapshot_with()
    now = datetime(2026, 12, 1, tzinfo=ZoneInfo("America/Toronto"))
    board, cfg = CountdownBoard(), CountdownConfig(seconds_per_holiday=3)
    for w, h in [(128, 64), (64, 32)]:
        ctx = BoardContext(snapshot=snap, profile=profile_for(w, h), width=w, height=h, fps=30, now=now, elapsed=1.0)
        first = board.render(ctx, cfg)
        assert first.size == (w, h) and first.getbbox() is not None
        second = board.render(BoardContext(**{**ctx.__dict__, "elapsed": 4.0}), cfg)
        assert first.tobytes() != second.tobytes()
        assert board.done(BoardContext(**{**ctx.__dict__, "elapsed": 6.1}), cfg)


def test_board_draws_the_alternate_name():
    now = datetime(2026, 12, 1, tzinfo=ZoneInfo("America/Toronto"))
    board, cfg = CountdownBoard(), CountdownConfig(seconds_per_holiday=3)
    ctx = BoardContext(snapshot=snapshot_with(), profile=profile_for(128, 64), width=128, height=64,
                       fps=30, now=now, elapsed=1.0)
    plain = board.render(ctx, cfg)
    board = CountdownBoard()
    renamed = BoardContext(**{**ctx.__dict__, "snapshot": snapshot_with(display="Xmas at the Rink")})
    assert board.render(renamed, cfg).tobytes() != plain.tobytes()


def test_board_survives_an_image_that_went_missing(tmp_path):
    """An uploaded picture can be deleted between the source publishing and the board drawing."""
    now = datetime(2026, 12, 1, tzinfo=ZoneInfo("America/Toronto"))
    board, cfg = CountdownBoard(), CountdownConfig()
    ctx = BoardContext(snapshot=snapshot_with(image=str(tmp_path / "gone.png")), profile=profile_for(128, 64),
                       width=128, height=64, fps=30, now=now, elapsed=1.0)
    assert board.render(ctx, cfg).size == (128, 64)


def test_slug_is_filename_safe():
    for name in ("../../etc/passwd", "Bob & Alice's Day!", "  "):
        assert re.fullmatch(r"[a-z0-9_]*", slug(name)), name


def test_available_says_where_an_upload_for_a_row_would_go(tmp_path, monkeypatch):
    monkeypatch.setattr("scoreboard.extras.holidays.images.USER_IMAGES", tmp_path)
    cfg = HolidaysConfig(country="US", overrides={"Halloween": HolidayOverride(image="pumpkins")},
                         custom=[CustomHoliday(name="Lily's Birthday", date="04-23", image="birthday")])
    by_name = {i["name"]: i for i in available(cfg, date(2026, 12, 1))}
    assert by_name["Christmas Day"]["image_slug"] == "christmas_day"      # named after the holiday
    assert by_name["Halloween"]["image_slug"] == "pumpkins"               # an override redirects it
    assert by_name["Lily's Birthday"]["image_slug"] == "birthday"         # so does a custom date
    assert all(not i["uploaded"] for i in by_name.values())

    Image.new("RGBA", (8, 8)).save(tmp_path / "christmas_day.png")
    refreshed = {i["name"]: i for i in available(cfg, date(2026, 12, 1))}
    assert refreshed["Christmas Day"]["uploaded"] is True                 # a delete would now do something
    assert refreshed["Halloween"]["uploaded"] is False


def test_observed_days_can_be_given_their_own_picture():
    """The row borrows independence_day.png but an upload lands under its own name.

    Conflating the two showed a broken thumbnail: the panel asked for a picture at the
    slug it would upload to, which is not the one on screen.
    """
    by_name = {i["name"]: i for i in available(HolidaysConfig(country="US"), date(2026, 7, 1))}
    observed = by_name["Independence Day (observed)"]
    assert Path(observed["image"]).name == "independence_day.png"
    assert observed["image_name"] == "independence_day"              # what it shows
    assert observed["image_slug"] == "independence_day_observed"     # where an upload goes


def test_a_row_with_no_picture_has_nothing_to_show():
    by_name = {i["name"]: i for i in available(HolidaysConfig(country="US"), date(2026, 12, 1))}
    assert by_name["Columbus Day"]["image"] is None
    assert by_name["Columbus Day"]["image_name"] is None
    assert by_name["Columbus Day"]["image_slug"] == "columbus_day"   # but you can still add one
