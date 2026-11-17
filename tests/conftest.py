import pytest

from liveclips.core.config import load_settings
from liveclips.core.db import DB


@pytest.fixture
def db():
    return DB(":memory:")


@pytest.fixture
def settings():
    return load_settings({"DATA_DIR": "/tmp/liveclips-test"})
