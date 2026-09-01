import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

# Point the runtime image cache and the user-data dir at throwaway dirs before anything
# imports them, so tests see the same empty ones everywhere instead of whatever the
# developer's box happens to have downloaded or uploaded.
os.environ.setdefault("SCOREBOARD_CACHE_DIR", tempfile.mkdtemp(prefix="scoreboard-test-cache-"))
os.environ.setdefault("SCOREBOARD_DATA_DIR", tempfile.mkdtemp(prefix="scoreboard-test-data-"))

from scoreboard.boards.base import BoardContext
from scoreboard.config import ConfigStore
from scoreboard.data import Snapshot
from scoreboard.render.profiles import profile_for


@pytest.fixture
def config_store(tmp_path: Path) -> ConfigStore:
    return ConfigStore(tmp_path / "config.json")


@pytest.fixture
def ctx() -> BoardContext:
    return BoardContext(
        snapshot=Snapshot(),
        profile=profile_for(128, 64),
        width=128,
        height=64,
        fps=30,
        now=datetime(2026, 1, 15, 19, 5, tzinfo=UTC),
        elapsed=0.0,
    )
