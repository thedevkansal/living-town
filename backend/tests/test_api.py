"""HTTP-level guards for the public deployment (against the live FalkorDB, own test-* graphs)."""
import asyncio
import uuid

import httpx
import pytest

from app import main, sim
from app.db import store


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://t") as c:
        yield c


@pytest.fixture
def pid():
    return f"test-api-{uuid.uuid4().hex[:6]}"  # each test drops its own graph


async def test_operator_routes_disabled_without_admin_token(client):
    assert (await client.get("/api/worlds")).status_code == 404
    assert (await client.delete("/api/worlds/someone")).status_code == 404
    assert (await client.delete("/api/worlds/someone", headers={"x-admin-token": "guess"})).status_code == 404


async def test_demo_world_cannot_be_reset(client):
    assert (await client.post("/api/worlds/demo/reset")).status_code == 403
    r = await client.post("/api/worlds", json={"player_id": "demo", "name": "x", "reset": True})
    assert r.status_code == 403


async def test_oversized_input_rejected(client):
    r = await client.post("/api/worlds", json={"player_id": "a" * 10_000, "name": "x"})
    assert r.status_code == 422


async def test_empty_graph_is_rebuilt_not_a_crash(client, pid):
    try:
        await store.rw(pid, "MATCH (t:Town) SET t.tick = 1")  # a write on a missing key leaves an empty graph
        assert await store.exists(pid) and not await sim.world_ok(pid)
        r = await client.post("/api/worlds", json={"player_id": pid, "name": "Ada"})
        assert r.status_code == 200 and r.json()["returning"] is False
        assert (await client.get(f"/api/worlds/{pid}")).status_code == 200
    finally:
        await sim.drop_world(pid)


async def test_drop_during_autoplay_leaves_no_world(client, pid):
    assert (await client.post("/api/worlds", json={"player_id": pid, "name": "Ada"})).status_code == 200
    r = await client.post(f"/api/worlds/{pid}/autoplay", json={"on": True, "interval": 0.8})
    assert r.status_code == 200 and pid in sim.autoplay_tasks
    tick = asyncio.create_task(sim.tick(pid))  # a tick in flight while the world is deleted
    await sim.drop_world(pid)
    await asyncio.gather(tick, return_exceptions=True)
    await asyncio.sleep(1.0)
    assert pid not in sim.autoplay_tasks
    assert not await store.exists(pid) and pid not in await store.list_tenants()


async def test_autoplay_stops_when_nobody_watches(client, pid, monkeypatch):
    monkeypatch.setattr(main, "AUTOPLAY_NO_VIEWER_TICKS", 1)
    published = []
    real = main.hub.publish

    async def spy(p, msg):
        published.append(msg)
        await real(p, msg)

    monkeypatch.setattr(main.hub, "publish", spy)
    try:
        await client.post("/api/worlds", json={"player_id": pid, "name": "Ada"})
        await client.post(f"/api/worlds/{pid}/autoplay", json={"on": True, "interval": 0.8})
        task = sim.autoplay_tasks[pid]
        await asyncio.wait_for(task, 5)
        assert {"type": "autoplay", "on": False, "reason": "nobody watching"} in published
    finally:
        await sim.drop_world(pid)


async def test_only_one_bench_at_a_time(client, monkeypatch):
    gate = asyncio.Event()

    async def slow(n, ticks):
        await gate.wait()
        return {"ok": True}

    monkeypatch.setattr(main, "_bench", slow)
    monkeypatch.setattr(main, "_bench_last_end", 0.0)
    first = asyncio.create_task(client.post("/api/bench", json={"tenants": 1, "ticks": 1}))
    await asyncio.sleep(0.05)
    busy = await client.post("/api/bench", json={"tenants": 1, "ticks": 1})
    assert busy.status_code == 429
    gate.set()
    assert (await first).status_code == 200
    assert (await client.post("/api/bench", json={})).status_code == 429  # cooldown
