"""Simulation engine: world creation, player actions and the multi-agent tick.

Each NPC is an agent whose *entire* memory lives in the tenant's FalkorDB graph.
A tick runs in phases; inside each phase every agent runs concurrently
(``asyncio.gather``), issuing its own reads (GRAPH.RO_QUERY) and writes against
the shared graph:

1. move        -- follow the daily schedule, or a task (report / investigate / confront)
2. gossip      -- perceive company, pick the most salient belief, TELL it (provenance + decay)
3. post        -- guards open investigations, witnesses who know `report_crime` volunteer
4. claim       -- agents race to atomically claim open tasks on the shared board
5. work        -- investigation, reporting and confrontation handoffs
6. teach       -- procedural memory hands off between friends
7. notice      -- owners discover unseen thefts (a crime with no known culprit)
"""
from __future__ import annotations

import asyncio
import random
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from . import queries as Q
from .db import TickTimer, store
from .world import (GUARD_ROLES, ITEMS, LOCATIONS, NPC_BY_ID, NPCS, PROCEDURES, SEED_EVENTS, SKILLS, TICKS_PER_DAY,
                    TOWN_NAME, directed_ties, schedule_location, tick_info)

ACCEPT = 0.3  # confidence needed to accept (not just doubt) a story
HOP_DECAY = 0.9  # each retelling loses 10% on top of the listener's trust in the teller
DAWN_DECAY = 0.93  # hearsay fades a little every dawn
CONFRONT_MIN = 0.4
CASE_MIN = 0.35
DAYTIME = {0, 1, 2, 3, 4}

PREDICATE_VALENCE = {"stole": -1.0, "lied_about": -0.8, "gifted": 0.6, "helped": 0.7, "traded": 0.2,
                     "returned": 0.8, "confronted": -0.3, "chatted": 0.05}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _rng(seed: Any, tick: int, npc: str, purpose: str) -> random.Random:
    return random.Random(f"{seed}:{tick}:{npc}:{purpose}")


def _eid(kind: str, tick: int) -> str:
    return f"e{tick}-{kind}-{uuid.uuid4().hex[:6]}"


class WorldError(Exception):
    pass


BENCH_PREFIX = "bench-"

_locks: dict[str, asyncio.Lock] = {}
autoplay_tasks: dict[str, asyncio.Task] = {}  # server-side auto-play loops, one per world


def lock_for(pid: str) -> asyncio.Lock:
    if pid not in _locks:
        _locks[pid] = asyncio.Lock()
    return _locks[pid]


async def stop_autoplay(pid: str) -> bool:
    """Cancel a world's auto-play loop and wait until it has really stopped."""
    task = autoplay_tasks.pop(pid, None)
    if not task or task.done():
        return False
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return True


async def drop_world(pid: str) -> None:
    """Delete a world safely: stop its auto-play, then wait for any in-flight tick or action.

    Without this, a tick that is still running after the delete re-creates the graph key
    (GRAPH.QUERY on a missing key creates an empty graph) and leaves a broken, empty world behind."""
    await stop_autoplay(pid)
    async with lock_for(pid):
        await store.drop(pid)


async def world_ok(pid: str) -> bool:
    """A world exists *and* is intact (an empty graph left behind by an interrupted delete is not)."""
    if not await store.exists(pid):
        return False
    try:
        return bool(await store.ro(pid, Q.CLOCK.cypher))
    except Exception:
        return False


async def _require(pid: str) -> None:
    if not await store.exists(pid):  # cached: costs a round trip only for an unknown or deleted world
        raise WorldError("world not found")


# =========================================================================== world creation
async def create_world(pid: str, player_name: str = "Wanderer", seed: int = 7) -> None:
    """Build a tenant world in ~5 round trips (the cloud DB is a ~30-60 ms hop away)."""
    await store.drop(pid)
    await store.rw(pid, Q.W_BUILD.cypher, {
        "name": TOWN_NAME, "seed": seed, "player_name": player_name, "now": _now(),
        "locations": LOCATIONS,
        "npcs": [{**{k: n[k] for k in ("id", "name", "role", "personality", "chattiness", "credibility", "color",
                                       "schedule")}, "start": n["schedule"][0]} for n in NPCS],
        "ties": directed_ties(),
        "npc_items": [i for i in ITEMS if i["owner"] != "player"],
        "player_items": [i for i in ITEMS if i["owner"] == "player"],
        "procedures": PROCEDURES,
        "skills": [{"npc": npc, "proc": p, "proficiency": lvl} for npc, sk in SKILLS.items() for p, lvl in sk]})
    store._known.add(pid)
    g = store.graph(pid)

    async def index(stmt: str) -> None:
        try:
            await g.query(stmt)
        except Exception:  # index may exist already
            pass

    # seed memories (the town has a past before the player arrives) while the indexes build
    async def seed_event(se: dict[str, Any]) -> None:
        involved = [{"id": se["actor"], "role": "actor"}]
        if se["target"]:
            involved.append({"id": se["target"], "role": "target"})
        await record_deed(pid, tick=-1, kind=se["kind"], loc=se["location"], eid=se["id"], summary=se["claim"],
                          npc_involved=involved, player_role=None, item=se["item"], witnesses=se["witnesses"],
                          fact=dict(subject=se["actor"], subject_name=NPC_BY_ID[se["actor"]]["name"],
                                    predicate=se["predicate"], object=se["target"] or "",
                                    object_name=NPC_BY_ID[se["target"]]["name"] if se["target"] else "",
                                    claim=se["claim"], truth=True, importance=se["importance"],
                                    valence=se["valence"], kind="deed"),
                          session=0)

    await asyncio.gather(*(index(stmt) for stmt in Q.SCHEMA), *(seed_event(se) for se in SEED_EVENTS))
    await asyncio.gather(*(
        store.rw(pid, Q.TELL.cypher, {"from": via, "to": npc, "fid": f"f-{se['id']}", "conf": conf, "accept": ACCEPT,
                                      "hop": 1, "tick": -1, "channel": "gossip"})
        for se in SEED_EVENTS for npc, (via, conf) in se["hearsay"].items()))


async def prune_worlds(keep: set[str] | None = None) -> list[str]:
    """Free-tier guard: keep at most MAX_WORLDS tenant graphs, deleting the least recently active."""
    from .config import settings
    keep = (keep or set()) | {settings.demo_player}
    tenants = await store.list_tenants()
    idle_cutoff = (datetime.now(timezone.utc) - timedelta(minutes=settings.prune_idle_minutes)).isoformat(
        timespec="seconds")

    async def last_active(t: str) -> str:
        try:
            rows = await store.ro(t, Q.LAST_ACTIVE.cypher)
            return (rows[0]["last_active"] or rows[0]["created_at"] or "") if rows else ""
        except Exception:
            return ""  # empty or broken graph: oldest of all

    # benchmark worlds are dropped right after their run; only leftovers from a crashed run are collected here
    bench = [t for t in tenants if t.startswith(BENCH_PREFIX)]
    players = [t for t in tenants if not t.startswith(BENCH_PREFIX)]
    doomed = [t for t, ts in zip(bench, await asyncio.gather(*(last_active(t) for t in bench))) if ts < idle_cutoff]
    if len(players) > settings.max_worlds:
        ages = sorted(zip(await asyncio.gather(*(last_active(t) for t in players)), players))
        idle = [t for ts, t in ages if t not in keep and ts < idle_cutoff]  # never evict a world in use
        doomed += idle[: len(players) - settings.max_worlds]
    for t in doomed:
        await drop_world(t)
    return doomed


async def world_count() -> int:
    return sum(1 for t in await store.list_tenants() if not t.startswith(BENCH_PREFIX))


async def start_session(pid: str) -> dict[str, Any]:
    rows = await store.rw(pid, Q.START_SESSION.cypher, {"now": _now()})
    return rows[0] if rows else {"n": 1}


async def clock(pid: str) -> dict[str, Any]:
    rows = await store.ro(pid, Q.CLOCK.cypher)
    if not rows:
        raise WorldError("world not found")
    c = rows[0]
    return {**c, **tick_info(c["tick"])}


# =========================================================================== deeds -> memory
async def record_deed(pid: str, *, tick: int, kind: str, loc: str, summary: str, fact: dict[str, Any],
                      witnesses: list[str], npc_involved: list[dict[str, str]] | None = None,
                      player_role: str | None = None, item: str | None = None, eid: str | None = None,
                      session: int | None = None, witness_conf: float = 1.0) -> tuple[str, str]:
    """Episodic Event + its witnesses + a semantic Fact the witnesses now believe."""
    eid = eid or _eid(kind, tick)
    fid = f"f-{eid}"
    ti = tick_info(max(tick, 0))
    params = {"loc": loc, "eid": eid, "fid": fid, "tick": tick, "day": ti["day"] if tick >= 0 else 0,
              "slot": ti["slot"], "kind": kind, "summary": summary,
              "session": session if session is not None else -1,
              **{k: v for k, v in fact.items() if k != "kind"}, "fkind": fact.get("kind", "deed")}
    await store.rw(pid, Q.DEED.cypher, params)
    writes = [_link_about(pid, fid, fact)]
    if npc_involved:
        writes.append(store.rw(pid, Q.INVOLVE_NPC.cypher, {"eid": eid, "rows": npc_involved}))
    if player_role:
        writes.append(store.rw(pid, Q.INVOLVE_PLAYER.cypher, {"eid": eid, "role": player_role}))
    if item:
        writes.append(store.rw(pid, Q.INVOLVE_ITEM.cypher, {"eid": eid, "item": item}))
    if witnesses:
        writes.append(store.rw(pid, Q.WITNESS_BELIEVE.cypher, {"eid": eid, "fid": fid, "ids": witnesses, "tick": tick,
                                                               "conf": witness_conf}))
    results = await asyncio.gather(store.rw(pid, Q.CONTRADICT.cypher, {"fid": fid}), *writes)
    if results[0] and witnesses:
        await asyncio.gather(*(store.rw(pid, Q.RESOLVE_CONFLICT.cypher, {"npc": w, "fid": fid}) for w in witnesses))
    return eid, fid


async def _link_about(pid: str, fid: str, fact: dict[str, Any]) -> None:
    calls = []
    for role, ent in (("subject", fact.get("subject")), ("object", fact.get("object"))):
        if not ent or ent == "someone":
            continue
        if ent == "player":
            calls.append(store.rw(pid, Q.ABOUT_PLAYER.cypher, {"fid": fid, "role": role}))
        elif ent in NPC_BY_ID:
            calls.append(store.rw(pid, Q.ABOUT_NPC.cypher, {"fid": fid, "id": ent, "role": role}))
        else:
            calls.append(store.rw(pid, Q.ABOUT_ITEM.cypher, {"fid": fid, "id": ent, "role": role}))
    await asyncio.gather(*calls)


# =========================================================================== player actions
async def _player(pid: str) -> dict[str, Any]:
    rows = await store.ro(pid, Q.PLAYER_STATE.cypher)
    if not rows:
        raise WorldError("world not found")
    p = rows[0]
    p["items"] = [i for i in p["items"] if i.get("id")]
    return p


async def _present(pid: str, loc: str) -> list[dict[str, Any]]:
    return await store.ro(pid, Q.PRESENT_AT.cypher, {"loc": loc})


async def _opinion(pid: str, npc: str) -> float:
    rows = await store.ro(pid, Q.OPINION_OF_PLAYER.cypher, {"npc": npc})
    return float(rows[0]["score"]) if rows else 0.0


async def _proficiency(pid: str, npc: str, proc: str) -> float:
    rows = await store.ro(pid, Q.MY_PROCEDURES.cypher, {"npc": npc})
    return next((r["proficiency"] for r in rows if r["id"] == proc), 0.0)


async def act(pid: str, action: dict[str, Any]) -> dict[str, Any]:
    async with lock_for(pid):
        await _require(pid)
        return await _act(pid, action)


async def _act(pid: str, a: dict[str, Any]) -> dict[str, Any]:
    kind = a.get("action")
    rows = await store.ro(pid, Q.ACT_CONTEXT.cypher)
    if not rows:
        raise WorldError("world not found")
    ctx = rows[0]
    tick, session = ctx["tick"], ctx.get("sessions") or 1
    player = {"name": ctx["name"], "location": ctx["location"], "items": [i for i in ctx["items"] if i]}
    pname, loc = player["name"], player["location"]
    events: list[dict[str, Any]] = []

    if kind == "move":
        dest = a.get("location")
        if dest not in {l["id"] for l in LOCATIONS}:
            raise WorldError(f"unknown location {dest!r}")
        await store.rw(pid, Q.MOVE_PLAYER.cypher, {"loc": dest})
        return {"ok": True, "message": f"You walk to {_loc_name(dest)}.", "events": [
            {"type": "player_move", "from": loc, "to": dest}]}

    present_ids = sorted(ctx["present"])

    if kind == "steal":
        item_id = a.get("item")
        info = (await store.ro(pid, Q.ITEM_INFO.cypher, {"id": item_id}) or [None])[0]
        if not info:
            raise WorldError(f"unknown item {item_id!r}")
        if info["owner_label"] == "Player":
            raise WorldError("you already have that")
        if info["location"] != loc:
            raise WorldError(f"the {info['name']} is not here; it is kept at {_loc_name(info['location'])}")
        owner, owner_name = info["owner"], info["owner_name"]
        claim = f"{pname} stole {owner_name}'s {info['name']}"
        witnesses = present_ids
        eid, fid = await record_deed(
            pid, tick=tick, kind="theft", loc=loc, summary=claim, witnesses=witnesses,
            npc_involved=[{"id": owner, "role": "victim"}], player_role="actor", item=item_id, session=session,
            fact=dict(subject="player", subject_name=pname, predicate="stole", object=item_id,
                      object_name=f"{owner_name}'s {info['name']}", claim=claim, truth=True, importance=1.0,
                      valence=PREDICATE_VALENCE["stole"], kind="deed"))
        await asyncio.gather(store.rw(pid, Q.TRANSFER_TO_PLAYER.cypher, {"id": item_id}),
                             *([store.rw(pid, Q.MARK_ITEM.cypher, {"id": item_id, "flag": True})] if not witnesses else []))
        events.append({"type": "deed", "kind": "theft", "event": eid, "fact": fid, "claim": claim, "location": loc,
                       "witnesses": witnesses})
        for w in witnesses:
            events.append({"type": "say", "npc": w, "text": _gasp(w, owner, info["name"])})
        msg = (f"You pocket the {info['name']}. " + (
            f"Seen by {', '.join(NPC_BY_ID[w]['name'] for w in witnesses)}." if witnesses else
            "No one saw you... for now."))
        return {"ok": True, "message": msg, "events": events, "fact": fid}

    npc = a.get("npc")
    if kind in ("gift", "help", "trade", "lie", "talk") and npc not in NPC_BY_ID:
        raise WorldError(f"unknown npc {npc!r}")
    if kind in ("gift", "help", "trade", "lie") and npc not in present_ids:
        raise WorldError(f"{NPC_BY_ID[npc]['name']} is not here")
    nname = NPC_BY_ID.get(npc, {}).get("name", "")

    if kind == "gift":
        item_id = a.get("item")
        mine = {i["id"]: i["name"] for i in player["items"]}
        if item_id not in mine:
            raise WorldError("you don't have that item")
        info = (await store.ro(pid, Q.ITEM_INFO.cypher, {"id": item_id}))[0]
        returning = info["original_owner"] == npc
        pred = "returned" if returning else "gifted"
        claim = (f"{pname} returned {nname}'s {mine[item_id]}" if returning
                 else f"{pname} gave {nname} a {mine[item_id]}")
        eid, fid = await record_deed(
            pid, tick=tick, kind=pred, loc=loc, summary=claim, witnesses=present_ids,
            npc_involved=[{"id": npc, "role": "recipient"}], player_role="actor", item=item_id, session=session,
            fact=dict(subject="player", subject_name=pname, predicate=pred, object=npc, object_name=nname,
                      claim=claim, truth=True, importance=0.7 if returning else 0.55,
                      valence=PREDICATE_VALENCE[pred], kind="deed"))
        await asyncio.gather(store.rw(pid, Q.TRANSFER_TO_NPC.cypher, {"id": item_id, "npc": npc}),
                             *([store.rw(pid, Q.MARK_ITEM.cypher, {"id": item_id, "flag": False})] if returning else []))
        events.append({"type": "deed", "kind": pred, "event": eid, "fact": fid, "claim": claim, "location": loc,
                       "witnesses": present_ids})
        events.append({"type": "say", "npc": npc, "text": "My hammer! Well... thank you for bringing it back."
                       if returning and item_id == "hammer" else ("You brought it back? Thank you."
                                                                   if returning else "For me? How kind of you!")})
        return {"ok": True, "message": claim + ".", "events": events, "fact": fid}

    if kind in ("help", "trade"):
        claim = (f"{pname} helped {nname} with the day's work" if kind == "help"
                 else f"{pname} traded fairly with {nname}")
        pred = "helped" if kind == "help" else "traded"
        eid, fid = await record_deed(
            pid, tick=tick, kind=pred, loc=loc, summary=claim, witnesses=present_ids,
            npc_involved=[{"id": npc, "role": "recipient"}], player_role="actor", session=session,
            fact=dict(subject="player", subject_name=pname, predicate=pred, object=npc, object_name=nname,
                      claim=claim, truth=True, importance=0.5 if kind == "help" else 0.3,
                      valence=PREDICATE_VALENCE[pred], kind="deed"))
        events.append({"type": "deed", "kind": pred, "event": eid, "fact": fid, "claim": claim, "location": loc,
                       "witnesses": present_ids})
        events.append({"type": "say", "npc": npc, "text": "Much obliged, friend." if kind == "help"
                       else "A fair deal. Come again."})
        return {"ok": True, "message": claim + ".", "events": events, "fact": fid}

    if kind == "lie":
        return await _lie(pid, a, tick, session, pname, loc, npc, nname, present_ids)

    if kind == "talk":
        from .agent import converse  # local import to avoid a cycle
        if npc not in present_ids:
            raise WorldError(f"{nname} is not here")
        reply = await converse(pid, npc, (a.get("message") or "Hello!")[:500])
        return {"ok": True, "message": reply["reply"], "events": [{"type": "say", "npc": npc, "text": reply["reply"]}],
                "dialogue": reply}

    raise WorldError(f"unknown action {kind!r}")


async def _lie(pid, a, tick, session, pname, loc, npc, nname, present_ids) -> dict[str, Any]:
    """The player tells `npc` that `subject` stole `item`. Creates a claim Fact the player TOLD the npc."""
    subject, item_id = a.get("subject"), a.get("item")
    if subject not in NPC_BY_ID:
        raise WorldError("you can only blame a townsperson")
    info = (await store.ro(pid, Q.ITEM_INFO.cypher, {"id": item_id}) or [None])[0]
    if not info:
        raise WorldError(f"unknown item {item_id!r}")
    orig = info["original_owner"]
    orig_name = NPC_BY_ID[orig]["name"] if orig in NPC_BY_ID else pname
    sname = NPC_BY_ID[subject]["name"]
    claim = f"{sname} stole {orig_name}'s {info['name']}"
    fid = f"claim-{subject}-stole-{item_id}"
    await store.rw(pid, Q.CREATE_FACT.cypher, {
        "fid": fid, "eid": "", "tick": tick, "subject": subject, "subject_name": sname, "predicate": "stole",
        "object": item_id, "object_name": f"{orig_name}'s {info['name']}", "claim": claim, "truth": False,
        "importance": 0.9, "valence": 0.0, "kind": "claim"})
    _, _, opinion, discern = await asyncio.gather(
        _link_about(pid, fid, {"subject": subject, "object": item_id}),
        store.rw(pid, Q.CONTRADICT.cypher, {"fid": fid}), _opinion(pid, npc), _proficiency(pid, npc, "discern_truth"))
    trust = max(0.1, min(0.9, 0.55 + 0.35 * opinion))
    conf = round(trust * (1 - 0.6 * discern), 3)
    res = (await store.rw(pid, Q.PLAYER_TELL.cypher, {"fid": fid, "to": npc, "conf": conf, "accept": ACCEPT,
                                                      "tick": tick}))[0]
    conflicts = await store.rw(pid, Q.RESOLVE_CONFLICT.cypher, {"npc": npc, "fid": fid})
    caught = any(c["winner"] != fid for c in conflicts)
    events: list[dict[str, Any]] = [{"type": "player_tell", "npc": npc, "fact": fid, "claim": claim,
                                     "confidence": conf, "stance": "doubted" if caught else res["stance"]}]
    for cf in conflicts:
        events.append({"type": "conflict", "npc": npc, "fact": cf["fact"], "rival": cf["rival"],
                       "winner": cf["winner"], "c_fact": cf["c_fact"], "c_rival": cf["c_rival"],
                       "claim": cf["fact_claim"], "rival_claim": cf["rival_claim"]})
    if caught:
        # the listener knows better: the lie itself becomes a story about the player
        lie_claim = f"{pname} tried to pin the {info['name']} theft on {sname}"
        eid, lfid = await record_deed(
            pid, tick=tick, kind="caught_lying", loc=loc, summary=lie_claim, witnesses=[npc],
            npc_involved=[{"id": npc, "role": "listener"}, {"id": subject, "role": "accused"}], player_role="actor",
            session=session,
            fact=dict(subject="player", subject_name=pname, predicate="lied_about", object=subject,
                      object_name=sname, claim=lie_claim, truth=True, importance=0.9,
                      valence=PREDICATE_VALENCE["lied_about"], kind="deed"))
        events.append({"type": "deed", "kind": "caught_lying", "event": eid, "fact": lfid, "claim": lie_claim,
                       "location": loc, "witnesses": [npc]})
        events.append({"type": "say", "npc": npc, "text": await _rebuttal(pid, npc, sname, conflicts, fid)})
        msg = f"{nname} doesn't buy it - and now that's a story about you."
    elif res["stance"] == "accepted":
        events.append({"type": "say", "npc": npc, "text": f"{sname}? I never would have guessed..."})
        msg = f"{nname} believes you ({conf:.0%} sure)."
    else:
        events.append({"type": "say", "npc": npc, "text": "Hmm. I'll believe that when I see it."})
        msg = f"{nname} doubts you ({conf:.0%})."
    return {"ok": True, "message": msg, "events": events, "fact": fid, "conflicts": conflicts}


async def _rebuttal(pid: str, npc: str, sname: str, conflicts: list[dict[str, Any]], lie: str) -> str:
    """Why the listener rejects a lie, in terms of how it knows the version it holds instead."""
    winner = next((c["winner"] for c in conflicts if c["winner"] != lie), None)
    mine = next((r for r in await store.ro(pid, Q.RECALL_ALL.cypher, {"npc": npc}) if r["fact"] == winner), {})
    if mine.get("source") == "witnessed":
        return f"{sname}? I know what I saw. Don't take me for a fool."
    via = mine.get("via")
    if via in NPC_BY_ID:
        return f"{sname}? That's not what {NPC_BY_ID[via]['name']} told me, and I trust them more than you."
    if via == "player":
        return f"{sname}? That's not what you told me before."
    return f"{sname}? That's not what I heard. Don't take me for a fool."


def _loc_name(loc: str | None) -> str:
    return next((l["name"] for l in LOCATIONS if l["id"] == loc), loc or "somewhere")


def _gasp(witness: str, owner: str, item: str) -> str:
    if witness == owner:
        return f"Oi! That's my {item}!"
    if NPC_BY_ID[witness]["role"] in GUARD_ROLES:
        return "Stop, thief!"
    return f"Did they just take the {item}?!"


# =========================================================================== the tick
async def tick(pid: str) -> dict[str, Any]:
    async with lock_for(pid):
        await _require(pid)  # never write into a world deleted while this tick waited for the lock
        return await _tick(pid)


async def _tick(pid: str) -> dict[str, Any]:
    """One simulated hour.

    Every phase runs all ten agents concurrently, and each agent needs at most one or two
    round trips per phase, so a tick is ~6 round trips deep no matter how many agents act:
      1. advance the clock + read the shared board (parallel)
      2. move: everyone at once (destinations planned from schedules and the task board)
      3. perceive: one read per agent (company, salient beliefs, who already knows what, skills)
      4. act: gossip / post tasks / work claimed tasks / teach / notice thefts (parallel writes)
      5. claim: agents race for open tasks; the database decides the single winner
    """
    t0 = time.perf_counter()
    with TickTimer(store, pid) as timer:
        clk, snap = await asyncio.gather(store.rw(pid, Q.ADVANCE_AND_READ.cypher, {"now": _now()}),
                                         store.ro(pid, Q.TICK_SNAPSHOT.cypher))
        if not clk or not snap:
            raise WorldError("world not found")
        c, board = clk[0], snap[0]
        tick_no, seed, session = c["tick"], c["seed"], c.get("sessions") or 1
        ti = tick_info(tick_no)
        tasks = [{**t, "fact": t.get("fact_id")} for t in board["tasks"] if t]
        events: list[dict[str, Any]] = []
        agents = [NPCAgent(pid, n["id"], tick_no, seed, tasks, session) for n in NPCS]

        # 2. move
        dest = plan_moves(agents, board["player_loc"], tick_no)
        side = []
        if ti["slot_index"] == 0:
            side = [store.rw(pid, Q.DECAY.cypher, {"factor": DAWN_DECAY}),
                    store.rw(pid, Q.DECAY_EPISODIC.cypher, {"factor": 0.9})]
            events.append({"type": "dawn", "day": ti["day"]})
        moved = await asyncio.gather(*(a.move(*dest[a.id]) for a in agents), *side)
        for evs in moved[:len(agents)]:
            events.extend(evs)

        # 3. perceive
        await asyncio.gather(*(a.perceive() for a in agents))

        # 4. act (+ owners noticing unseen thefts)
        results = await asyncio.gather(*(a.act() for a in agents),
                                       notice_unseen_thefts(pid, tick_no, session, board["unseen"], dest),
                                       return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                events.append({"type": "error", "phase": "act", "error": repr(r)})
            else:
                events.extend(r)

        # 5. claim
        open_tasks = [t for t in tasks if t["status"] == "open"] + [t for a in agents for t in a.posted
                                                                     if t["status"] == "open"]
        if open_tasks:
            results = await asyncio.gather(*(a.claim(open_tasks) for a in agents), return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    events.append({"type": "error", "phase": "claim", "error": repr(r)})
                else:
                    events.extend(r)
    ms = (time.perf_counter() - t0) * 1000
    metrics = {"tick_ms": round(ms, 1), "writes": timer.writes, "queries": timer.queries,
               "writes_per_sec": round(timer.writes / (ms / 1000), 1) if ms else 0, "agents": len(agents),
               "concurrency": len(agents)}
    return {**ti, "events": events, "metrics": metrics}


def plan_moves(agents: list["NPCAgent"], player_loc: str, tick_no: int) -> dict[str, tuple[str, str | None]]:
    """Where every agent goes this hour: its schedule, or wherever its task leads (shared board state)."""
    dest: dict[str, tuple[str, str | None]] = {}
    for a in agents:
        t = a.override_task()
        if not t:
            dest[a.id] = (schedule_location(a.id, tick_no), None)
        elif t["kind"] == "confront":
            dest[a.id] = (player_loc, "confront")
    for _ in range(2):  # reporters/investigators head to where their target will be (targets may move too)
        for a in agents:
            t = a.override_task()
            if t and t["kind"] in ("report", "investigate"):
                target = t["target"]
                if target == a.id or target not in NPC_BY_ID:
                    dest[a.id] = (schedule_location(a.id, tick_no), None)
                else:
                    dest[a.id] = (dest.get(target, (schedule_location(target, tick_no), None))[0], t["kind"])
    return dest


async def notice_unseen_thefts(pid: str, tick_no: int, session: int, unseen: list[dict[str, Any]],
                               dest: dict[str, tuple[str, str | None]]) -> list[dict[str, Any]]:
    out = []
    for row in [u for u in unseen if u]:
        owner = row["owner"]
        if owner not in dest or dest[owner][0] != row["location"]:
            continue
        present = sorted(n for n, (loc, _) in dest.items() if loc == row["location"])
        owner_name = NPC_BY_ID[owner]["name"]
        claim = f"Someone stole {owner_name}'s {row['item_name']}"
        eid, fid = await record_deed(
            pid, tick=tick_no, kind="missing", loc=row["location"], summary=claim, witnesses=present,
            npc_involved=[{"id": owner, "role": "victim"}], item=row["item"], session=session,
            fact=dict(subject="someone", subject_name="someone", predicate="stole", object=row["item"],
                      object_name=f"{owner_name}'s {row['item_name']}", claim=claim, truth=True,
                      importance=0.8, valence=0.0, kind="deed"))
        await store.rw(pid, Q.MARK_ITEM.cypher, {"id": row["item"], "flag": False})
        out.append({"type": "deed", "kind": "missing", "event": eid, "fact": fid, "claim": claim,
                    "location": row["location"], "witnesses": present})
        out.append({"type": "say", "npc": owner, "text": f"My {row['item_name']}... it's gone!"})
    return out


class NPCAgent:
    """One NPC's turn. Everything it knows comes from the graph; everything it does is written back."""

    def __init__(self, pid: str, npc: str, tick: int, seed: Any, tasks: list[dict[str, Any]], session: int = 1):
        self.pid, self.id, self.tick, self.seed, self.session = pid, npc, tick, seed, session
        self.meta = NPC_BY_ID[npc]
        self.slot = tick % TICKS_PER_DAY
        self.board = tasks
        self.my_tasks = [t for t in tasks if t.get("claimed_by") == npc and t["status"] == "claimed"]
        self.is_guard = self.meta["role"] in GUARD_ROLES
        self.view: dict[str, Any] = {"location": None, "company": [], "player_here": False, "beliefs": [], "procs": []}
        self.posted: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ helpers
    def rng(self, purpose: str) -> random.Random:
        return _rng(self.seed, self.tick, self.id, purpose)

    def override_task(self) -> dict[str, Any] | None:
        for t in self.my_tasks:
            if t["kind"] == "confront" and self.slot in DAYTIME:
                return t
            if t["kind"] in ("report", "investigate") and t.get("target"):
                return t
        return None

    async def ro(self, query: Q.Q, **params):
        return await store.ro(self.pid, query.cypher, params)

    async def rw(self, query: Q.Q, **params):
        return await store.rw(self.pid, query.cypher, params)

    def tasks_for(self, fact: str) -> list[dict[str, Any]]:
        return [t for t in self.board if t.get("fact") == fact]

    @property
    def company_ids(self) -> set[str]:
        return {o["id"] for o in self.view["company"]}

    @property
    def proc_ids(self) -> set[str]:
        return {p["id"] for p in self.view["procs"]}

    # ------------------------------------------------------------------ move / perceive
    async def move(self, dest: str, reason: str | None) -> list[dict[str, Any]]:
        moved = await self.rw(Q.MOVE_NPC, id=self.id, loc=dest)
        if moved:
            return [{"type": "move", "npc": self.id, "from": moved[0]["from"], "to": dest, "reason": reason}]
        return []

    async def perceive(self) -> None:
        rows = await self.ro(Q.PERCEIVE, id=self.id)
        if rows:
            v = rows[0]
            v["company"] = [o for o in v["company"] if o]
            v["beliefs"] = [b for b in v["beliefs"] if b]
            self.view = v

    async def act(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for evs in await asyncio.gather(self.gossip(), self.post(), self.work(), self.teach()):
            out.extend(evs)
        return out

    # ------------------------------------------------------------------ gossip
    async def gossip(self) -> list[dict[str, Any]]:
        company, beliefs = self.view["company"], self.view["beliefs"]
        if not company or not beliefs or self.rng("chatty").random() > self.meta["chattiness"]:
            return []
        best, best_score = None, 0.0
        for b in beliefs:
            crime = b["predicate"] in ("stole", "lied_about")
            holders = set(b["holders"] or [])
            for o in company:
                if o["id"] in holders or o["id"] == b["subject"]:
                    continue
                to_guard = o["role"] in GUARD_ROLES
                if self.is_guard and crime and not to_guard:
                    continue  # guards keep open cases confidential
                if o["affinity"] < (0.15 if crime and to_guard else 0.3):
                    continue
                fresh = 1.0 / (1.0 + max(0, self.tick - (b["since"] or 0)) / 6.0)
                score = (b["importance"] * b["confidence"] * fresh * o["affinity"]
                         * (1.6 if crime and to_guard else 1.0) * (1.3 if b["subject"] == "player" else 1.0))
                if score > best_score:
                    best, best_score = (b, o), score
        if not best:
            return []
        b, o = best
        conf = round(b["confidence"] * o["trust_in_me"] * HOP_DECAY, 3)
        res = await self.rw(Q.TELL_RESOLVED, **{"from": self.id}, to=o["id"], fid=b["fact"], conf=conf, accept=ACCEPT,
                            hop=(b["hop"] or 0) + 1, tick=self.tick, channel="gossip")
        if not res:
            return []
        r = res[0]
        conflicts = await self.rw(Q.RESOLVE_CONFLICT, npc=o["id"], fid=b["fact"]) if r["contested"] else []
        stance = r["stance"]
        if conflicts:  # report the stance the listener ends up with, after weighing the rival claims
            stance = "accepted" if conflicts[0]["winner"] == b["fact"] else "doubted"
        evs = [{"type": "tell", "from": self.id, "to": o["id"], "fact": b["fact"], "claim": b["claim"],
                "confidence": r["confidence"], "stance": stance, "hop": r["hop"],
                "first_time": r["first_time"], "location": self.view["location"]},
               {"type": "say", "npc": self.id, "text": _gossip_line(b["claim"], o["id"])}]
        for cf in conflicts:
            evs.append({"type": "conflict", "npc": o["id"], "fact": cf["fact"], "rival": cf["rival"],
                        "winner": cf["winner"], "c_fact": cf["c_fact"], "c_rival": cf["c_rival"],
                        "claim": cf["fact_claim"], "rival_claim": cf["rival_claim"]})
        return evs

    # ------------------------------------------------------------------ post tasks on the shared board
    async def _post(self, *, tid: str, kind: str, fact: str, claim: str, status: str, required: str | None,
                    target: str | None, note: str) -> list[dict[str, Any]]:
        claimed_by = self.id if status == "claimed" else None
        r = await self.rw(Q.POST_TASK, by=self.id, fid=fact, tid=tid, kind=kind, status=status, tick=self.tick,
                          required=required, target=target, note=note, claimed_by=claimed_by)
        if not (r and r[0]["created"]):
            return []
        self.posted.append({"id": tid, "kind": kind, "status": status, "fact": fact, "required": required,
                            "posted_by": self.id, "claimed_by": claimed_by, "target": target, "note": note})
        evs = [{"type": "task_posted", "task": tid, "kind": kind, "by": self.id, "claim": claim}]
        if kind == "investigate":
            evs.append({"type": "say", "npc": self.id, "text": "This needs looking into. Finn!"})
        elif kind == "report":
            evs.append({"type": "say", "npc": self.id, "text": "The captain needs to hear about this."})
        return evs

    async def post(self) -> list[dict[str, Any]]:
        jobs = []
        crimes = [b for b in self.view["beliefs"] if b["predicate"] in ("stole", "lied_about")]
        if self.meta["role"] == "guard":
            for b in crimes:
                if b["confidence"] < CASE_MIN:
                    continue
                kinds = {t["kind"] for t in self.tasks_for(b["fact"])}
                if "investigate" not in kinds:
                    jobs.append(self._post(tid=f"t-inv-{b['fact']}", kind="investigate", fact=b["fact"],
                                           claim=b["claim"], status="open", required="investigate", target=None,
                                           note=f"Find who really saw: {b['claim']}"))
                if b["subject"] == "player" and "confront" not in kinds and b["confidence"] >= CONFRONT_MIN:
                    jobs.append(self._post(tid=f"t-con-{b['fact']}", kind="confront", fact=b["fact"],
                                           claim=b["claim"], status="claimed", required=None, target="player",
                                           note=f"Confront the player: {b['claim']}"))
        elif not self.is_guard and "report_crime" in self.proc_ids:
            for b in crimes:
                if b["confidence"] >= 0.5 and not self.tasks_for(b["fact"]):
                    jobs.append(self._post(tid=f"t-rep-{b['fact']}-{self.id}", kind="report", fact=b["fact"],
                                           claim=b["claim"], status="claimed", required="report_crime",
                                           target="oswin", note=f"Report to the captain: {b['claim']}"))
        out: list[dict[str, Any]] = []
        for evs in await asyncio.gather(*jobs):
            out.extend(evs)
        return out

    # ------------------------------------------------------------------ claim (races are resolved by the DB)
    async def claim(self, open_tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self.my_tasks:
            return []  # busy
        evs: list[dict[str, Any]] = []
        for t in open_tasks:
            if t["required"] in self.proc_ids and t["posted_by"] != self.id:
                won = await self.rw(Q.CLAIM_AND_TRACE, tid=t["id"], npc=self.id, tick=self.tick)
                if won:
                    evs.append({"type": "task_claimed", "task": t["id"], "kind": t["kind"], "by": self.id})
                    evs.append({"type": "say", "npc": self.id, "text": "I'll take the case, Captain."})
                    if won[0]["witness"]:
                        evs.append({"type": "task_progress", "task": t["id"], "by": self.id,
                                    "target": won[0]["witness"],
                                    "note": f"traced the story back to eyewitness {won[0]['witness_name']}"})
                    break
                evs.append({"type": "claim_lost", "task": t["id"], "by": self.id})
        return evs

    # ------------------------------------------------------------------ work claimed tasks
    async def work(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        jobs = []
        for t in self.my_tasks:
            if t["kind"] == "investigate":
                jobs.append(self._investigate(t))
            elif t["kind"] == "report":
                jobs.append(self._report(t))
            elif t["kind"] == "confront" and self.view["player_here"] and self.slot in DAYTIME:
                jobs.append(self._confront(t))
        for evs in await asyncio.gather(*jobs):
            out.extend(evs)
        return out

    async def _investigate(self, t: dict[str, Any]) -> list[dict[str, Any]]:
        target, boss = t.get("target"), t["posted_by"]
        if not target:
            await self.rw(Q.COMPLETE_TASK, tid=t["id"], tick=self.tick, result="No eyewitness in anyone's memory.",
                          handoff=boss)
            return [{"type": "task_done", "task": t["id"], "by": self.id, "result": "no eyewitness"}]
        if target != self.id and target not in self.company_ids:
            return []
        evs: list[dict[str, Any]] = []
        wname = NPC_BY_ID[target]["name"]
        testimony = None
        if target != self.id:
            res, info = await asyncio.gather(
                self.rw(Q.TELL_RESOLVED, **{"from": target}, to=self.id, fid=t["fact"], conf=0.95, accept=ACCEPT,
                        hop=1, tick=self.tick, channel="testimony"),
                self.ro(Q.FACT_INFO, fid=t["fact"]))
            if not res:  # the eyewitness no longer holds the story: there is nothing to confirm
                await self.rw(Q.COMPLETE_TASK, tid=t["id"], tick=self.tick,
                              result=f"Eyewitness {wname} could not confirm it.", handoff=boss)
                return [{"type": "task_done", "task": t["id"], "by": self.id, "result": "eyewitness could not confirm"}]
            testimony = res[0]
            claim = info[0]["fact"].get("claim", t["note"]) if info else t["note"]
            evs.append({"type": "tell", "from": target, "to": self.id, "fact": t["fact"], "claim": claim,
                        "confidence": testimony["confidence"], "stance": testimony["stance"], "hop": testimony["hop"],
                        "first_time": testimony["first_time"], "channel": "testimony"})
            evs.append({"type": "say", "npc": target, "text": "Aye, I saw it with my own eyes."})
        else:
            info = await self.ro(Q.FACT_INFO, fid=t["fact"])
            claim = info[0]["fact"].get("claim", t["note"]) if info else t["note"]
        # findings: the boss's belief is raised to the eyewitness level, then the hand-over is recorded as a TOLD edge
        before = await self.rw(Q.CORROBORATE, npc=boss, fid=t["fact"], conf=0.95, by=self.id)
        result = f"Eyewitness {wname} confirmed it."
        told, _ = await asyncio.gather(
            self.rw(Q.TELL_RESOLVED, **{"from": self.id}, to=boss, fid=t["fact"], conf=0.95, accept=ACCEPT,
                    hop=((testimony or {}).get("hop") or 0) + 1, tick=self.tick, channel="findings"),
            self.rw(Q.COMPLETE_TASK, tid=t["id"], tick=self.tick, result=result, handoff=boss))
        if not told:
            return evs + [{"type": "task_done", "task": t["id"], "by": self.id, "result": "findings not delivered"}]
        evs.append({"type": "tell", "from": self.id, "to": boss, "fact": t["fact"], "claim": claim,
                    "confidence": told[0]["confidence"], "from_confidence": before[0]["before"] if before else None,
                    "stance": told[0]["stance"], "hop": told[0]["hop"], "first_time": told[0]["first_time"],
                    "channel": "findings"})
        evs.append({"type": "task_done", "task": t["id"], "by": self.id, "result": result, "handoff": boss})
        evs.append({"type": "handoff", "from": self.id, "to": boss, "task": t["id"], "kind": "findings"})
        return evs

    async def _report(self, t: dict[str, Any]) -> list[dict[str, Any]]:
        if any(x["kind"] == "investigate" for x in self.tasks_for(t["fact"])):
            await self.rw(Q.COMPLETE_TASK, tid=t["id"], tick=self.tick, result="Already under investigation.",
                          handoff=None)
            return [{"type": "task_done", "task": t["id"], "by": self.id, "result": "already under investigation"}]
        guard = t["target"]
        if guard not in self.company_ids:
            return []
        mine = next((b for b in self.view["beliefs"] if b["fact"] == t["fact"]), None)
        conf = round((mine["confidence"] if mine else 0.8) * 0.95, 3)
        res, _ = await asyncio.gather(
            self.rw(Q.TELL_RESOLVED, **{"from": self.id}, to=guard, fid=t["fact"], conf=conf, accept=ACCEPT,
                    hop=((mine or {}).get("hop") or 0) + 1, tick=self.tick, channel="report"),
            self.rw(Q.COMPLETE_TASK, tid=t["id"], tick=self.tick, result="Reported to the captain.", handoff=guard))
        claim = mine["claim"] if mine else t["note"]
        if not res:  # the reporter no longer holds the story, so nothing was passed on
            return [{"type": "task_done", "task": t["id"], "by": self.id, "result": "nothing to report"}]
        return [{"type": "tell", "from": self.id, "to": guard, "fact": t["fact"], "claim": claim,
                 "confidence": res[0]["confidence"], "stance": res[0]["stance"], "hop": res[0]["hop"],
                 "first_time": res[0]["first_time"], "channel": "report"},
                {"type": "say", "npc": self.id, "text": "Captain, you need to hear this."},
                {"type": "task_done", "task": t["id"], "by": self.id, "result": "reported", "handoff": guard},
                {"type": "handoff", "from": self.id, "to": guard, "task": t["id"], "kind": "report"}]

    async def _confront(self, t: dict[str, Any]) -> list[dict[str, Any]]:
        loc = self.view["location"]
        prov, info, clk = await asyncio.gather(self.ro(Q.PROVENANCE, npc=self.id, fid=t["fact"]),
                                               self.ro(Q.FACT_INFO, fid=t["fact"]), self.ro(Q.CLOCK))
        chain = [n["name"] for n in (prov[0]["chain"] if prov else [])][:-1]
        info = info[0]["fact"] if info else {}
        if prov and (prov[0]["belief"] or {}).get("source") == "witnessed":
            how = "I saw it with my own eyes"
        elif len(chain) >= 2:
            how = f"{chain[-1]} told me, and it traces back to {chain[0]}, who saw it"
        elif chain:
            how = f"{chain[0]} saw it and told me"
        else:
            how = "I have it on good authority"
        line = f"You there! {how}. They say you took {info.get('object_name', 'something')}. Care to explain?"
        pname = clk[0]["player_name"]
        present = sorted(self.company_ids | {self.id})
        claim = f"Captain Oswin confronted {pname} about {info.get('object_name', 'a theft')}"
        (eid, _), _ = await asyncio.gather(
            record_deed(self.pid, tick=self.tick, kind="confrontation", loc=loc, summary=claim, witnesses=present,
                        npc_involved=[{"id": self.id, "role": "accuser"}], player_role="accused", session=self.session,
                        fact=dict(subject="player", subject_name=pname, predicate="confronted", object=self.id,
                                  object_name=self.meta["name"], claim=claim, truth=True, importance=0.6,
                                  valence=PREDICATE_VALENCE["confronted"], kind="deed")),
            self.rw(Q.COMPLETE_TASK, tid=t["id"], tick=self.tick, result="Confronted the player.", handoff=None))
        return [{"type": "confront", "npc": self.id, "fact": t["fact"], "event": eid, "text": line, "location": loc},
                {"type": "say", "npc": self.id, "text": line, "long": True},
                {"type": "task_done", "task": t["id"], "by": self.id, "result": "confronted"}]

    # ------------------------------------------------------------------ teach (procedural handoff)
    async def teach(self) -> list[dict[str, Any]]:
        if self.rng("teach").random() > 0.12:
            return []
        friends = [o for o in self.view["company"] if o["affinity"] >= 0.6]
        mine = [p for p in self.view["procs"] if p["teachable"] and p["proficiency"] >= 0.7]
        for proc in mine:
            for f in friends:
                if proc["id"] not in set(f["procs"] or []):
                    r = await self.rw(Q.TEACH, teacher=self.id, learner=f["id"], proc=proc["id"], tick=self.tick)
                    if r:
                        return [{"type": "teach", "from": self.id, "to": f["id"], "procedure": proc["id"],
                                 "name": proc["name"]},
                                {"type": "say", "npc": self.id, "text": f"Here, let me show you: {proc['name'].lower()}."}]
        return []


def _gossip_line(claim: str, listener: str) -> str:
    if NPC_BY_ID[listener]["role"] in GUARD_ROLES and ("stole" in claim or "pin" in claim):
        return f"Captain, you'll want to hear this: {claim}."
    return f"Did you hear? {claim}!"


# =========================================================================== state for the UI
async def state(pid: str) -> dict[str, Any]:
    c = await clock(pid)
    (locs, npcs, ties, player, tasks, events, rep) = await asyncio.gather(
        store.ro(pid, Q.STATE_LOCATIONS.cypher), store.ro(pid, Q.STATE_NPCS.cypher),
        store.ro(pid, Q.STATE_TIES.cypher), _player(pid), store.ro(pid, Q.TASKS_ALL.cypher),
        store.ro(pid, Q.RECENT_EVENTS.cypher), store.ro(pid, Q.TOWN_REPUTATION.cypher))
    items_here = await store.ro(pid, Q.ITEMS_AT.cypher, {"loc": player["location"]})
    repmap = {r["npc"]: round(r["score"], 2) for r in rep}
    for n in npcs:
        n["opinion"] = repmap.get(n["id"], 0.0)
    st = store.stats[pid]
    return {"player_id": pid, "clock": c, "locations": locs, "npcs": npcs, "ties": ties, "player": player,
            "items_here": [i for i in items_here if i["owner"] != "player"], "tasks": tasks, "events": events,
            "metrics": {"tick_ms": round(st.last_tick_ms, 1), "writes": st.last_tick_writes,
                        "queries": st.last_tick_queries, "total_writes": st.writes, "total_queries": st.queries,
                        "writes_per_sec": round(st.last_tick_writes / (st.last_tick_ms / 1000), 1)
                        if st.last_tick_ms else 0, "agents": len(npcs), "history": st.tick_history}}
