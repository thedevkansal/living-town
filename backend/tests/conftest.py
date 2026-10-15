import uuid

import pytest

from app import sim
from app.db import store


@pytest.fixture
async def world():
    """A fresh, isolated tenant graph per test (deleted afterwards)."""
    pid = f"test-{uuid.uuid4().hex[:8]}"
    await sim.create_world(pid, "Tester")
    await sim.start_session(pid)
    yield pid
    await store.drop(pid)


async def run_theft(pid: str) -> str:
    """Canonical scenario: steal Bram's hammer at the forge in the morning."""
    await sim.act(pid, {"action": "move", "location": "forge"})
    await sim.tick(pid)  # -> day 1 morning
    out = await sim.act(pid, {"action": "steal", "item": "hammer"})
    return out["fact"]
