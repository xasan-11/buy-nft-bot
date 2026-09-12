from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.database.repository import Repository

OWNER_ID = 111
ADMIN_ID = 222
STRANGER_ID = 333


@pytest.fixture
async def repo(tmp_path):
    r = Repository(tmp_path / "test.db")
    await r.connect(OWNER_ID)
    yield r
    await r.close()
