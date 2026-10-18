"""The memory tool catalogue.

These functions are the *only* way agents (the rule-based sim, Claude, or an
external MCP client) touch memory. Each wraps one or more fixed queries from
``queries.py``; arguments are resolved against the graph first.
"""
from __future__ import annotations

import asyncio
import re
import uuid
from typing import Any

from . import queries as Q
from .db import store
from .world import LOCATIONS, NPC_BY_ID, tick_info


class ToolError(Exception):
    pass


# --------------------------------------------------------------------------- entity resolution
async def resolve_entity(pid: str, name: str) -> list[dict[str, Any]]:
    """Resolve free text ("the blacksmith", "hamer", "kettle") to graph entities."""
    q = (name or "").strip().lower()
    q = re.sub(r"^(the|a|an)\s+", "", q)
    if not q:
        return []
    rows = await store.ro(pid, Q.RESOLVE_EXACT.cypher, {"q": q})
    if rows:
        return _dedupe(rows)
    terms = [t for t in re.findall(r"[a-z]+", q) if len(t) >= 3]
    found: list[dict[str, Any]] = []
    for term in terms:
        for pattern in (f"{term}*", f"%{term}%"):
            for query in (Q.RESOLVE_FULLTEXT_NPC, Q.RESOLVE_FULLTEXT_ITEM, Q.RESOLVE_FULLTEXT_LOC):
                try:
                    found += await store.ro(pid, query.cypher, {"q": pattern})
                except Exception:
                    pass
            if found:
                break
    return _dedupe(found)


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for r in rows:
        if r["id"] not in seen:
            seen.add(r["id"])
            out.append({"id": r["id"], "name": r["name"], "label": r["label"]})
    return out


async def _entity_id(pid: str, entity: str) -> str:
    if entity in NPC_BY_ID or entity == "player" or entity in {l["id"] for l in LOCATIONS}:
        return entity
    hits = await resolve_entity(pid, entity)
    if not hits:
        raise ToolError(f"Nothing in the town is called {entity!r}.")
    return hits[0]["id"]


def _npc(npc: str) -> str:
    if npc not in NPC_BY_ID:
        raise ToolError(f"unknown npc {npc!r}")
    return npc


# --------------------------------------------------------------------------- recall
async def recall_about(pid: str, npc: str, entity: str) -> dict[str, Any]:
    eid = await _entity_id(pid, entity)
    rows = await store.ro(pid, Q.RECALL_ABOUT.cypher, {"npc": _npc(npc), "entity": eid})
    return {"entity": eid, "beliefs": [_belief_row(r) for r in rows]}


async def recall_all(pid: str, npc: str) -> list[dict[str, Any]]:
    return [_belief_row(r) for r in await store.ro(pid, Q.RECALL_ALL.cypher, {"npc": _npc(npc)})]


def _belief_row(r: dict[str, Any]) -> dict[str, Any]:
    out = dict(r)
    if out.get("via") in NPC_BY_ID:
        out["via_name"] = NPC_BY_ID[out["via"]]["name"]
    elif out.get("via") == "player":
        out["via_name"] = "the player"
    if isinstance(out.get("confidence"), float):
        out["confidence"] = round(out["confidence"], 3)
    return out


async def who_told_me(pid: str, npc: str, fact: str) -> dict[str, Any]:
    """Provenance as an explicit graph path, plus a plain-language narrative."""
    rows = await store.ro(pid, Q.PROVENANCE.cypher, {"npc": _npc(npc), "fid": fact})
    if not rows:
        return {"found": False, "narrative": "Nothing in my memory connects to this.", "nodes": [], "edges": []}
    r = rows[0]
    corr = await store.ro(pid, Q.CORROBORATIONS.cypher, {"npc": npc, "fid": fact})
    chain, tellings, event, actor = r["chain"] or [], r["tellings"] or [], r["event"], r["actor"]
    belief, fct, loc = r["belief"] or {}, r["fact"] or {}, r["location"]
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    if actor and event:
        nodes.append({"id": actor["id"], "name": actor["name"], "label": r["actor_label"]})
        nodes.append({"id": event["id"], "name": event["summary"], "label": "Event", "kind": event["kind"],
                      "tick": event["tick"]})
        edges.append({"from": actor["id"], "to": event["id"], "type": (event["kind"] or "did").upper()})
    elif event:
        nodes.append({"id": event["id"], "name": event["summary"], "label": "Event", "kind": event["kind"],
                      "tick": event["tick"]})
    for n in chain:
        nodes.append({"id": n["id"], "name": n["name"], "label": n["label"]})
    if event and chain and chain[0]["label"] == "NPC":
        edges.append({"from": chain[0]["id"], "to": event["id"], "type": "WITNESSED", "tick": r["witnessed_tick"]})
    for a, b, t in zip(chain, chain[1:], tellings):
        edges.append({"from": a["id"], "to": b["id"], "type": "TOLD", "tick": t["tick"],
                      "confidence": round(t["confidence"] or 0, 3), "channel": t["channel"]})
    narrative = _narrate(npc, chain, tellings, event, loc, belief, corr)
    return {"found": True, "fact": fct, "belief": belief, "nodes": nodes, "edges": edges,
            "chain": [n["id"] for n in chain], "corroborations": corr, "narrative": narrative,
            "event": event, "location": loc}


def _when(tick: int | None) -> str:
    if tick is None or tick < 0:
        return "a while back"
    ti = tick_info(tick)
    return f"day {ti['day']}, {ti['slot'].lower()}"


def _narrate(npc, chain, tellings, event, loc, belief, corr) -> str:
    if belief.get("source") == "witnessed":
        where = f" at {loc['name']}" if loc else ""
        return f"I saw it with my own eyes{where}, {_when(event['tick'] if event else None)}."
    if belief.get("source") == "noted":
        return "I made a note of it myself."
    if len(chain) <= 1:
        return "I can't say who told me; nothing in my memory traces it."
    parts = []
    hops = list(zip(chain, chain[1:], tellings))[::-1]  # nearest first
    for i, (src, dst, t) in enumerate(hops):
        who = "I" if i == 0 else dst["name"]
        verb = "heard it from" if i == 0 else "had it from"
        parts.append(f"{who} {verb} {src['name'] if src['label'] != 'Player' else 'you yourself'}"
                     f" ({_when(t['tick'])}, {round((t['confidence'] or 0) * 100)}% sure)")
    origin = chain[0]
    if origin["label"] == "Player":
        tail = "and you are the only source."
    elif event:
        tail = f"and {origin['name']} saw it with their own eyes" + (f" at {loc['name']}" if loc else "") + "."
    else:
        tail = f"and {origin['name']} is where the story starts."
    text = "; ".join(parts) + "; " + tail
    if corr:
        text += " Also confirmed by " + ", ".join(c["name"] for c in corr if c.get("name")) + "."
    return text[0].upper() + text[1:]


async def what_happened_at(pid: str, npc: str, location: str) -> dict[str, Any]:
    loc = await _entity_id(pid, location)
    rows = await store.ro(pid, Q.WHAT_HAPPENED_AT.cypher, {"npc": _npc(npc), "loc": loc})
    if not rows:
        return {"location": loc, "seen": [], "heard": []}
    r = rows[0]
    return {"location": loc, "seen": [s for s in r["seen"] if s.get("summary")],
            "heard": [h for h in r["heard"] if h.get("summary")]}


async def my_relationships(pid: str, npc: str) -> list[dict[str, Any]]:
    return await store.ro(pid, Q.MY_RELATIONSHIPS.cypher, {"npc": _npc(npc)})


async def my_procedures(pid: str, npc: str) -> list[dict[str, Any]]:
    rows = await store.ro(pid, Q.MY_PROCEDURES.cypher, {"npc": _npc(npc)})
    for r in rows:
        if r.get("learned_from") in NPC_BY_ID:
            r["learned_from_name"] = NPC_BY_ID[r["learned_from"]]["name"]
    return rows


async def my_tasks(pid: str, npc: str) -> list[dict[str, Any]]:
    return await store.ro(pid, Q.MEM_TASKS.cypher, {"npc": _npc(npc)})


async def opinion_of_player(pid: str, npc: str) -> dict[str, Any]:
    rows = await store.ro(pid, Q.OPINION_OF_PLAYER.cypher, {"npc": _npc(npc)})
    score = float(rows[0]["score"]) if rows else 0.0
    mood = ("fond of" if score > 0.4 else "warm towards" if score > 0.1 else "wary of" if score < -0.4
            else "uneasy about" if score < -0.1 else "neutral about")
    return {"score": round(score, 3), "facts": rows[0]["facts"] if rows else 0, "mood": mood}


async def record_memory(pid: str, npc: str, about: str, note: str) -> dict[str, Any]:
    eid = await _entity_id(pid, about)
    c = (await store.ro(pid, Q.CLOCK.cypher))[0]
    fid = f"note-{npc}-{uuid.uuid4().hex[:6]}"
    subject_name = "the player" if eid == "player" else NPC_BY_ID.get(eid, {}).get("name", eid)
    await store.rw(pid, Q.NOTE.cypher, {"npc": _npc(npc), "fid": fid, "subject": eid, "subject_name": subject_name,
                                        "claim": note[:240], "valence": 0.0, "tick": c["tick"]})
    if eid == "player":
        await store.rw(pid, Q.NOTE_ABOUT_PLAYER.cypher, {"fid": fid})
    elif eid in NPC_BY_ID:
        await store.rw(pid, Q.NOTE_ABOUT_NPC.cypher, {"fid": fid, "id": eid})
    return {"fact": fid, "stored": True}


async def who_knows(pid: str, fact: str) -> list[dict[str, Any]]:
    return await store.ro(pid, Q.WHO_KNOWS.cypher, {"fid": fact})


async def forecast_spread(pid: str, fact: str) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for r in await store.ro(pid, Q.ALGO_FORECAST.cypher, {"fid": fact}):
        if r["npc"] not in best:  # rows arrive cheapest-first
            best[r["npc"]] = {**r, "distance": round(r["distance"], 3)}
    return list(best.values())[:6]


async def tell(pid: str, speaker: str, listener: str, fact: str) -> dict[str, Any]:
    """Explicit handoff of a belief between two co-located NPCs (used by MCP clients)."""
    from .sim import ACCEPT, HOP_DECAY
    a, b = _npc(speaker), _npc(listener)
    la = await store.ro(pid, Q.WHERE_IS.cypher, {"id": a})
    lb = await store.ro(pid, Q.WHERE_IS.cypher, {"id": b})
    if not la or not lb or la[0]["location"] != lb[0]["location"]:
        raise ToolError(f"{NPC_BY_ID[a]['name']} and {NPC_BY_ID[b]['name']} are not in the same place")
    mine = next((x for x in await recall_all(pid, a) if x["fact"] == fact), None)
    if not mine or mine["stance"] != "accepted":
        raise ToolError(f"{NPC_BY_ID[a]['name']} does not believe that")
    trust = next((r["trust"] for r in await my_relationships(pid, b) if r["id"] == a), 0.25)
    c = (await store.ro(pid, Q.CLOCK.cypher))[0]
    conf = round(mine["confidence"] * trust * HOP_DECAY, 3)
    res = await store.rw(pid, Q.TELL.cypher, {"from": a, "to": b, "fid": fact, "conf": conf, "accept": ACCEPT,
                                              "hop": (mine["hop"] or 0) + 1, "tick": c["tick"], "channel": "mcp"})
    return {"told": True, **(res[0] if res else {})}


async def post_task(pid: str, npc: str, kind: str, fact: str, required: str | None = None,
                    note: str = "") -> dict[str, Any]:
    c = (await store.ro(pid, Q.CLOCK.cypher))[0]
    tid = f"t-{kind}-{uuid.uuid4().hex[:6]}"
    r = await store.rw(pid, Q.POST_TASK.cypher, {"by": _npc(npc), "fid": fact, "tid": tid, "kind": kind,
                                                 "status": "open", "tick": c["tick"], "required": required,
                                                 "target": None, "note": note, "claimed_by": None})
    if not r:
        raise ToolError("could not post task (unknown fact?)")
    return {"task": tid, "status": "open"}


async def claim_task(pid: str, npc: str, task: str) -> dict[str, Any]:
    c = (await store.ro(pid, Q.CLOCK.cypher))[0]
    r = await store.rw(pid, Q.CLAIM_TASK.cypher, {"tid": task, "npc": _npc(npc), "tick": c["tick"]})
    return {"task": task, "claimed": bool(r)}


async def complete_task(pid: str, npc: str, task: str, result: str, handoff_to: str | None = None) -> dict[str, Any]:
    _npc(npc)
    if handoff_to:
        _npc(handoff_to)
    t = next((r for r in await store.ro(pid, Q.TASKS_OPEN.cypher) if r["id"] == task), None)
    if not t:
        return {"task": task, "done": False, "reason": "no such open task"}
    if npc not in (t["claimed_by"], t["posted_by"]):
        raise ToolError(f"{NPC_BY_ID[npc]['name']} neither claimed nor posted task {task!r}")
    c = (await store.ro(pid, Q.CLOCK.cypher))[0]
    r = await store.rw(pid, Q.COMPLETE_TASK.cypher, {"tid": task, "tick": c["tick"], "result": result,
                                                     "handoff": handoff_to})
    return {"task": task, "done": bool(r)}


async def task_board(pid: str) -> list[dict[str, Any]]:
    return await store.ro(pid, Q.TASKS_ALL.cypher)


# --------------------------------------------------------------------------- analytics (graph algorithms)
async def analytics(pid: str) -> dict[str, Any]:
    comm, hubs, pr, reach, talk, wcc = await asyncio.gather(
        store.ro(pid, Q.ALGO_COMMUNITIES.cypher), store.ro(pid, Q.ALGO_BETWEENNESS.cypher),
        store.ro(pid, Q.ALGO_PAGERANK.cypher), store.ro(pid, Q.ALGO_HARMONIC.cypher),
        store.ro(pid, Q.TALKATIVE.cypher), store.ro(pid, Q.ALGO_WCC.cypher))
    # renumber communities 0..k for stable colours
    ids: dict[Any, int] = {}
    for r in sorted(comm, key=lambda r: r["npc"]):
        r["community"] = ids.setdefault(r["community"], len(ids))
    told_any = {r["npc"] for r in talk if r["told"]}
    return {
        "communities": {r["npc"]: r["community"] for r in comm},
        "gossip_hubs": [{"npc": r["npc"], "score": round(r["score"], 3)} for r in hubs],
        "best_informed": [{"npc": r["npc"], "score": round(r["score"], 4)} for r in pr],
        "broadcast_reach": [{"npc": r["npc"], "score": round(r["score"], 3)} for r in reach],
        "most_talkative": talk,
        "rumour_islands": len({r["component"] for r in wcc if r["npc"] in told_any}) if told_any else 0,
    }


async def memory_graph(pid: str, npc: str) -> dict[str, Any]:
    """The NPC's memory subgraph for the inspector (episodic / semantic / procedural / social)."""
    _npc(npc)
    epi, sem, proc, rel, tel, tasks = await asyncio.gather(
        store.ro(pid, Q.MEM_EPISODIC.cypher, {"npc": npc}), store.ro(pid, Q.MEM_SEMANTIC.cypher, {"npc": npc}),
        my_procedures(pid, npc), my_relationships(pid, npc), store.ro(pid, Q.MEM_TELLERS.cypher, {"npc": npc}),
        my_tasks(pid, npc))
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    def node(i, label, name, **kw):
        if i and i not in nodes:
            nodes[i] = {"id": i, "label": label, "name": name, **kw}

    me = NPC_BY_ID[npc]
    node(npc, "Self", me["name"], color=me["color"])
    for e in epi:
        node(e["id"], "Event", e["summary"], kind=e["kind"], tick=e["tick"], vividness=round(e["vividness"] or 0, 2),
             place=e["place"], memory="episodic")
        edges.append({"from": npc, "to": e["id"], "type": "WITNESSED"})
    for f in sem:
        node(f["id"], "Fact", f["claim"], memory="semantic", confidence=round(f["confidence"] or 0, 3),
             stance=f["stance"], hop=f["hop"], via=f["via"], source=f["source"], truth=f["truth"], kind=f["kind"])
        edges.append({"from": npc, "to": f["id"], "type": "BELIEVES", "confidence": round(f["confidence"] or 0, 3),
                      "stance": f["stance"]})
        if f["event"] and f["event"] in nodes:
            edges.append({"from": f["id"], "to": f["event"], "type": "DERIVED_FROM"})
        for a in f["about"]:
            if a.get("id") and a["id"] != npc:
                node(a["id"], a["label"], a["name"] or a["id"], memory="entity")
                edges.append({"from": f["id"], "to": a["id"], "type": "ABOUT", "role": a.get("role")})
    for p in proc:
        node(f"proc-{p['id']}", "Procedure", p["name"], memory="procedural", proficiency=round(p["proficiency"], 2),
             steps=p["steps"], learned_from=p.get("learned_from"))
        edges.append({"from": npc, "to": f"proc-{p['id']}", "type": "KNOWS_HOW", "proficiency": round(p["proficiency"], 2)})
        if p.get("learned_from") in NPC_BY_ID:
            node(p["learned_from"], "NPC", NPC_BY_ID[p["learned_from"]]["name"], memory="social")
            edges.append({"from": p["learned_from"], "to": f"proc-{p['id']}", "type": "TAUGHT"})
    t = tel[0] if tel else {"heard_from": [], "told": []}
    for h in t["heard_from"]:
        if h.get("id") and h.get("fact") in nodes:
            node(h["id"], h["label"] or "NPC", h["name"] or h["id"], memory="social")
            edges.append({"from": h["id"], "to": h["fact"], "type": "TOLD_ME", "tick": h["tick"],
                          "primary": h["primary"]})
    for r in rel[:4]:
        node(r["id"], "NPC", r["name"], memory="social")
        edges.append({"from": npc, "to": r["id"], "type": "KNOWS", "affinity": r["affinity"], "trust": r["trust"]})
    for tk in tasks:
        node(tk["id"], "Task", f"{tk['kind']} ({tk['status']})", memory="coordination", result=tk.get("result"))
        edges.append({"from": tk["id"], "to": npc, "type": tk["rel"]})
        if tk.get("fact") in nodes:
            edges.append({"from": tk["id"], "to": tk["fact"], "type": "CONCERNS"})
    beliefs = [_belief_row({"fact": f["id"], "claim": f["claim"], "kind": f["kind"], "confidence": f["confidence"],
                            "stance": f["stance"], "hop": f["hop"], "via": f["via"], "source": f["source"],
                            "truth": f["truth"], "importance": f["importance"]}) for f in sem]
    beliefs.sort(key=lambda b: -((b["importance"] or 0) * (b["confidence"] or 0)))
    return {"npc": {"id": npc, **{k: me[k] for k in ("name", "role", "personality", "color")}},
            "nodes": list(nodes.values()), "edges": edges, "beliefs": beliefs,
            "episodic": epi, "procedures": proc, "relationships": rel, "tasks": tasks,
            "opinion": await opinion_of_player(pid, npc)}
