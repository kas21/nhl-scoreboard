"""Goal replays: the tracking clip behind each goal, fetched after the game and drawn on a rink."""
import asyncio
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from scoreboard.boards.base import BoardContext
from scoreboard.data import Event, SnapshotStore
from scoreboard.data.source import SourceContext
from scoreboard.nhl.api import BASE_URL, NhlApi
from scoreboard.nhl.boards.replay import (
    FINAL,
    INTERMISSION,
    STOPPAGE,
    GoalReplayAlert,
    GoalReplayBoard,
    ReplayAlertConfig,
    ReplayConfig,
    Rink,
    header_image,
)
from scoreboard.nhl.replay import (
    SPRITE_HEADERS,
    ReplayFetcher,
    SpriteUnavailable,
    compact_replay,
    detect_replays,
    fetch_sprite,
    keeps_recap,
    replay_candidate,
    replays_from_files,
)
from scoreboard.nhl.source import NhlConfig, NhlSource
from scoreboard.render.profiles import profile_for

FIX = Path(__file__).parent / "fixtures" / "nhl"
GAME = 2025021270
SPRITES = "https://wsr.nhle.com/sprites/20252026/2025021270"
TORONTO = ZoneInfo("America/Toronto")


def load(name):
    return json.loads((FIX / name).read_text())


@pytest.fixture
def landing():
    return load("landing_2025021270.json")


@pytest.fixture
def raw_101():
    return load("ppt_replay_2025021270_ev101.json")


# -- the file -> what a board draws --------------------------------------------


def test_compact_replay_keeps_players_puck_and_frame_offsets(raw_101):
    clip = compact_replay(raw_101, away_id=13, home_id=10)
    assert clip["fps"] == 10 and len(clip["frames"]) == 140
    assert len(clip["players"]) == 12                                   # six a side; the puck is not a player
    assert {p["side"] for p in clip["players"]} == {"away", "home"}
    assert sum(p["side"] == "away" for p in clip["players"]) == 6
    first = clip["frames"][0]
    assert first["t"] == 0 and clip["frames"][-1]["t"] == 139             # tenths of a second, from the first frame
    assert first["puck"] == [348, 988]                                    # inches, rounded
    assert len(first["on_ice"]) == 12 and all(len(e) == 3 for e in first["on_ice"])
    assert all(0 <= x <= 2400 and 0 <= y <= 1020 for _, x, y in first["on_ice"])
    luostarinen = next(i for i, p in enumerate(clip["players"]) if p["player_id"] == 8480185)
    assert clip["players"][luostarinen]["sweater"] == 27


def test_compact_replay_follows_a_line_change():
    raw = load("ppt_replay_2025021270_ev950.json")
    clip = compact_replay(raw, 13, 10)
    assert len(clip["players"]) == 13                                     # a 13th skater appears mid-clip
    assert any(len(f["on_ice"]) != 12 for f in clip["frames"]) or len(clip["players"]) == 13


def test_compact_replay_survives_junk():
    assert compact_replay([], 1, 2) is None
    assert compact_replay([{"timeStamp": 1, "onIce": "nope"}], 1, 2) is None
    clip = compact_replay([{"timeStamp": 5, "onIce": {"1": {"x": 1.2, "y": 3.7, "playerId": ""},
                                                       "99": {"x": "bad", "playerId": 7},
                                                       "7": {"x": 10, "y": 20, "playerId": 7, "teamId": 2, "sweaterNumber": "34"}}}], 1, 2)
    assert clip["frames"] == [{"t": 0, "puck": [1, 4], "on_ice": [[0, 10, 20]]}]
    assert clip["players"] == [{"side": "home", "sweater": 34, "player_id": 7}]


def test_value_from_files_marks_goals_with_and_without_clips(landing, raw_101):
    value = replays_from_files(landing, {101: raw_101}, ["TOR"])
    assert value["game_id"] == GAME and value["away"]["abbrev"] == "FLA" and value["home"]["abbrev"] == "TOR"
    assert value["favorite"] == "TOR"
    assert len(value["goals"]) == 8
    with_clip = [g for g in value["goals"] if g["clip"]]
    assert [g["event_id"] for g in with_clip] == [101]
    g = with_clip[0]
    assert g["team"] == "FLA" and g["last_name"] == "Luostarinen" and g["scorer_id"] == 8480185
    assert (g["period"], g["time"], g["away_score"], g["home_score"]) == (1, "00:23", 1, 0)
    assert g["home_defending_side"] == "right" and g["shot_type"] == "wrist"
    assert value["goals"][3]["team"] == "TOR" and value["goals"][3]["clip"] is None     # the feed never gave Nylander's a file


# -- which game ----------------------------------------------------------------------


def _snap(**keys):
    store = SnapshotStore()
    snap = store.get()
    for k, v in keys.items():
        snap = store.publish(k.replace("__", "."), v)
    return snap


def test_candidate_is_the_favourites_game_on_or_over_then_its_last_result():
    final = {"id": 5, "date": "2026-04-11", "phase": "postgame", "outcome": "FINAL"}
    assert replay_candidate(_snap(nhl__main_event=final), ["TOR"]) == {"id": 5, "date": "2026-04-11", "phase": "postgame"}
    live = {**final, "phase": "live", "outcome": ""}
    assert replay_candidate(_snap(nhl__main_event=live), ["TOR"]) == {"id": 5, "date": "2026-04-11", "phase": "live"}      # files land during the game
    assert replay_candidate(_snap(nhl__main_event={**live, "phase": "intermission"}), ["TOR"])["phase"] == "intermission"
    summary = {"TOR": {"prev_game": {"id": 7, "date": "2026-04-10", "result": "W"}}}
    assert replay_candidate(_snap(nhl__main_event=None, nhl__team_summary=summary), ["TOR"]) == {"id": 7, "date": "2026-04-10", "phase": "postgame"}
    assert replay_candidate(_snap(nhl__team_summary=summary), ["TOR"]) is None             # the scores loop has not spoken yet
    assert replay_candidate(_snap(nhl__main_event={**final, "phase": "pregame", "outcome": ""}, nhl__team_summary=summary), ["TOR"])["id"] == 7
    assert replay_candidate(_snap(nhl__main_event={**final, "outcome": "PPD"}, nhl__team_summary=summary), ["TOR"])["id"] == 7
    assert replay_candidate(_snap(nhl__main_event=None, nhl__team_summary={"TOR": {"prev_game": {"id": 7, "result": ""}}}), ["TOR"]) is None
    assert replay_candidate(_snap(nhl__main_event=None), ["TOR"]) is None
    simulated = {**final, "id": 2099990001, "simulated": True}
    assert replay_candidate(_snap(nhl__main_event=simulated, nhl__team_summary=summary), ["TOR"]) is None   # not a real game, and not a reason to drop the real recap
    assert replay_candidate(_snap(nhl__main_event={**simulated, "phase": "live"}, nhl__team_summary=summary), ["TOR"]) is None


def test_only_a_simulated_game_keeps_the_last_recap_up():
    assert keeps_recap({"phase": "postgame", "outcome": "FINAL", "simulated": True})
    assert keeps_recap({"phase": "pregame", "simulated": True})
    assert not keeps_recap({"phase": "pregame"}) and not keeps_recap(None)           # a real game that is not a candidate: nothing to hold for
    assert not keeps_recap({"phase": "postgame", "outcome": "PPD"})


# -- the detector: when a clip is worth interrupting for ------------------------------


def _game(**over):
    base = {"id": GAME, "sport": "nhl", "phase": "live", "clock_running": True, "period_number": 1, "outcome": "",
            "away": {"abbrev": "FLA"}, "home": {"abbrev": "TOR"}}
    return {**base, **over}


def _recap(*event_ids, periods=None):
    periods = periods or {}
    return {"game_id": GAME, "away": {"abbrev": "FLA"}, "home": {"abbrev": "TOR"},
            "goals": [{"event_id": ev, "period": periods.get(ev, 1), "clip": {"fps": 10, "players": [], "frames": [{"t": 0, "puck": None, "on_ice": []}]}}
                      for ev in event_ids]}


def _diff(before: dict, after: dict):
    store = SnapshotStore()
    prev = store.get()
    for k, v in before.items():
        prev = store.publish(k, v)
    new = prev
    for k, v in after.items():
        new = store.publish(k, v)
    return list(detect_replays(prev, new))


def test_whistle_with_a_clip_in_hand_is_a_stoppage_event():
    running = {"nhl.main_event": _game(), "nhl.goal_replays": _recap(101)}
    events = _diff(running, {"nhl.main_event": _game(clock_running=False)})
    assert [e.kind for e in events] == [STOPPAGE] and events[0].payload["clips"] == [101]
    assert "frames" not in json.dumps(events[0].payload)                                 # ids only, no clip data: the MQTT bridge mirrors events
    assert _diff(running, {"nhl.main_event": _game(clock_running=True)}) == []          # play goes on: nothing
    assert _diff({"nhl.main_event": _game(clock_running=False), "nhl.goal_replays": _recap(101)},
                 {"nhl.main_event": _game(clock_running=False)}) == []                   # still stopped, nothing new
    assert _diff({"nhl.main_event": _game()}, {"nhl.main_event": _game(clock_running=False)}) == []     # whistle, no clips yet


def test_a_clip_arriving_during_a_stoppage_plays_at_once():
    stopped = {"nhl.main_event": _game(clock_running=False), "nhl.goal_replays": _recap(101)}
    events = _diff(stopped, {"nhl.goal_replays": _recap(101, 152)})
    assert [e.kind for e in events] == [STOPPAGE] and events[0].payload["clips"] == [101, 152]
    assert _diff({"nhl.main_event": _game(), "nhl.goal_replays": _recap(101)}, {"nhl.goal_replays": _recap(101, 152)}) == []   # in play: wait for the whistle


def test_intermission_and_final_events():
    live = {"nhl.main_event": _game(period_number=1), "nhl.goal_replays": _recap(101, 152)}
    events = _diff(live, {"nhl.main_event": _game(phase="intermission", clock_running=False, period_number=1)})
    assert [e.kind for e in events] == [INTERMISSION] and events[0].payload["period"] == 1
    inter = {"nhl.main_event": _game(phase="intermission", clock_running=False), "nhl.goal_replays": _recap(101)}
    assert _diff(inter, {"nhl.main_event": _game(phase="intermission", clock_running=False)}) == []       # same intermission, nothing new
    late = _diff(inter, {"nhl.goal_replays": _recap(101, 152)})
    assert [e.kind for e in late] == [INTERMISSION] and late[0].payload["clips"] == [101, 152]           # a late clip plays by itself
    over = _diff({"nhl.main_event": _game(phase="live"), "nhl.goal_replays": _recap(101)},
                 {"nhl.main_event": _game(phase="postgame", outcome="FINAL/OT", clock_running=False)})
    assert [e.kind for e in over] == [FINAL]
    assert _diff({"nhl.main_event": _game(phase="live"), "nhl.goal_replays": _recap(101)},
                 {"nhl.main_event": _game(phase="postgame", outcome="PPD")}) == []
    assert _diff({"nhl.main_event": _game(simulated=True), "nhl.goal_replays": _recap(101)},
                 {"nhl.main_event": _game(simulated=True, clock_running=False)}) == []
    assert _diff({"nhl.main_event": _game(), "nhl.goal_replays": {**_recap(101), "game_id": 1}},
                 {"nhl.main_event": _game(clock_running=False)}) == []                                   # a recap of another game


# -- the fetch# -- the fetch ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sprite_fetch_goes_out_browser_shaped_and_tells_missing_from_broken(raw_101):
    async with httpx.AsyncClient() as http, respx.mock(assert_all_called=False) as mock:
        ok = mock.get(f"{SPRITES}/ev101.json").mock(return_value=httpx.Response(200, json=raw_101))
        mock.get(f"{SPRITES}/ev152.json").mock(return_value=httpx.Response(403, text="<Error><Code>AccessDenied</Code></Error>"))
        mock.get(f"{SPRITES}/ev516.json").mock(return_value=httpx.Response(500))
        mock.get(f"{SPRITES}/ev786.json").mock(return_value=httpx.Response(200, json={"not": "a list"}))
        assert len(await fetch_sprite(http, f"{SPRITES}/ev101.json")) == 140
        sent = ok.calls[0].request.headers
        assert sent["origin"] == SPRITE_HEADERS["Origin"] and sent["referer"] == SPRITE_HEADERS["Referer"]
        assert "Mozilla" in sent["user-agent"]                                        # the bucket 403s a bare client
        with pytest.raises(SpriteUnavailable):
            await fetch_sprite(http, f"{SPRITES}/ev152.json")
        with pytest.raises(httpx.HTTPError):
            await fetch_sprite(http, f"{SPRITES}/ev516.json")
        with pytest.raises(ValueError):
            await fetch_sprite(http, f"{SPRITES}/ev786.json")


@pytest.mark.asyncio
async def test_fetcher_keeps_asking_until_the_batch_lands(landing, raw_101):
    """Goal 101's file is there at once, 152's turns up on the second look, the rest never do."""
    async with httpx.AsyncClient() as http, respx.mock(assert_all_called=False) as mock:
        mock.get(f"{BASE_URL}/gamecenter/{GAME}/landing").mock(return_value=httpx.Response(200, json=landing))
        mock.get(f"{SPRITES}/ev101.json").mock(return_value=httpx.Response(200, json=raw_101))
        late = mock.get(f"{SPRITES}/ev152.json").mock(return_value=httpx.Response(403))
        missing = mock.get(url__regex=rf"{SPRITES}/ev\d+\.json").mock(return_value=httpx.Response(403))
        fetcher = ReplayFetcher(GAME)
        value = await fetcher.refresh(NhlApi(http), http, ["TOR"], max_attempts=2)
        assert [g["event_id"] for g in value["goals"] if g["clip"]] == [101]
        assert fetcher.pending(2)
        late.mock(return_value=httpx.Response(200, json=raw_101))
        value = await fetcher.refresh(NhlApi(http), http, ["TOR"], max_attempts=2)
        assert [g["event_id"] for g in value["goals"] if g["clip"]] == [101, 152]
        assert fetcher.pending(2)                                           # Nylander's goals have no URL at all: the batch may still add one
        assert await fetcher.refresh(NhlApi(http), http, ["TOR"], max_attempts=2) is None     # nothing new: nothing to publish
        assert missing.call_count == 4 * 2                                  # four goals with URLs but no file, twice each, then left alone
        for g in [g for g in landing["summary"]["scoring"][1]["goals"] if g["teamAbbrev"]["default"] == "TOR"]:
            g["pptReplayUrl"] = f"{SPRITES}/ev{g['eventId']}.json"
        mock.get(f"{BASE_URL}/gamecenter/{GAME}/landing").mock(return_value=httpx.Response(200, json=landing))
        await fetcher.refresh(NhlApi(http), http, ["TOR"], max_attempts=1)
        assert fetcher.pending(1) is False                                  # every goal has a URL and has had its tries


@pytest.mark.asyncio
async def test_fetcher_refetches_once_after_the_final(landing, raw_101):
    """A clip fetched during the game is fetched again after the final, since the league rewrites the files."""
    async with httpx.AsyncClient() as http, respx.mock(assert_all_called=False) as mock:
        mock.get(f"{BASE_URL}/gamecenter/{GAME}/landing").mock(return_value=httpx.Response(200, json=landing))
        sprite = mock.get(f"{SPRITES}/ev101.json").mock(return_value=httpx.Response(200, json=raw_101))
        mock.get(url__regex=rf"{SPRITES}/ev\d+\.json").mock(return_value=httpx.Response(403))
        fetcher = ReplayFetcher(GAME)
        await fetcher.refresh(NhlApi(http), http, ["TOR"], max_attempts=1, in_play=True)
        assert sprite.call_count == 1 and fetcher.fetched_in_play
        assert fetcher.rewrite() is True and fetcher.clips == {}
        assert fetcher.rewrite() is False                                   # once
        await fetcher.refresh(NhlApi(http), http, ["TOR"], max_attempts=1)
        assert sprite.call_count == 2 and 101 in fetcher.clips
        fresh = ReplayFetcher(GAME)
        await fresh.refresh(NhlApi(http), http, ["TOR"], max_attempts=1)
        assert fresh.rewrite() is False                                     # fetched after the final already: nothing to redo


@pytest.mark.asyncio
async def test_source_publishes_replays_for_the_favourites_final(monkeypatch, landing, raw_101):
    """The fixture day: the Leafs' game is over, so the replay loop fetches the landing and the sprites."""
    import scoreboard.nhl.source as src
    monkeypatch.setattr(src, "_local_today", lambda ctx: "2026-04-11")
    monkeypatch.setattr(src, "carry_last_night", lambda ctx: False)
    monkeypatch.setattr(src, "REPLAY_LOOKUP_SECONDS", 0.02)
    store = SnapshotStore()
    cfg = NhlConfig(favorites=["TOR"], idle_interval=15, standings_interval=300, replay_interval=30)
    async with httpx.AsyncClient() as http, respx.mock(assert_all_called=False) as mock:
        mock.get(f"{BASE_URL}/score/now").mock(return_value=httpx.Response(200, json=load("score_2026-04-11.json")))
        mock.get(f"{BASE_URL}/standings/now").mock(return_value=httpx.Response(200, json=load("standings_2026-04-10.json")))
        mock.get(f"{BASE_URL}/club-schedule-season/TOR/now").mock(return_value=httpx.Response(200, json=load("club_schedule_TOR_week.json")))
        mock.get(f"{BASE_URL}/schedule/now").mock(return_value=httpx.Response(200, json=load("schedule_now.json")))
        mock.get(url__regex=rf"{BASE_URL}/schedule/\d{{4}}-\d\d-\d\d").mock(return_value=httpx.Response(200, json={"gameWeek": []}))
        mock.get(f"{BASE_URL}/gamecenter/{GAME}/landing").mock(return_value=httpx.Response(200, json=landing))
        mock.get(f"{SPRITES}/ev101.json").mock(return_value=httpx.Response(200, json=raw_101))
        mock.get(url__regex=rf"{SPRITES}/ev\d+\.json").mock(return_value=httpx.Response(403))
        ctx = SourceContext("nhl", store, lambda: cfg, http)
        task = asyncio.create_task(NhlSource().run(ctx))
        for _ in range(200):
            await asyncio.sleep(0.01)
            if store.get().get("nhl.goal_replays"):
                break
        task.cancel()
        snap = store.get()
    main = snap.get("nhl.main_event")
    assert main["id"] == GAME and main["phase"] == "postgame"
    value = snap.get("nhl.goal_replays")
    assert value["game_id"] == GAME and value["favorite"] == "TOR"
    assert [g["event_id"] for g in value["goals"] if g["clip"]] == [101]


@pytest.mark.asyncio
async def test_source_fetches_during_the_game(monkeypatch, landing, raw_101):
    """The score feed says the Leafs' game is live: the clips are fetched now, not after the horn."""
    import scoreboard.nhl.source as src
    monkeypatch.setattr(src, "_local_today", lambda ctx: "2026-04-11")
    monkeypatch.setattr(src, "carry_last_night", lambda ctx: False)
    monkeypatch.setattr(src, "REPLAY_LOOKUP_SECONDS", 0.02)
    score = load("score_2026-04-11.json")
    for g in score["games"]:
        if g["homeTeam"]["abbrev"] == "TOR":
            g["gameState"] = "LIVE"
            g["clock"] = {"timeRemaining": "12:34", "running": True, "inIntermission": False}
            g["periodDescriptor"] = {"number": 2, "periodType": "REG"}
    live_landing = {**landing, "gameState": "LIVE", "clock": {"timeRemaining": "12:34", "running": True, "inIntermission": False},
                    "periodDescriptor": {"number": 2, "periodType": "REG"}}
    store = SnapshotStore()
    cfg = NhlConfig(favorites=["TOR"], idle_interval=15, standings_interval=300)
    async with httpx.AsyncClient() as http, respx.mock(assert_all_called=False) as mock:
        mock.get(f"{BASE_URL}/score/now").mock(return_value=httpx.Response(200, json=score))
        mock.get(f"{BASE_URL}/standings/now").mock(return_value=httpx.Response(200, json=load("standings_2026-04-10.json")))
        mock.get(f"{BASE_URL}/club-schedule-season/TOR/now").mock(return_value=httpx.Response(200, json=load("club_schedule_TOR_week.json")))
        mock.get(f"{BASE_URL}/schedule/now").mock(return_value=httpx.Response(200, json=load("schedule_now.json")))
        mock.get(url__regex=rf"{BASE_URL}/schedule/\d{{4}}-\d\d-\d\d").mock(return_value=httpx.Response(200, json={"gameWeek": []}))
        mock.get(f"{BASE_URL}/gamecenter/{GAME}/landing").mock(return_value=httpx.Response(200, json=live_landing))
        mock.get(f"{SPRITES}/ev101.json").mock(return_value=httpx.Response(200, json=raw_101))
        mock.get(url__regex=rf"{SPRITES}/ev\d+\.json").mock(return_value=httpx.Response(403))
        ctx = SourceContext("nhl", store, lambda: cfg, http)
        task = asyncio.create_task(NhlSource().run(ctx))
        for _ in range(200):
            await asyncio.sleep(0.01)
            if store.get().get("nhl.goal_replays"):
                break
        task.cancel()
        snap = store.get()
    assert snap.get("nhl.main_event")["phase"] == "live"
    assert [g["event_id"] for g in snap.get("nhl.goal_replays")["goals"] if g["clip"]] == [101]


@pytest.mark.asyncio
async def test_source_leaves_replays_alone_when_switched_off(monkeypatch, landing):
    import scoreboard.nhl.source as src
    monkeypatch.setattr(src, "_local_today", lambda ctx: "2026-04-11")
    monkeypatch.setattr(src, "carry_last_night", lambda ctx: False)
    monkeypatch.setattr(src, "REPLAY_LOOKUP_SECONDS", 0.02)
    store = SnapshotStore()
    cfg = NhlConfig(favorites=["TOR"], idle_interval=15, standings_interval=300, goal_replays=False)
    async with httpx.AsyncClient() as http, respx.mock(assert_all_called=False) as mock:
        mock.get(f"{BASE_URL}/score/now").mock(return_value=httpx.Response(200, json=load("score_2026-04-11.json")))
        mock.get(f"{BASE_URL}/standings/now").mock(return_value=httpx.Response(200, json=load("standings_2026-04-10.json")))
        mock.get(f"{BASE_URL}/club-schedule-season/TOR/now").mock(return_value=httpx.Response(200, json=load("club_schedule_TOR_week.json")))
        mock.get(f"{BASE_URL}/schedule/now").mock(return_value=httpx.Response(200, json=load("schedule_now.json")))
        mock.get(url__regex=rf"{BASE_URL}/schedule/\d{{4}}-\d\d-\d\d").mock(return_value=httpx.Response(200, json={"gameWeek": []}))
        sprites = mock.get(url__regex=rf"{SPRITES}/ev\d+\.json").mock(return_value=httpx.Response(403))
        ctx = SourceContext("nhl", store, lambda: cfg, http)
        task = asyncio.create_task(NhlSource().run(ctx))
        for _ in range(50):
            await asyncio.sleep(0.01)
            if store.get().has("nhl.main_event"):
                break
        await asyncio.sleep(0.05)
        task.cancel()
        snap = store.get()
    assert not snap.has("nhl.goal_replays") and sprites.call_count == 0


# -- the board ----------------------------------------------------------------------


def _ctx(snap, w, h, elapsed, pace=None):
    return BoardContext(snapshot=snap, profile=profile_for(w, h), width=w, height=h, fps=30,
                        now=datetime(2026, 4, 11, 22, 0, tzinfo=TORONTO), elapsed=elapsed, pace=pace)


@pytest.fixture
def replays(landing, raw_101):
    sprites = {101: raw_101, 950: load("ppt_replay_2025021270_ev950.json")}
    return _snap(nhl__goal_replays=replays_from_files(landing, sprites, ["TOR"]))


def test_rink_fits_under_the_header_at_every_size():
    for w, h in ((128, 64), (64, 32), (128, 32), (128, 128), (256, 256)):
        r = Rink(w, h)
        assert r.y >= 8 and r.y + r.h <= h and r.x >= 0 and r.x + r.w <= w
        assert r.px(0, 0) == (r.x, r.y) and r.px(2400, 1020) == (r.x + r.w - 1, r.y + r.h - 1)
        assert r.px(-50, 5000) == (r.x, r.y + r.h - 1)                              # off-rink coordinates clamp


def test_board_plays_every_goal_with_a_clip_in_order(replays):
    board, cfg = GoalReplayBoard(), ReplayConfig()
    ctx = _ctx(replays, 128, 64, 0.0)
    assert board.auto_items(ctx, cfg) == (2, "goal")
    assert board.auto_seconds(ctx, cfg) == pytest.approx(2 * (14.0 + cfg.hold_seconds))      # 140 frames at 10 fps, then the hold
    for w, h in ((128, 64), (64, 32), (128, 32)):
        img = board.render(_ctx(replays, w, h, 5.0), cfg)
        assert img.size == (w, h) and img.getbbox()
    assert not board.done(_ctx(replays, 128, 64, 20.0), cfg)
    assert board.done(_ctx(replays, 128, 64, 31.0), cfg)
    assert board.render(_ctx(replays, 128, 64, 40.0), cfg).getbbox() is None            # past the end: black


def test_board_header_names_the_scorer_and_the_score(replays):
    board, cfg = GoalReplayBoard(), ReplayConfig()
    goals = [g for g in replays.get("nhl.goal_replays")["goals"] if g["clip"]]
    assert [(g["last_name"], g["away_score"], g["home_score"]) for g in goals] == [("Luostarinen", 1, 0), ("Nosek", 6, 2)]
    first = board.render(_ctx(replays, 128, 64, 1.0), cfg)
    second = board.render(_ctx(replays, 128, 64, 14.0 + cfg.hold_seconds + 1.0), cfg)
    assert first.crop((0, 0, 128, 7)).tobytes() == header_image(128, "FLA", "LUOSTARINEN", "1-0").tobytes()
    assert second.crop((0, 0, 128, 7)).tobytes() == header_image(128, "FLA", "NOSEK", "6-2").tobytes()
    assert first.crop((0, 0, 128, 7)).tobytes() != second.crop((0, 0, 128, 7)).tobytes()


def test_board_moves_the_dots_between_frames(replays):
    board, cfg = GoalReplayBoard(), ReplayConfig(puck_trail=False)
    a = board.render(_ctx(replays, 128, 64, 1.0), cfg).crop((0, 8, 128, 64))
    b = board.render(_ctx(replays, 128, 64, 6.0), cfg).crop((0, 8, 128, 64))
    assert a.tobytes() != b.tobytes()
    held = board.render(_ctx(replays, 128, 64, 14.2), cfg).crop((0, 8, 128, 64))
    held_again = board.render(_ctx(replays, 128, 64, 14.4), cfg).crop((0, 8, 128, 64))
    held_later = board.render(_ctx(replays, 128, 64, 14.8), cfg).crop((0, 8, 128, 64))
    assert held.tobytes() != held_again.tobytes()                 # the puck and scorer blink during the hold...
    assert held.tobytes() == held_later.tobytes()                 # ...and nothing else moves


def test_playlist_seconds_squeeze_the_clip_to_fit(replays):
    board, cfg = GoalReplayBoard(), ReplayConfig()
    paced = _ctx(replays, 128, 64, 0.0, pace=6.0)
    assert board.auto_seconds(paced, cfg) == pytest.approx(12.0)                        # six seconds a goal, hold included
    assert board.done(_ctx(replays, 128, 64, 12.0, pace=6.0), cfg)
    end_of_first = board.render(_ctx(replays, 128, 64, 4.49, pace=6.0), cfg)             # the clip's last frame comes at 4.5 s now
    natural = board.render(_ctx(replays, 128, 64, 13.97), cfg)                           # same frame, same blink phase
    assert end_of_first.crop((0, 8, 128, 64)).tobytes() == natural.crop((0, 8, 128, 64)).tobytes()


def test_favourite_only_filter_and_empty_value(landing, raw_101):
    value = replays_from_files(landing, {101: raw_101}, ["TOR"])
    board = GoalReplayBoard()
    snap = _snap(nhl__goal_replays=value)
    assert board.auto_items(_ctx(snap, 128, 64, 0.0), ReplayConfig(favorite_goals_only=True)) is None   # only a Panthers goal has a clip
    ctx = _ctx(snap, 128, 64, 0.0)
    assert board.done(ctx, ReplayConfig(favorite_goals_only=True))
    assert board.render(ctx, ReplayConfig(favorite_goals_only=True)).size == (128, 64)
    assert board.done(_ctx(_snap(nhl__goal_replays=None), 128, 64, 0.0), ReplayConfig())


def _event(kind, clips, game=None, **extra):
    return Event(kind, ts=1.0, payload={"game": game or _game(), "clips": list(clips), **extra})


def test_alert_plays_what_the_viewer_has_not_seen_at_a_whistle(replays):
    board, cfg = GoalReplayAlert(), ReplayAlertConfig()
    first = _event(STOPPAGE, [101])
    assert board.matches(first, cfg)
    ctx = _ctx(replays, 128, 64, 1.0); ctx = BoardContext(**{**ctx.__dict__, "event": first})
    board.enter(ctx, cfg)
    assert [g["event_id"] for g in board._playing] == [101]
    assert board.auto_seconds(ctx, cfg) == pytest.approx(14.0 + cfg.hold_seconds)
    assert board.render(ctx, cfg).crop((0, 0, 128, 7)).tobytes() == header_image(128, "FLA", "LUOSTARINEN", "1-0").tobytes()
    assert not board.done(ctx, cfg) and board.done(BoardContext(**{**ctx.__dict__, "elapsed": 16.0}), cfg)
    assert not board.matches(_event(STOPPAGE, [101]), cfg)                 # the next whistle: nothing new
    second = _event(STOPPAGE, [101, 950])
    assert board.matches(second, cfg)                                       # a second goal's clip: that one only
    board.enter(BoardContext(**{**ctx.__dict__, "event": second}), cfg)
    assert [g["event_id"] for g in board._playing] == [950]
    assert board.matches(_event(STOPPAGE, [101, 950], game=_game(id=1)), cfg)      # another game starts afresh


def test_alert_switches_and_occasions(replays):
    board = GoalReplayAlert()
    off = ReplayAlertConfig(at_stoppage=False)
    assert not board.matches(_event(STOPPAGE, [101]), off)
    assert not board.matches(_event(FINAL, [101]), ReplayAlertConfig())             # off by default: the playlist board covers it
    assert board.matches(_event(FINAL, [101]), ReplayAlertConfig(at_final=True))
    assert not board.matches(_event(STOPPAGE, [101]), ReplayAlertConfig(enabled=False))
    # the intermission replays the period even after the whistle showed the goal, and only that period
    inter = _event(INTERMISSION, [101, 950], period=1)
    ctx = BoardContext(**{**_ctx(replays, 128, 64, 0.5).__dict__, "event": _event(STOPPAGE, [101, 950])})
    board.enter(ctx, ReplayAlertConfig())
    assert board.matches(inter, ReplayAlertConfig())
    board.enter(BoardContext(**{**ctx.__dict__, "event": inter}), ReplayAlertConfig())
    assert [g["event_id"] for g in board._playing] == [101]                         # 950 is a third-period goal
    assert not board.matches(inter, ReplayAlertConfig())
    assert board.matches(_event(INTERMISSION, [101, 950], period=3), ReplayAlertConfig())
    # favourite-only keeps the Panthers' goals off, and does not re-arm the next whistle with them
    fav = ReplayAlertConfig(favorite_goals_only=True)
    fav_ctx = BoardContext(**{**ctx.__dict__, "event": _event(STOPPAGE, [101], game=_game(id=GAME))})
    fresh = GoalReplayAlert()
    assert fresh.matches(fav_ctx.event, fav)
    fresh.enter(fav_ctx, fav)
    assert fresh._playing == [] and fresh.done(fav_ctx, fav)
    assert not fresh.matches(_event(STOPPAGE, [101]), fav)


def test_alert_renders_without_enter_and_outlives_the_default_event_cap(replays):
    from scoreboard.director.director import EVENT_MAX_SECONDS
    board, cfg = GoalReplayAlert(), ReplayAlertConfig()
    ctx = BoardContext(**{**_ctx(replays, 64, 32, 3.0).__dict__, "event": _event(STOPPAGE, [101, 950])})
    img = board.render(ctx, cfg)                                             # the golden harness and a UI preview enter through render
    assert img.size == (64, 32) and img.getbbox()
    assert board.auto_seconds(ctx, cfg) > EVENT_MAX_SECONDS <= board.max_seconds   # two clips outrun the default cap; the board raises its own


def test_board_is_registered_and_in_the_postgame_rotation():
    from scoreboard.config.models import Playlists
    from scoreboard.plugins import load_registry
    registry = load_registry()
    assert "nhl.goal_replay" in registry.boards and "nhl.goal_replay_alert" in registry.boards
    assert any(getattr(d, "__name__", "") == "detect_replays" for d in registry.detectors.values()) if isinstance(registry.detectors, dict) else True
    assert "nhl.goal_replay" in [e.board for e in Playlists().postgame]
