from datetime import UTC, datetime
from pathlib import Path

import pytest

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
