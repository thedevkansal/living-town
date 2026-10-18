"""Integration tests against the live FalkorDB (FALKORDB_HOST/PORT, default localhost:6381)."""
import asyncio

from app import agent, sim
from app import queries as Q
from app import tools as T
from app.db import graph_name, store

from .conftest import run_theft


async def counts(pid):
    nodes = {r["label"]: r["n"] for r in await store.ro(pid, Q.GRAPH_COUNTS.cypher)}
    edges = {r["type"]: r["n"] for r in await store.ro(pid, Q.EDGE_COUNTS.cypher)}
    return nodes, edges


# --------------------------------------------------------------------------- world generation
async def test_world_generation(world):
    nodes, edges = await counts(world)
    assert nodes["NPC"] == 10 and nodes["Location"] == 8 and nodes["Player"] == 1
    assert nodes["Procedure"] >= 8 and nodes["Item"] == 8
    assert nodes["Event"] == 3 and nodes["Fact"] == 3  # seeded town history
    assert edges["KNOWS"] == 42  # 21 ties, both directions
    assert edges["IS_AT"] == 11  # every NPC + the player placed exactly once
    assert edges["KNOWS_HOW"] >= 15 and edges["WITNESSED"] >= 8
    c = await sim.clock(world)
    assert c["tick"] == 0 and c["slot"] == "Dawn" and c["sessions"] == 1


async def test_entity_resolution(world):
    assert (await T.resolve_entity(world, "the blacksmith"))[0]["id"] == "bram"
    assert (await T.resolve_entity(world, "Kettle"))[0]["id"] == "tavern"
    assert (await T.resolve_entity(world, "hamer"))[0]["id"] == "hammer"  # fuzzy full-text
    assert await T.resolve_entity(world, "dragon") == []


# --------------------------------------------------------------------------- gossip propagation
async def test_witnesses_form_episodic_memory(world):
    fid = await run_theft(world)
    rows = await store.ro(world, "MATCH (n:NPC)-[:WITNESSED]->(:Event)<-[:DERIVED_FROM]-(:Fact {id:$f}) "
                                 "RETURN n.id AS id", {"f": fid})
    assert [r["id"] for r in rows] == ["bram"]
    bram = await T.recall_about(world, "bram", "player")
    assert any(b["fact"] == fid and b["source"] == "witnessed" and b["confidence"] == 1.0 for b in bram["beliefs"])


async def test_gossip_spreads_with_decay(world):
    fid = await run_theft(world)
    for _ in range(4):  # midday .. night
        await sim.tick(world)
    knows = {r["npc"]: r for r in await T.who_knows(world, fid)}
    assert "oswin" in knows, "the guard never saw the theft but should have heard of it"
    assert knows["bram"]["hop"] == 0 and knows["oswin"]["hop"] >= 2
    assert knows["bram"]["confidence"] > knows["mira"]["confidence"] > 0.3
    # exactly one primary TOLD in-edge per believer -> provenance is a tree
    primaries = await store.ro(world, "MATCH ()-[t:TOLD {fact_id:$f}]->(b:NPC) WHERE t.primary "
                                      "RETURN b.id AS b, count(t) AS n", {"f": fid})
    assert primaries and all(r["n"] == 1 for r in primaries)


async def test_guard_coordination_and_confrontation(world):
    await run_theft(world)
    events = []
    for _ in range(5):  # through to day-2 dawn
        events += (await sim.tick(world))["events"]
    kinds = [e["type"] for e in events]
    assert "task_posted" in kinds and "task_claimed" in kinds and "handoff" in kinds
    claimed = next(e for e in events if e["type"] == "task_claimed")
    assert claimed["by"] == "finn"  # the captain delegates; the deputy claims via KNOWS_HOW investigate
    assert any(e["type"] == "confront" and e["npc"] == "oswin" for e in events)
    findings = next(e for e in events if e["type"] == "tell" and e.get("channel") == "findings")
    assert findings["to"] == "oswin" and findings["from_confidence"] < 0.9 <= findings["confidence"]
    # the confrontation is part of this play session's history
    rows = await store.ro(world, "MATCH (e:Event {kind:'confrontation'})-[:DURING]->(s:Session) RETURN count(e) AS n")
    assert rows[0]["n"] >= 1


async def test_lie_rebuttal_names_the_real_source(world):
    fid = await run_theft(world)
    for _ in range(4):
        await sim.tick(world)
        mira = {b["fact"]: b for b in await T.recall_all(world, "mira")}
        if fid in mira:
            break
    assert mira[fid]["source"] == "told"
    where = (await store.ro(world, Q.WHERE_IS.cypher, {"id": "mira"}))[0]["location"]
    await sim.act(world, {"action": "move", "location": where})
    out = await sim.act(world, {"action": "lie", "npc": "mira", "subject": "pip", "item": "hammer"})
    said = next(e["text"] for e in out["events"] if e["type"] == "say" and e["npc"] == "mira")
    assert "I know what I saw" not in said
    assert f"{T.NPC_BY_ID[mira[fid]['via']]['name']} told me" in said


async def test_long_message_does_not_fan_out(world):
    q0 = store.stats[world].queries
    await agent.converse(world, "mira", "xyzzyplugh quuxword " * 2000)
    assert store.stats[world].queries - q0 < 60


async def test_only_the_claimer_completes_a_task(world):
    fid = await run_theft(world)
    t = await T.post_task(world, "oswin", "investigate", fid, "investigate")
    assert (await T.claim_task(world, "finn", t["task"]))["claimed"]
    try:
        await T.complete_task(world, "pip", t["task"], "done")
        raise AssertionError("pip must not complete finn's task")
    except T.ToolError:
        pass
    assert (await T.complete_task(world, "finn", t["task"], "done", "oswin"))["done"]


# --------------------------------------------------------------------------- provenance
async def test_provenance_path(world):
    fid = await run_theft(world)
    for _ in range(3):  # midday, afternoon, evening
        await sim.tick(world)
    prov = await T.who_told_me(world, "oswin", fid)
    assert prov["found"]
    assert prov["chain"] == ["bram", "mira", "oswin"]
    types = [(e["from"], e["type"], e["to"]) for e in prov["edges"]]
    assert ("player", "THEFT", prov["event"]["id"]) in types
    assert ("bram", "WITNESSED", prov["event"]["id"]) in types
    assert ("bram", "TOLD", "mira") in types and ("mira", "TOLD", "oswin") in types
    assert "Mira" in prov["narrative"] and "Bram" in prov["narrative"]


async def test_honest_when_nothing_connects(world):
    fid = await run_theft(world)
    prov = await T.who_told_me(world, "agnes", fid)  # Agnes never heard it
    assert not prov["found"] and "Nothing in my memory" in prov["narrative"]
    reply = await agent.converse(world, "agnes", "What do you know about the silver hammer?")
    assert "Nothing in my memory" in reply["reply"]


async def test_unseen_theft_has_no_culprit(world):
    for _ in range(4):  # evening: the bakery is empty
        await sim.tick(world)
    assert await sim._present(world, "bakery") == []
    await sim.act(world, {"action": "move", "location": "bakery"})
    out = await sim.act(world, {"action": "steal", "item": "loaf"})
    assert "No one saw you" in out["message"]
    events = []
    for _ in range(2):  # night, then dawn: Elsie opens the bakery
        events += (await sim.tick(world))["events"]
    missing = [e for e in events if e["type"] == "deed" and e["kind"] == "missing"]
    assert missing and missing[0]["claim"].startswith("Someone stole Elsie")
    about_player = await T.recall_about(world, "elsie", "player")
    assert not any("loaf" in b["claim"] for b in about_player["beliefs"])


async def test_lie_conflict_resolved_by_trust(world):
    fid = await run_theft(world)
    out = await sim.act(world, {"action": "lie", "npc": "bram", "subject": "pip", "item": "hammer"})
    # Bram saw it himself: his own memory beats the player's claim, and the lie becomes a story
    assert out["conflicts"] and all(c["winner"] == fid for c in out["conflicts"])
    assert any(e["type"] == "deed" and e["kind"] == "caught_lying" for e in out["events"])
    beliefs = {b["fact"]: b for b in await T.recall_all(world, "bram")}
    assert beliefs[fid]["stance"] == "accepted" and beliefs["claim-pip-stole-hammer"]["stance"] == "doubted"


# --------------------------------------------------------------------------- coordination & procedural memory
async def test_atomic_task_claim(world):
    fid = await run_theft(world)
    await store.rw(world, Q.TEACH.cypher, {"teacher": "oswin", "learner": "pip", "proc": "investigate", "tick": 1})
    t = await T.post_task(world, "oswin", "investigate", fid, "investigate")
    results = await asyncio.gather(*(T.claim_task(world, n, t["task"]) for n in ("finn", "pip", "finn", "pip")))
    assert sum(r["claimed"] for r in results) == 1  # exactly one winner, decided by the database
    t2 = await T.post_task(world, "oswin", "investigate", fid, "investigate")
    assert not (await T.claim_task(world, "tobias", t2["task"]))["claimed"]  # lacks the procedure


async def test_procedure_teaching_handoff(world):
    r = await store.rw(world, Q.TEACH.cypher, {"teacher": "agnes", "learner": "hilda", "proc": "report_crime",
                                               "tick": 3})
    assert r and r[0]["name"] == "Report a crime"
    procs = {p["id"]: p for p in await T.my_procedures(world, "hilda")}
    assert procs["report_crime"]["learned_from"] == "agnes"
    assert abs(procs["report_crime"]["proficiency"] - 0.4) < 1e-6


# --------------------------------------------------------------------------- tenant isolation
async def test_tenant_isolation(world):
    other = world + "-b"
    await sim.create_world(other, "Other")
    try:
        assert graph_name(world) != graph_name(other)
        fid = await run_theft(world)
        for _ in range(3):
            await sim.tick(world)
        assert await T.who_knows(world, fid)
        assert await T.who_knows(other, fid) == []  # the fact does not exist in the other world
        for npc in ("bram", "mira", "oswin"):
            assert (await T.recall_about(other, npc, "player"))["beliefs"] == []
        assert (await sim.clock(other))["tick"] == 0
        assert (await sim.clock(world))["tick"] == 4
    finally:
        await store.drop(other)


async def test_returning_player_is_remembered(world):
    await run_theft(world)
    s2 = await sim.start_session(world)
    assert s2["n"] == 2
    lines = await agent.greetings(world, True, s2["n"])
    assert any(line["npc"] == "bram" and "hammer" in line["text"] for line in lines)


async def test_graph_algorithms(world):
    await run_theft(world)
    for _ in range(3):
        await sim.tick(world)
    a = await T.analytics(world)
    assert set(a["communities"]) == {n["id"] for n in (await sim.state(world))["npcs"]}
    assert len(set(a["communities"].values())) >= 2
    assert a["gossip_hubs"][0]["npc"] == "mira"
    fid = next(r["fact"] for r in await T.recall_all(world, "oswin") if "hammer" in r["claim"])
    forecast = await T.forecast_spread(world, fid)
    holders = {k["npc"] for k in await T.who_knows(world, fid)}
    assert forecast and all(f["npc"] not in holders for f in forecast)


async def test_provenance_stays_fast_over_many_days(world):
    """Regression: the TOLD*0..10 path must be filtered during expansion, not after (it went exponential)."""
    import time
    fid = await run_theft(world)
    events = []
    for _ in range(30):
        events += (await sim.tick(world))["events"]
    while (await sim.clock(world))["slot_index"] != 1:  # morning: the market is busy
        events += (await sim.tick(world))["events"]
    await sim.act(world, {"action": "move", "location": "market"})
    out = await sim.act(world, {"action": "steal", "item": "apples"})
    assert out["events"][0]["witnesses"], "someone should see the second theft"
    late = []
    for _ in range(10):
        late += (await sim.tick(world))["events"]
    assert not [e for e in events + late if e["type"] == "error"]
    assert any(e["type"] == "confront" and e["fact"] == fid for e in events)
    assert any(e["type"] == "confront" and e["fact"] == out["fact"] for e in late), "guard must still confront"
    worst = 0.0
    for f in (fid, out["fact"]):
        for row in await T.who_knows(world, f):
            t0 = time.perf_counter()
            prov = await T.who_told_me(world, row["npc"], f)
            worst = max(worst, time.perf_counter() - t0)
            assert prov["found"] and prov["chain"][-1] == row["npc"]
            if row["source"] == "told":
                assert len(prov["chain"]) - 1 == row["hop"] and prov["chain"][-2] == row["via"]
    assert worst < 0.3, f"provenance took {worst * 1000:.0f} ms"


async def test_conflict_with_several_rivals_has_one_winner(world):
    fid = await run_theft(world)  # Bram saw it: confidence 1.0
    await sim.act(world, {"action": "lie", "npc": "bram", "subject": "pip", "item": "hammer"})
    await sim.act(world, {"action": "lie", "npc": "bram", "subject": "tobias", "item": "hammer"})
    beliefs = {b["fact"]: b["stance"] for b in await T.recall_all(world, "bram")}
    assert beliefs[fid] == "accepted"
    assert beliefs["claim-pip-stole-hammer"] == beliefs["claim-tobias-stole-hammer"] == "doubted"


async def test_llm_output_validation():
    ok, bad = agent.validate("Bram told me you took the silver hammer.", '{"via": "bram", "claim": "silver hammer"}',
                             "oswin", "Ada", "how do you know?")
    assert ok and not bad
    ok, bad = agent.validate("Hilda saw you at the Chapel of Dawn.", '{"via": "bram"}', "oswin", "Ada", "hi")
    assert not ok and "hilda" in bad and "chapel" in bad
