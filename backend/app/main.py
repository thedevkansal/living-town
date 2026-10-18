"""FastAPI app: per-player worlds, actions, ticks, dialogue, memory inspector,
provenance, analytics, a concurrency benchmark and a WebSocket event stream."""
from __future__ import annotations

import asyncio
import logging
import os
import statistics
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent, sim
from . import tools as T
from .config import settings
from .db import normalize_player_id, store
from .queries import CATALOGUE
from .world import TICKS_PER_DAY

log = logging.getLogger("living-town")
logging.basicConfig(level=logging.INFO)


# --------------------------------------------------------------------------- event hub (WebSocket fan-out)
class Hub:
    def __init__(self) -> None:
        self.subs: dict[str, set[WebSocket]] = {}

    async def publish(self, pid: str, msg: dict[str, Any]) -> None:
        dead = []
        for ws in list(self.subs.get(pid, ())):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.subs.get(pid, set()).discard(ws)


hub = Hub()
autoplay: dict[str, asyncio.Task] = {}

# Render's free tier sleeps a service after 15 minutes without inbound traffic. A request
# to our own public URL goes through Render's edge, so it counts as traffic; /api/health
# also pings FalkorDB, which keeps a free FalkorDB Cloud instance from pausing.
SELF_PING_SECONDS = 10 * 60


async def self_ping(base_url: str) -> None:
    from urllib.request import urlopen  # stdlib: httpx is only a dev dependency

    url = base_url.rstrip("/") + "/api/health"

    def get() -> int:
        with urlopen(url, timeout=60) as r:
            return r.status

    while True:
        await asyncio.sleep(SELF_PING_SECONDS)
        try:
            log.info("keep-awake ping %s -> %s", url, await asyncio.to_thread(get))
        except Exception as exc:
            log.warning("keep-awake ping failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # RENDER_EXTERNAL_URL is set by Render on every web service; unset locally.
    public_url = os.getenv("KEEP_AWAKE_URL") or os.getenv("RENDER_EXTERNAL_URL")
    pinger = asyncio.create_task(self_ping(public_url)) if public_url else None
    warm = None
    if await store.ping():
        warm = asyncio.create_task(store.warmup())  # open the connection pool in the background
        try:
            pruned = await sim.prune_worlds()
            if pruned:
                log.info("pruned stale worlds: %s", pruned)
            if settings.demo_autocreate and not await store.exists(settings.demo_player):
                await sim.create_world(settings.demo_player, "Traveller")
                log.info("created demo world town:%s", settings.demo_player)
        except Exception:
            log.exception("prune failed")
    else:
        log.warning("FalkorDB not reachable")
    yield
    if pinger:
        pinger.cancel()
    if warm:
        warm.cancel()
    for t in autoplay.values():
        t.cancel()


app = FastAPI(title="Living Town", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins.split(","), allow_methods=["*"],
                   allow_headers=["*"])


def pid_of(raw: str) -> str:
    try:
        return normalize_player_id(raw)
    except ValueError as e:
        raise HTTPException(400, str(e))


async def require_world(pid: str) -> None:
    if not await store.exists(pid):
        raise HTTPException(404, f"no world for player {pid!r}; POST /api/worlds first")


# --------------------------------------------------------------------------- meta
@app.get("/api/health")
async def health():
    """Liveness for the platform and the keep-alive cron. Pings FalkorDB so the cloud DB stays active too.
    Deliberately reveals nothing about where the database lives."""
    db = await store.ping()
    return {"ok": db, "db": db, "mode": settings.mode, "llm": settings.llm_enabled}


@app.get("/api/catalogue")
async def catalogue():
    return {"queries": [{"name": q.name, "kind": q.kind, "purpose": q.purpose, "cypher": q.cypher}
                        for q in CATALOGUE.values()],
            "tools": [{"name": t["name"], "description": t["description"]} for t in agent.TOOL_SPECS]}


@app.get("/api/worlds")
async def list_worlds():
    return {"worlds": await store.list_tenants()}


# --------------------------------------------------------------------------- worlds
class CreateWorld(BaseModel):
    player_id: str
    name: str | None = None
    reset: bool = False


@app.post("/api/worlds")
async def create_or_join(body: CreateWorld):
    pid = pid_of(body.player_id)
    returning = await store.exists(pid) and not body.reset
    async with sim.lock_for(pid):
        if not returning:
            await sim.create_world(pid, (body.name or body.player_id).strip()[:24] or "Wanderer")
            await sim.prune_worlds(keep={pid})
        sess = await sim.start_session(pid)
    greetings = await agent.greetings(pid, returning, sess["n"])
    st = await sim.state(pid)
    past = await store.ro(pid, "MATCH (:Player)-[:HAD_SESSION]->(s:Session) RETURN count(s) AS n")
    return {"returning": returning, "session": sess["n"], "sessions": past[0]["n"] if past else 1,
            "greetings": greetings, "state": st, "mode": settings.mode}


@app.post("/api/worlds/{player_id}/reset")
async def reset(player_id: str):
    pid = pid_of(player_id)
    name = None
    if await store.exists(pid):
        name = (await sim.clock(pid))["player_name"]
    async with sim.lock_for(pid):
        await sim.create_world(pid, name or player_id)
        sess = await sim.start_session(pid)
    await hub.publish(pid, {"type": "reset"})
    return {"session": sess["n"], "state": await sim.state(pid)}


@app.delete("/api/worlds/{player_id}")
async def delete_world(player_id: str):
    pid = pid_of(player_id)
    await store.drop(pid)
    return {"deleted": pid}


@app.get("/api/worlds/{player_id}")
async def get_state(player_id: str):
    pid = pid_of(player_id)
    await require_world(pid)
    return await sim.state(pid)


class Action(BaseModel):
    action: str
    npc: str | None = None
    item: str | None = None
    location: str | None = None
    subject: str | None = None
    message: str | None = None


@app.post("/api/worlds/{player_id}/act")
async def act(player_id: str, body: Action):
    pid = pid_of(player_id)
    await require_world(pid)
    try:
        out = await sim.act(pid, body.model_dump())
    except (sim.WorldError, T.ToolError) as e:
        raise HTTPException(400, str(e))
    await hub.publish(pid, {"type": "action", "action": body.action, **out})
    return out


async def _run_ticks(pid: str, n: int, delay: float) -> list[dict[str, Any]]:
    out = []
    for i in range(n):
        res = await sim.tick(pid)
        out.append(res)
        await hub.publish(pid, {"type": "tick", **res})
        if delay and i < n - 1:
            await asyncio.sleep(delay)
    return out


@app.post("/api/worlds/{player_id}/tick")
async def tick(player_id: str, n: int = Query(1, ge=1, le=24), delay: float = Query(0.0, ge=0, le=5)):
    pid = pid_of(player_id)
    await require_world(pid)
    ticks = await _run_ticks(pid, n, delay)
    return {"ticks": ticks, "clock": (await sim.clock(pid))}


@app.post("/api/worlds/{player_id}/advance-day")
async def advance_day(player_id: str, delay: float = Query(0.0, ge=0, le=5)):
    pid = pid_of(player_id)
    await require_world(pid)
    c = await sim.clock(pid)
    n = TICKS_PER_DAY - c["slot_index"]  # run until the next dawn
    ticks = await _run_ticks(pid, n, delay)
    return {"ticks": ticks, "clock": await sim.clock(pid)}


class Autoplay(BaseModel):
    on: bool
    interval: float = 2.5


@app.post("/api/worlds/{player_id}/autoplay")
async def set_autoplay(player_id: str, body: Autoplay):
    pid = pid_of(player_id)
    await require_world(pid)
    old = autoplay.pop(pid, None)
    if old:
        old.cancel()
    if body.on:
        async def loop():
            while True:
                await asyncio.sleep(max(0.8, body.interval))
                try:
                    await _run_ticks(pid, 1, 0)
                except Exception:
                    log.exception("autoplay tick failed")
                    return
        autoplay[pid] = asyncio.create_task(loop())
    await hub.publish(pid, {"type": "autoplay", "on": body.on})
    return {"autoplay": body.on}


# --------------------------------------------------------------------------- NPCs & memory
class Talk(BaseModel):
    message: str


@app.post("/api/worlds/{player_id}/npcs/{npc}/talk")
async def talk(player_id: str, npc: str, body: Talk):
    pid = pid_of(player_id)
    await require_world(pid)
    try:
        out = await agent.converse(pid, npc, body.message[:500])
    except T.ToolError as e:
        raise HTTPException(400, str(e))
    await hub.publish(pid, {"type": "say", "npc": npc, "text": out["reply"]})
    return out


@app.get("/api/worlds/{player_id}/npcs/{npc}/memory")
async def memory(player_id: str, npc: str):
    pid = pid_of(player_id)
    await require_world(pid)
    try:
        return await T.memory_graph(pid, npc)
    except T.ToolError as e:
        raise HTTPException(404, str(e))


@app.get("/api/worlds/{player_id}/provenance")
async def provenance(player_id: str, npc: str, fact: str):
    pid = pid_of(player_id)
    await require_world(pid)
    try:
        prov = await T.who_told_me(pid, npc, fact)
    except T.ToolError as e:
        raise HTTPException(404, str(e))
    prov["who_knows"] = await T.who_knows(pid, fact)
    prov["forecast"] = await T.forecast_spread(pid, fact)
    return prov


@app.get("/api/worlds/{player_id}/facts/{fact}")
async def fact_spread(player_id: str, fact: str):
    pid = pid_of(player_id)
    await require_world(pid)
    return {"who_knows": await T.who_knows(pid, fact), "forecast": await T.forecast_spread(pid, fact)}


@app.get("/api/worlds/{player_id}/resolve")
async def resolve(player_id: str, q: str):
    pid = pid_of(player_id)
    await require_world(pid)
    return {"matches": await T.resolve_entity(pid, q)}


@app.get("/api/worlds/{player_id}/analytics")
async def analytics(player_id: str):
    pid = pid_of(player_id)
    await require_world(pid)
    return await T.analytics(pid)


@app.get("/api/worlds/{player_id}/tasks")
async def tasks(player_id: str):
    pid = pid_of(player_id)
    await require_world(pid)
    return {"tasks": await T.task_board(pid)}


# --------------------------------------------------------------------------- concurrency benchmark
class Bench(BaseModel):
    tenants: int = 4
    ticks: int = 6


@app.post("/api/bench")
async def bench(body: Bench):
    """Spin up N isolated worlds and tick them all at once: N x 10 agents reading and writing concurrently."""
    n = max(1, min(body.tenants, 8))
    ticks = max(1, min(body.ticks, 12))
    run = uuid.uuid4().hex[:5]
    pids = [f"bench-{run}-{i}" for i in range(n)]
    try:
        await asyncio.gather(*(sim.create_world(p, f"Bench{i}") for i, p in enumerate(pids)))
        for p in pids:  # same deterministic opening for every world
            await sim.act(p, {"action": "move", "location": "forge"})
            await sim.tick(p)
            await sim.act(p, {"action": "steal", "item": "hammer"})
        w0 = sum(store.stats[p].writes for p in pids)
        q0 = sum(store.stats[p].queries for p in pids)
        lat: list[float] = []
        t0 = time.perf_counter()
        for _ in range(ticks):
            res = await asyncio.gather(*(sim.tick(p) for p in pids))
            lat += [r["metrics"]["tick_ms"] for r in res]
        secs = time.perf_counter() - t0
        writes = sum(store.stats[p].writes for p in pids) - w0
        queries = sum(store.stats[p].queries for p in pids) - q0
        return {"tenants": n, "agents": n * 10, "ticks_per_world": ticks, "seconds": round(secs, 2),
                "writes": writes, "queries": queries, "writes_per_sec": round(writes / secs, 1),
                "queries_per_sec": round(queries / secs, 1), "p50_tick_ms": round(statistics.median(lat), 1),
                "max_tick_ms": round(max(lat), 1)}
    finally:
        await asyncio.gather(*(store.drop(p) for p in pids), return_exceptions=True)


# --------------------------------------------------------------------------- live stream
@app.websocket("/ws/{player_id}")
async def ws(websocket: WebSocket, player_id: str):
    pid = normalize_player_id(player_id)
    await websocket.accept()
    hub.subs.setdefault(pid, set()).add(websocket)
    try:
        while True:
            await websocket.receive_text()  # keepalive pings from the client
    except WebSocketDisconnect:
        pass
    finally:
        hub.subs.get(pid, set()).discard(websocket)


# --------------------------------------------------------------------------- built UI (single-container deploys)
_static = Path(os.getenv("STATIC_DIR") or Path(__file__).resolve().parent.parent / "static")
if _static.is_dir():
    app.mount("/", StaticFiles(directory=_static, html=True), name="ui")
