from datetime import datetime, timezone

import pytest

from liveclips.config import load_settings
from liveclips.db import DB
from liveclips.models import Candidate


@pytest.fixture
def db():
    return DB(":memory:")


@pytest.fixture
def settings():
    return load_settings({"DATA_DIR": "/tmp/liveclips-test"})


def make_candidate(**overrides) -> Candidate:
    base = dict(
        platform="twitch",
        source="chat_spike",
        clip_id="twitch:abc",
        clip_url="https://clips.twitch.tv/abc",
        channel="gaules",
        created_at=datetime(2026, 11, 19, 3, 0, tzinfo=timezone.utc),
        viewers=1000,
        spike_score=10.0,
        rank=10.0,
    )
    base.update(overrides)
    return Candidate(**base)
