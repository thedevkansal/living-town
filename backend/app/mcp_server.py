"""MCP server (stdio): exposes the town's graph memory tools to any MCP client.

Run:  uv run --directory backend python -m app.mcp_server

Every tool takes ``player_id`` (the tenant -> graph ``town:{player_id}``), so an
external agent can only ever touch one isolated world per call. The tools are
the same fixed, parameterised Cypher catalogue the in-game agents use; clients
never send Cypher.
"""
from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import agent, sim
from . import tools as T
from .db import normalize_player_id, store

server = MCPServer(
    name="living-town-memory",
    instructions=(
        "Graph memory for a town of NPC agents (FalkorDB). Use list_npcs first, resolve_entity for any name, then "
        "recall_about / who_told_me to explain beliefs with their provenance path. All tools need player_id."),
)


async def _pid(player_id: str) -> str:
    pid = normalize_player_id(player_id)
    if not await store.exists(pid):
        raise T.ToolError(f"no world for {pid!r}; call create_world first")
    return pid


def _j(x: Any) -> str:
    return json.dumps(x, default=str, indent=1)


@server.tool()
async def create_world(player_id: str, player_name: str = "Wanderer", reset: bool = False) -> str:
    """Create (or join) the isolated town world for a player id."""
    pid = normalize_player_id(player_id)
    async with sim.lock_for(pid):
        if reset or not await sim.world_ok(pid):
            await sim.create_world(pid, player_name[:24] or "Wanderer")
            await sim.prune_worlds(keep={pid})
        s = await sim.start_session(pid)
    return _j({"player_id": pid, "session": s["n"], "clock": await sim.clock(pid)})


@server.tool()
async def list_npcs(player_id: str) -> str:
    """NPC agents with their role, location and opinion of the player."""
    st = await sim.state(await _pid(player_id))
    return _j({"clock": st["clock"], "npcs": st["npcs"], "player": st["player"]})


@server.tool()
async def resolve_entity(player_id: str, name: str) -> str:
    """Resolve free text (e.g. 'the blacksmith', 'hamer') to town entity ids. Call before other tools."""
    return _j(await T.resolve_entity(await _pid(player_id), name))


@server.tool()
async def recall_about(player_id: str, npc: str, entity: str) -> str:
    """What one NPC believes about an entity ('player' = the human player), with confidence and source."""
    return _j(await T.recall_about(await _pid(player_id), npc, entity))


@server.tool()
async def who_told_me(player_id: str, npc: str, fact_id: str) -> str:
    """Provenance path for an NPC's belief: actor -> event <- witness -TOLD-> ... -> npc."""
    return _j(await T.who_told_me(await _pid(player_id), npc, fact_id))


@server.tool()
async def what_happened_at(player_id: str, npc: str, location: str) -> str:
    """Episodic recall: what an NPC saw or heard happened at a location."""
    return _j(await T.what_happened_at(await _pid(player_id), npc, location))


@server.tool()
async def my_relationships(player_id: str, npc: str) -> str:
    """An NPC's social edges (trust, affinity, bond)."""
    return _j(await T.my_relationships(await _pid(player_id), npc))


@server.tool()
async def my_procedures(player_id: str, npc: str) -> str:
    """An NPC's procedural memory (skills, proficiency, who taught them)."""
    return _j(await T.my_procedures(await _pid(player_id), npc))


@server.tool()
async def record_memory(player_id: str, npc: str, about: str, note: str) -> str:
    """Write a note into an NPC's semantic memory, linked to the entity it is about."""
    return _j(await T.record_memory(await _pid(player_id), npc, about, note))


@server.tool()
async def tell(player_id: str, speaker: str, listener: str, fact_id: str) -> str:
    """Hand a belief from one NPC to another co-located NPC (records a TOLD provenance edge)."""
    return _j(await T.tell(await _pid(player_id), speaker, listener, fact_id))


@server.tool()
async def who_knows(player_id: str, fact_id: str) -> str:
    """Everyone who holds a fact, ordered by hops from the source."""
    return _j(await T.who_knows(await _pid(player_id), fact_id))


@server.tool()
async def forecast_spread(player_id: str, fact_id: str) -> str:
    """Who is likely to hear a story next (weighted shortest paths over the social graph)."""
    return _j(await T.forecast_spread(await _pid(player_id), fact_id))


@server.tool()
async def task_board(player_id: str) -> str:
    """The shared coordination board (investigations, reports, confrontations)."""
    return _j(await T.task_board(await _pid(player_id)))


@server.tool()
async def post_task(player_id: str, npc: str, kind: str, fact_id: str, required_procedure: str = "investigate",
                    note: str = "") -> str:
    """Post an open task concerning a fact; only NPCs who know `required_procedure` can claim it."""
    return _j(await T.post_task(await _pid(player_id), npc, kind, fact_id, required_procedure, note))


@server.tool()
async def claim_task(player_id: str, npc: str, task_id: str) -> str:
    """Atomically claim an open task (fails if someone else got it first)."""
    return _j(await T.claim_task(await _pid(player_id), npc, task_id))


@server.tool()
async def complete_task(player_id: str, npc: str, task_id: str, result: str, handoff_to: str = "") -> str:
    """Complete a task and optionally hand the result to another NPC."""
    return _j(await T.complete_task(await _pid(player_id), npc, task_id, result, handoff_to or None))


@server.tool()
async def town_analytics(player_id: str) -> str:
    """Graph algorithms: cliques (label propagation), gossip hubs (betweenness), best informed (PageRank)."""
    return _j(await T.analytics(await _pid(player_id)))


@server.tool()
async def talk_to(player_id: str, npc: str, message: str) -> str:
    """Say something to an NPC; it answers from its graph memory."""
    return _j(await agent.converse(await _pid(player_id), npc, message[:agent.MAX_MESSAGE]))


@server.tool()
async def advance(player_id: str, ticks: int = 1) -> str:
    """Advance the simulation (all NPC agents act concurrently each tick)."""
    pid = await _pid(player_id)
    out = [await sim.tick(pid) for _ in range(max(1, min(ticks, 12)))]
    return _j([{"tick": t["tick"], "slot": t["slot"], "metrics": t["metrics"],
                "events": [e for e in t["events"] if e["type"] not in ("move", "say")]} for t in out])


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
