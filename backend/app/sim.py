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
from datetime import datetime, timezone
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


_locks: dict[str, asyncio.Lock] = {}


def lock_for(pid: str) -> asyncio.Lock:
    if pid not in _locks:
        _locks[pid] = asyncio.Lock()
    return _locks[pid]


# =========================================================================== world creation
async def create_world(pid: str, player_name: str = "Wanderer", seed: int = 7) -> None:
    await store.drop(pid)
    g = store.graph(pid)
    for stmt in Q.SCHEMA:
        try:
            await g.query(stmt)
        except Exception:  # index may exist already
            pass
    await store.rw(pid, Q.W_TOWN.cypher, {"name": TOWN_NAME, "seed": seed, "player_name": player_name,
                                          "now": _now()})
    await store.rw(pid, Q.W_LOCATIONS.cypher, {"rows": LOCATIONS})
    await store.rw(pid, Q.W_NPCS.cypher, {"rows": [
        {**{k: n[k] for k in ("id", "name", "role", "personality", "chattiness", "credibility", "color", "schedule")},
         "start": n["schedule"][0]} for n in NPCS]})
    await store.rw(pid, Q.W_PLAYER.cypher, {"name": player_name})
    await store.rw(pid, Q.W_TIES.cypher, {"rows": directed_ties()})
    await store.rw(pid, Q.W_ITEMS_NPC.cypher, {"rows": [i for i in ITEMS if i["owner"] != "player"]})
    await store.rw(pid, Q.W_ITEMS_PLAYER.cypher, {"rows": [i for i in ITEMS if i["owner"] == "player"]})
    await store.rw(pid, Q.W_PROCEDURES.cypher, {"rows": PROCEDURES})
    await store.rw(pid, Q.W_SKILLS.cypher, {"rows": [
        {"npc": npc, "proc": p, "proficiency": lvl} for npc, sk in SKILLS.items() for p, lvl in sk]})
    # seed memories: the town has a past before the player arrives
    for se in SEED_EVENTS:
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
        fid = f"f-{se['id']}"
        for npc, (via, conf) in se["hearsay"].items():
            await store.rw(pid, Q.TELL.cypher, {"from": via, "to": npc, "fid": fid, "conf": conf, "accept": ACCEPT,
                                                "hop": 1, "tick": -1, "channel": "gossip"})


async def prune_worlds(keep: set[str] | None = None) -> list[str]:
    """Free-tier guard: keep at most MAX_WORLDS tenant graphs, deleting the least recently active."""
    from .config import settings
    keep = (keep or set()) | {settings.demo_player}
    tenants = await store.list_tenants()
    if len(tenants) <= settings.max_worlds:
        return []
    ages = []
    for t in tenants:
        try:
            rows = await store.ro(t, Q.LAST_ACTIVE.cypher)
            ages.append((rows[0]["last_active"] or rows[0]["created_at"] or "") if rows else "", t)
        except Exception:
            ages.append(("", t))
    ages.sort()
    doomed = [t for _, t in ages if t not in keep][: max(0, len(tenants) - settings.max_worlds)]
    for t in doomed:
        await store.drop(t)
    return doomed


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
    ti = tick_info(max(tick, 0))
    await store.rw(pid, Q.CREATE_EVENT.cypher, {"loc": loc, "eid": eid, "tick": tick, "day": ti["day"] if tick >= 0 else 0,
                                                "slot": ti["slot"], "kind": kind, "summary": summary,
                                                "session": session if session is not None else -1})
    if npc_involved:
        await store.rw(pid, Q.INVOLVE_NPC.cypher, {"eid": eid, "rows": npc_involved})
    if player_role:
        await store.rw(pid, Q.INVOLVE_PLAYER.cypher, {"eid": eid, "role": player_role})
    if item:
        await store.rw(pid, Q.INVOLVE_ITEM.cypher, {"eid": eid, "item": item})
    if witnesses:
        await store.rw(pid, Q.WITNESS.cypher, {"eid": eid, "ids": witnesses, "tick": tick})
    fid = f"f-{eid}"
    await store.rw(pid, Q.CREATE_FACT.cypher, {"fid": fid, "eid": eid, "tick": tick, **fact})
    await _link_about(pid, fid, fact)
    if witnesses:
        await store.rw(pid, Q.BELIEVE.cypher, {"fid": fid, "tick": tick, "rows": [
            {"id": w, "conf": witness_conf, "hop": 0, "via": None, "source": "witnessed"} for w in witnesses]})
    rivals = await store.rw(pid, Q.CONTRADICT.cypher, {"fid": fid})
    for w in witnesses:
        if rivals:
            await store.rw(pid, Q.RESOLVE_CONFLICT.cypher, {"npc": w, "fid": fid})
    return eid, fid


async def _link_about(pid: str, fid: str, fact: dict[str, Any]) -> None:
    for role, ent in (("subject", fact.get("subject")), ("object", fact.get("object"))):
        if not ent or ent == "someone":
            continue
        if ent == "player":
            await store.rw(pid, Q.ABOUT_PLAYER.cypher, {"fid": fid, "role": role})
        elif ent in NPC_BY_ID:
            await store.rw(pid, Q.ABOUT_NPC.cypher, {"fid": fid, "id": ent, "role": role})
        else:
            await store.rw(pid, Q.ABOUT_ITEM.cypher, {"fid": fid, "id": ent, "role": role})


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
        return await _act(pid, action)


async def _act(pid: str, a: dict[str, Any]) -> dict[str, Any]:
    kind = a.get("action")
    c = await clock(pid)
    tick, session = c["tick"], c.get("sessions") or 1
    player = await _player(pid)
    pname, loc = player["name"], player["location"]
    events: list[dict[str, Any]] = []

    if kind == "move":
        dest = a.get("location")
        if dest not in {l["id"] for l in LOCATIONS}:
            raise WorldError(f"unknown location {dest!r}")
        await store.rw(pid, Q.MOVE_PLAYER.cypher, {"loc": dest})
        return {"ok": True, "message": f"You walk to {_loc_name(dest)}.", "events": [
            {"type": "player_move", "from": loc, "to": dest}]}

    present = await _present(pid, loc)
    present_ids = [p["id"] for p in present]

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
        await store.rw(pid, Q.TRANSFER_TO_PLAYER.cypher, {"id": item_id})
        if not witnesses:
            await store.rw(pid, Q.MARK_ITEM.cypher, {"id": item_id, "flag": True})
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
        await store.rw(pid, Q.TRANSFER_TO_NPC.cypher, {"id": item_id, "npc": npc})
        if returning:
            await store.rw(pid, Q.MARK_ITEM.cypher, {"id": item_id, "flag": False})
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
        reply = await converse(pid, npc, a.get("message") or "Hello!")
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
    await _link_about(pid, fid, {"subject": subject, "object": item_id})
    await store.rw(pid, Q.CONTRADICT.cypher, {"fid": fid})
    trust = max(0.1, min(0.9, 0.55 + 0.35 * await _opinion(pid, npc)))
    discern = await _proficiency(pid, npc, "discern_truth")
    conf = round(trust * (1 - 0.6 * discern), 3)
    res = (await store.rw(pid, Q.PLAYER_TELL.cypher, {"fid": fid, "to": npc, "conf": conf, "accept": ACCEPT,
                                                      "tick": tick}))[0]
    conflicts = await store.rw(pid, Q.RESOLVE_CONFLICT.cypher, {"npc": npc, "fid": fid})
    events: list[dict[str, Any]] = [{"type": "player_tell", "npc": npc, "fact": fid, "claim": claim,
                                     "confidence": conf, "stance": res["stance"]}]
    caught = any(c["winner"] != fid for c in conflicts)
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
        events.append({"type": "say", "npc": npc, "text": f"{sname}? I know what I saw. Don't take me for a fool."})
        msg = f"{nname} doesn't buy it - and now that's a story about you."
    elif res["stance"] == "accepted":
        events.append({"type": "say", "npc": npc, "text": f"{sname}? I never would have guessed..."})
        msg = f"{nname} believes you ({conf:.0%} sure)."
    else:
        events.append({"type": "say", "npc": npc, "text": "Hmm. I'll believe that when I see it."})
        msg = f"{nname} doubts you ({conf:.0%})."
    return {"ok": True, "message": msg, "events": events, "fact": fid, "conflicts": conflicts}


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
        return await _tick(pid)


async def _tick(pid: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    with TickTimer(store, pid) as timer:
        tick_no = (await store.rw(pid, Q.ADVANCE_CLOCK.cypher, {"now": _now()}))[0]["tick"]
        c = await clock(pid)
        seed = c["seed"]
        ti = tick_info(tick_no)
        events: list[dict[str, Any]] = []
        if ti["slot_index"] == 0:
            await store.rw(pid, Q.DECAY.cypher, {"factor": DAWN_DECAY})
            await store.rw(pid, Q.DECAY_EPISODIC.cypher, {"factor": 0.9})
            events.append({"type": "dawn", "day": ti["day"]})
        tasks = await store.ro(pid, Q.TASKS_OPEN.cypher)
        agents = [NPCAgent(pid, n["id"], tick_no, seed, tasks) for n in NPCS]

        # 1. move: schedule-followers first, then task-driven agents who read others' positions
        free = [a for a in agents if not a.override_task()]
        driven = [a for a in agents if a.override_task()]
        for batch in (free, driven):
            for evs in await asyncio.gather(*(a.move() for a in batch)):
                events.extend(evs)
        # 2..7: each phase concurrently across all agents
        for phase in ("gossip", "post", "claim", "work", "teach"):
            results = await asyncio.gather(*(getattr(a, phase)() for a in agents), return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    events.append({"type": "error", "phase": phase, "error": repr(r)})
                else:
                    events.extend(r)
        events.extend(await notice_unseen_thefts(pid, tick_no, c.get("sessions") or 1))
    ms = (time.perf_counter() - t0) * 1000
    metrics = {"tick_ms": round(ms, 1), "writes": timer.writes, "queries": timer.queries,
               "writes_per_sec": round(timer.writes / (ms / 1000), 1) if ms else 0, "agents": len(agents),
               "concurrency": len(agents)}
    return {**ti, "events": events, "metrics": metrics}


async def notice_unseen_thefts(pid: str, tick_no: int, session: int) -> list[dict[str, Any]]:
    out = []
    for row in await store.ro(pid, Q.UNSEEN_THEFTS.cypher):
        present = [p["id"] for p in await _present(pid, row["location"])]
        claim = f"Someone stole {row['owner_name']}'s {row['item_name']}"
        eid, fid = await record_deed(
            pid, tick=tick_no, kind="missing", loc=row["location"], summary=claim, witnesses=present,
            npc_involved=[{"id": row["owner"], "role": "victim"}], item=row["item"], session=session,
            fact=dict(subject="someone", subject_name="someone", predicate="stole", object=row["item"],
                      object_name=f"{row['owner_name']}'s {row['item_name']}", claim=claim, truth=True,
                      importance=0.8, valence=0.0, kind="deed"))
        await store.rw(pid, Q.MARK_ITEM.cypher, {"id": row["item"], "flag": False})
        out.append({"type": "deed", "kind": "missing", "event": eid, "fact": fid, "claim": claim,
                    "location": row["location"], "witnesses": present})
        out.append({"type": "say", "npc": row["owner"], "text": f"My {row['item_name']}... it's gone!"})
    return out


class NPCAgent:
    """One NPC's turn. All state is read from / written to the graph."""

    def __init__(self, pid: str, npc: str, tick: int, seed: Any, tasks: list[dict[str, Any]]):
        self.pid, self.id, self.tick, self.seed = pid, npc, tick, seed
        self.meta = NPC_BY_ID[npc]
        self.slot = tick % TICKS_PER_DAY
        self.my_tasks = [t for t in tasks if t.get("claimed_by") == npc and t["status"] == "claimed"]
        self.is_guard = self.meta["role"] in GUARD_ROLES

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

    async def context(self) -> dict[str, Any]:
        rows = await self.ro(Q.AGENT_CONTEXT, id=self.id)
        return rows[0] if rows else {"location": None, "company": [], "player_here": False}

    # ------------------------------------------------------------------ 1. move
    async def move(self) -> list[dict[str, Any]]:
        dest = schedule_location(self.id, self.tick)
        reason = None
        t = self.override_task()
        if t:
            if t["kind"] == "confront":
                dest = (await self.ro(Q.WHERE_IS, id="player"))[0]["location"]
                reason = "confront"
            else:
                rows = await self.ro(Q.WHERE_IS, id=t["target"])
                if rows:
                    dest, reason = rows[0]["location"], t["kind"]
        moved = await self.rw(Q.MOVE_NPC, id=self.id, loc=dest)
        if moved:
            return [{"type": "move", "npc": self.id, "from": moved[0]["from"], "to": dest, "reason": reason}]
        return []

    # ------------------------------------------------------------------ 2. gossip
    async def gossip(self) -> list[dict[str, Any]]:
        ctx = await self.context()
        company = ctx["company"]
        if not company or self.rng("chatty").random() > self.meta["chattiness"]:
            return []
        beliefs = await self.ro(Q.AGENT_BELIEFS, id=self.id)
        if not beliefs:
            return []
        known = await self.ro(Q.WHO_BELIEVES_AMONG, ids=[o["id"] for o in company], facts=[b["fact"] for b in beliefs])
        known_pairs = {(k["npc"], k["fact"]) for k in known}
        best, best_score = None, 0.0
        for b in beliefs:
            crime = b["predicate"] in ("stole", "lied_about")
            for o in company:
                if (o["id"], b["fact"]) in known_pairs or o["id"] == b["subject"]:
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
        res = await self.rw(Q.TELL, **{"from": self.id}, to=o["id"], fid=b["fact"], conf=conf, accept=ACCEPT,
                            hop=(b["hop"] or 0) + 1, tick=self.tick, channel="gossip")
        if not res:
            return []
        await self.rw(Q.PROMOTE, npc=o["id"], fid=b["fact"], accept=ACCEPT)
        conflicts = await self.rw(Q.RESOLVE_CONFLICT, npc=o["id"], fid=b["fact"])
        evs = [{"type": "tell", "from": self.id, "to": o["id"], "fact": b["fact"], "claim": b["claim"],
                "confidence": res[0]["confidence"], "stance": res[0]["stance"], "hop": res[0]["hop"],
                "first_time": res[0]["first_time"], "location": ctx["location"]},
               {"type": "say", "npc": self.id, "text": _gossip_line(b["claim"], o["id"])}]
        for cf in conflicts:
            evs.append({"type": "conflict", "npc": o["id"], "fact": cf["fact"], "rival": cf["rival"],
                        "winner": cf["winner"], "c_fact": cf["c_fact"], "c_rival": cf["c_rival"],
                        "claim": cf["fact_claim"], "rival_claim": cf["rival_claim"]})
        return evs

    # ------------------------------------------------------------------ 3. post
    async def post(self) -> list[dict[str, Any]]:
        evs: list[dict[str, Any]] = []
        if self.meta["role"] == "guard":
            for case in await self.ro(Q.GUARD_CASES, guard=self.id, min_conf=CASE_MIN):
                if not case["investigation"]:
                    tid = f"t-inv-{case['fact']}"
                    r = await self.rw(Q.POST_TASK, by=self.id, fid=case["fact"], tid=tid, kind="investigate",
                                      status="open", tick=self.tick, required="investigate", target=None,
                                      note=f"Find who really saw: {case['claim']}", claimed_by=None)
                    if r and r[0]["created"]:
                        evs.append({"type": "task_posted", "task": tid, "kind": "investigate", "by": self.id,
                                    "claim": case["claim"]})
                        evs.append({"type": "say", "npc": self.id, "text": "This needs looking into. Finn!"})
                if (case["subject"] == "player" and not case["confrontation"]
                        and case["confidence"] >= CONFRONT_MIN):
                    tid = f"t-con-{case['fact']}"
                    r = await self.rw(Q.POST_TASK, by=self.id, fid=case["fact"], tid=tid, kind="confront",
                                      status="claimed", tick=self.tick, required=None, target="player",
                                      note=f"Confront the player: {case['claim']}", claimed_by=self.id)
                    if r and r[0]["created"]:
                        await self.rw(Q.SELF_ASSIGN, tid=tid, npc=self.id)
                        evs.append({"type": "task_posted", "task": tid, "kind": "confront", "by": self.id,
                                    "claim": case["claim"]})
        elif not self.is_guard:
            procs = {p["id"] for p in await self.ro(Q.MY_PROCEDURES, npc=self.id)}
            if "report_crime" in procs:
                for crime in await self.ro(Q.CRIME_BELIEFS, id=self.id):
                    if await self.ro(Q.TASKS_FOR_FACT, fid=crime["fact"]):
                        continue  # someone is already on it (shared state)
                    tid = f"t-rep-{crime['fact']}-{self.id}"
                    r = await self.rw(Q.POST_TASK, by=self.id, fid=crime["fact"], tid=tid, kind="report",
                                      status="claimed", tick=self.tick, required="report_crime", target="oswin",
                                      note=f"Report to the captain: {crime['claim']}", claimed_by=self.id)
                    if r and r[0]["created"]:
                        await self.rw(Q.SELF_ASSIGN, tid=tid, npc=self.id)
                        evs.append({"type": "task_posted", "task": tid, "kind": "report", "by": self.id,
                                    "claim": crime["claim"]})
                        evs.append({"type": "say", "npc": self.id, "text": "The captain needs to hear about this."})
        return evs

    # ------------------------------------------------------------------ 4. claim (races are resolved by the DB)
    async def claim(self) -> list[dict[str, Any]]:
        evs: list[dict[str, Any]] = []
        open_tasks = [t for t in await self.ro(Q.TASKS_OPEN) if t["status"] == "open"]
        if not open_tasks:
            return evs
        procs = {p["id"] for p in await self.ro(Q.MY_PROCEDURES, npc=self.id)}
        for t in open_tasks:
            if t["required"] in procs and t["posted_by"] != self.id:
                won = await self.rw(Q.CLAIM_TASK, tid=t["id"], npc=self.id, tick=self.tick)
                if won:
                    evs.append({"type": "task_claimed", "task": t["id"], "kind": t["kind"], "by": self.id})
                    evs.append({"type": "say", "npc": self.id, "text": "I'll take the case, Captain."})
                    break
                evs.append({"type": "claim_lost", "task": t["id"], "by": self.id})
        return evs

    # ------------------------------------------------------------------ 5. work
    async def work(self) -> list[dict[str, Any]]:
        mine = [t for t in await self.ro(Q.TASKS_OPEN) if t.get("claimed_by") == self.id and t["status"] == "claimed"]
        if not mine:
            return []
        evs: list[dict[str, Any]] = []
        ctx = await self.context()
        here = {o["id"] for o in ctx["company"]}
        for t in mine:
            if t["kind"] == "investigate":
                evs.extend(await self._investigate(t, here))
            elif t["kind"] == "report":
                evs.extend(await self._report(t, here))
            elif t["kind"] == "confront" and ctx["player_here"] and self.slot in DAYTIME:
                evs.extend(await self._confront(t, ctx["location"]))
        return evs

    async def _investigate(self, t: dict[str, Any], here: set[str]) -> list[dict[str, Any]]:
        if not t.get("target"):
            w = await self.ro(Q.FIRST_WITNESS, fid=t["fact"])
            if not w:
                await self.rw(Q.COMPLETE_TASK, tid=t["id"], tick=self.tick, result="No eyewitness in anyone's memory.",
                              handoff=t["posted_by"])
                return [{"type": "task_done", "task": t["id"], "by": self.id, "result": "no eyewitness"}]
            witness = w[0]
            await self.rw(Q.SET_TASK_TARGET, tid=t["id"], target=witness["id"],
                          note=f"Story traced back to eyewitness {witness['name']}")
            await self.rw(Q.TASK_FOUND, tid=t["id"], npc=witness["id"])
            if witness["id"] != self.id:
                return [{"type": "task_progress", "task": t["id"], "by": self.id, "target": witness["id"],
                         "note": f"traced the story back to eyewitness {witness['name']}"}]
            t = {**t, "target": witness["id"]}
        target = t["target"]
        if target != self.id and target not in here:
            return []
        evs: list[dict[str, Any]] = []
        if target != self.id:
            res = await self.rw(Q.TELL, **{"from": target}, to=self.id, fid=t["fact"], conf=0.95, accept=ACCEPT,
                                hop=1, tick=self.tick, channel="testimony")
            if res:
                evs.append({"type": "tell", "from": target, "to": self.id, "fact": t["fact"], "claim": t["note"],
                            "confidence": res[0]["confidence"], "stance": res[0]["stance"], "hop": res[0]["hop"],
                            "first_time": res[0]["first_time"], "channel": "testimony"})
                evs.append({"type": "say", "npc": target, "text": "Aye, I saw it with my own eyes."})
        boss = t["posted_by"]
        await self.rw(Q.TELL, **{"from": self.id}, to=boss, fid=t["fact"], conf=0.95, accept=ACCEPT, hop=2,
                      tick=self.tick, channel="findings")
        await self.rw(Q.CORROBORATE, npc=boss, fid=t["fact"], conf=0.95, by=self.id)
        result = f"Eyewitness {NPC_BY_ID[target]['name']} confirmed it."
        await self.rw(Q.COMPLETE_TASK, tid=t["id"], tick=self.tick, result=result, handoff=boss)
        evs.append({"type": "task_done", "task": t["id"], "by": self.id, "result": result, "handoff": boss})
        evs.append({"type": "handoff", "from": self.id, "to": boss, "task": t["id"], "kind": "findings"})
        return evs

    async def _report(self, t: dict[str, Any], here: set[str]) -> list[dict[str, Any]]:
        existing = await self.ro(Q.TASKS_FOR_FACT, fid=t["fact"])
        if any(x["kind"] == "investigate" for x in existing):
            await self.rw(Q.COMPLETE_TASK, tid=t["id"], tick=self.tick, result="Already under investigation.",
                          handoff=None)
            return [{"type": "task_done", "task": t["id"], "by": self.id, "result": "already under investigation"}]
        guard = t["target"]
        if guard not in here:
            return []
        mine = next((b for b in await self.ro(Q.AGENT_BELIEFS, id=self.id) if b["fact"] == t["fact"]), None)
        conf = round((mine["confidence"] if mine else 0.8) * 0.95, 3)
        res = await self.rw(Q.TELL, **{"from": self.id}, to=guard, fid=t["fact"], conf=conf, accept=ACCEPT,
                            hop=((mine or {}).get("hop") or 0) + 1, tick=self.tick, channel="report")
        await self.rw(Q.COMPLETE_TASK, tid=t["id"], tick=self.tick, result="Reported to the captain.", handoff=guard)
        return [{"type": "tell", "from": self.id, "to": guard, "fact": t["fact"], "claim": t["note"],
                 "confidence": res[0]["confidence"] if res else conf, "stance": res[0]["stance"] if res else "accepted",
                 "hop": res[0]["hop"] if res else 1, "first_time": res[0]["first_time"] if res else True,
                 "channel": "report"},
                {"type": "say", "npc": self.id, "text": "Captain, you need to hear this."},
                {"type": "task_done", "task": t["id"], "by": self.id, "result": "reported", "handoff": guard},
                {"type": "handoff", "from": self.id, "to": guard, "task": t["id"], "kind": "report"}]

    async def _confront(self, t: dict[str, Any], loc: str) -> list[dict[str, Any]]:
        prov = await self.ro(Q.PROVENANCE, npc=self.id, fid=t["fact"])
        chain = [n["name"] for n in (prov[0]["chain"] if prov else [])][:-1]
        info = (await self.ro(Q.FACT_INFO, fid=t["fact"]))[0]["fact"]
        if len(chain) >= 2:
            how = f"{chain[-1]} told me, and it traces back to {chain[0]}, who saw it"
        elif chain:
            how = f"{chain[0]} saw it and told me"
        else:
            how = "I have it on good authority"
        line = f"You there! {how}. They say you took {info.get('object_name', 'something')}. Care to explain?"
        pname = (await self.ro(Q.CLOCK))[0]["player_name"]
        present = [p["id"] for p in await _present(self.pid, loc)]
        claim = f"Captain Oswin confronted {pname} about {info.get('object_name', 'a theft')}"
        eid, fid = await record_deed(
            self.pid, tick=self.tick, kind="confrontation", loc=loc, summary=claim, witnesses=present,
            npc_involved=[{"id": self.id, "role": "accuser"}], player_role="accused",
            fact=dict(subject="player", subject_name=pname, predicate="confronted", object=self.id,
                      object_name=self.meta["name"], claim=claim, truth=True, importance=0.6,
                      valence=PREDICATE_VALENCE["confronted"], kind="deed"))
        await self.rw(Q.COMPLETE_TASK, tid=t["id"], tick=self.tick, result="Confronted the player.", handoff=None)
        return [{"type": "confront", "npc": self.id, "fact": t["fact"], "event": eid, "text": line, "location": loc},
                {"type": "say", "npc": self.id, "text": line, "long": True},
                {"type": "task_done", "task": t["id"], "by": self.id, "result": "confronted"}]

    # ------------------------------------------------------------------ 6. teach (procedural handoff)
    async def teach(self) -> list[dict[str, Any]]:
        if self.rng("teach").random() > 0.12:
            return []
        ctx = await self.context()
        friends = [o for o in ctx["company"] if o["affinity"] >= 0.6]
        if not friends:
            return []
        mine = await self.ro(Q.TEACHABLE, id=self.id)
        if not mine:
            return []
        skills = {r["id"]: set(r["procs"]) for r in await self.ro(Q.SKILLS_OF, ids=[f["id"] for f in friends])}
        for proc in mine:
            for f in friends:
                if proc["id"] not in skills.get(f["id"], set()):
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
