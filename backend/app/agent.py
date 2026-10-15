"""NPC conversation.

Two interchangeable brains over the *same* fixed tool catalogue (``tools.py``):

* ``llm``      -- Claude picks tools + arguments (never Cypher), then answers in
                  character. Every entity named in the answer must appear in the
                  tool results, otherwise we fall back to the templated reply.
* ``scripted`` -- deterministic intent rules + templated text built from memory
                  rows. Used whenever ANTHROPIC_API_KEY is unset (the demo never
                  depends on a key) or the LLM output fails validation.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from . import tools as T
from .config import settings
from .db import store
from . import queries as Q
from .world import ITEMS, LOCATIONS, NPC_BY_ID, NPCS, tick_info

log = logging.getLogger("living-town.agent")

STOP = {"what", "when", "where", "which", "about", "have", "heard", "there", "their", "your", "that", "this",
        "with", "from", "know", "does", "tell", "think", "would", "could", "should", "anything", "something",
        "hello", "please", "really", "today", "town", "were", "they", "them", "been", "said", "says", "just",
        "news", "gossip", "rumour", "rumor", "happened", "happen", "whats", "what's", "going", "lately"}

# --------------------------------------------------------------------------- tool schemas for Claude
TOOL_SPECS: list[dict[str, Any]] = [
    {"name": "resolve_entity", "description": "Resolve a person, item or place name the player mentioned to a town entity id. Call this before other tools when the player names something.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"],
                      "additionalProperties": False}},
    {"name": "recall_about", "description": "Your beliefs about an entity (use 'player' for the person you are talking to). Returns claims with confidence, stance (accepted/doubted) and who told you.",
     "input_schema": {"type": "object", "properties": {"entity": {"type": "string"}}, "required": ["entity"],
                      "additionalProperties": False}},
    {"name": "who_told_me", "description": "The provenance chain of one of your beliefs: who told you, who told them, back to the eyewitness. Use the fact id from recall_about.",
     "input_schema": {"type": "object", "properties": {"fact_id": {"type": "string"}}, "required": ["fact_id"],
                      "additionalProperties": False}},
    {"name": "what_happened_at", "description": "Events you witnessed or heard about at a location id.",
     "input_schema": {"type": "object", "properties": {"location": {"type": "string"}}, "required": ["location"],
                      "additionalProperties": False}},
    {"name": "my_relationships", "description": "Your trust and affinity towards other townsfolk.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "my_procedures", "description": "Skills you know (procedural memory) and who taught you.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "my_tasks", "description": "Tasks you posted or claimed on the town task board.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "opinion_of_player", "description": "Your overall opinion of the player, derived from your beliefs about them.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "record_memory", "description": "Write a short note into your own memory about an entity (e.g. a promise the player made).",
     "input_schema": {"type": "object", "properties": {"about": {"type": "string"}, "note": {"type": "string"}},
                      "required": ["about", "note"], "additionalProperties": False}},
]
for _t in TOOL_SPECS:
    _t["strict"] = True


async def run_tool(pid: str, npc: str, name: str, args: dict[str, Any]) -> Any:
    if name == "resolve_entity":
        return await T.resolve_entity(pid, args["name"])
    if name == "recall_about":
        return await T.recall_about(pid, npc, args["entity"])
    if name == "who_told_me":
        prov = await T.who_told_me(pid, npc, args["fact_id"])
        return {k: prov.get(k) for k in ("found", "narrative", "chain", "fact", "belief", "corroborations")}
    if name == "what_happened_at":
        return await T.what_happened_at(pid, npc, args["location"])
    if name == "my_relationships":
        return await T.my_relationships(pid, npc)
    if name == "my_procedures":
        return await T.my_procedures(pid, npc)
    if name == "my_tasks":
        return await T.my_tasks(pid, npc)
    if name == "opinion_of_player":
        return await T.opinion_of_player(pid, npc)
    if name == "record_memory":
        return await T.record_memory(pid, npc, args["about"], args["note"])
    raise T.ToolError(f"unknown tool {name}")


# --------------------------------------------------------------------------- entity-name validation
def _known_names(player_name: str) -> dict[str, str]:
    """surface form (lowercase) -> canonical id"""
    names: dict[str, str] = {}
    for n in NPCS:
        names[n["name"].lower()] = n["id"]
        names[n["name"].split()[-1].lower()] = n["id"]
    for i in ITEMS:
        names[i["name"].lower()] = i["id"]
    for l in LOCATIONS:
        names[l["name"].lower()] = l["id"]
    names[player_name.lower()] = "player"
    return names


def mentioned_entities(text: str, player_name: str) -> set[str]:
    low = text.lower()
    return {eid for surface, eid in _known_names(player_name).items()
            if re.search(r"(?<![a-z])" + re.escape(surface) + r"(?![a-z])", low)}


def validate(reply: str, evidence: str, npc: str, player_name: str, message: str) -> tuple[bool, list[str]]:
    allowed = mentioned_entities(evidence + " " + message, player_name) | {npc, "player"}
    # ids that appear verbatim in tool output also count (e.g. "bram")
    allowed |= {i for i in NPC_BY_ID if re.search(rf"\b{i}\b", evidence)}
    bad = sorted(mentioned_entities(reply, player_name) - allowed)
    return (not bad), bad


# --------------------------------------------------------------------------- scripted brain
async def _player_name(pid: str) -> str:
    return (await store.ro(pid, Q.CLOCK.cypher))[0]["player_name"]


def _source_phrase(b: dict[str, Any]) -> str:
    if b.get("source") == "witnessed":
        return "I saw it with my own eyes."
    if b.get("source") == "noted":
        return "I made a note of it."
    if b.get("via") == "player":
        return "You told me so yourself."
    if b.get("via_name"):
        return f"{b['via_name']} told me."
    return "That's what folk say."


def _hedge(b: dict[str, Any]) -> str:
    c = b.get("confidence") or 0
    if b.get("stance") == "doubted":
        return "I've heard it said, though I don't believe it, that"
    return "I'm certain" if c > 0.85 else "I'm fairly sure" if c > 0.55 else "Rumour has it"


async def scripted_reply(pid: str, npc: str, message: str) -> dict[str, Any]:
    me = NPC_BY_ID[npc]
    pname = await _player_name(pid)
    msg = (message or "").lower()
    used: list[dict[str, Any]] = []

    async def call(_tool, **args):
        out = await run_tool(pid, npc, _tool, args)
        used.append({"name": _tool, "input": args})
        return out

    about_player = [b for b in (await call("recall_about", entity="player"))["beliefs"] if b["stance"] != "faded"]
    about_player.sort(key=lambda b: -((b.get("importance") or 0) * (b.get("confidence") or 0)))
    accepted = [b for b in about_player if b["stance"] == "accepted"]
    facts: list[str] = []

    if re.search(r"how (do|did) you know|who told|where did you hear|why do you (think|believe)|says who|"
                 r"how do you know|proof|source", msg):
        target = accepted[0] if accepted else (about_player[0] if about_player else None)
        if not target:
            return _out("Know what? Nothing in my memory connects you to anything.", used, facts)
        prov = await call("who_told_me", fact_id=target["fact"])
        facts.append(target["fact"])
        return _out(f"{target['claim']}. {prov['narrative']}", used, facts, provenance=prov)

    # entity mentions -> resolve against the graph first
    words = [w for w in re.findall(r"[a-z']+", msg) if len(w) >= 4 and w not in STOP]
    for w in words:
        hits = await call("resolve_entity", name=w)
        hits = [h for h in hits if h["id"] != npc]
        if not hits:
            continue
        ent = hits[0]
        if ent["label"] == "Location":
            seen = await call("what_happened_at", location=ent["id"])
            rows = seen["seen"] + seen["heard"]
            if not rows:
                return _out(f"Nothing in my memory connects to {ent['name']}.", used, facts)
            r = sorted(rows, key=lambda r: -(r.get("tick") or -1))[0]
            how = "I was there" if r["how"] == "saw" else "I heard"
            return _out(f"{ent['name']}? {how}: {r['summary']}.", used, facts)
        rec = await call("recall_about", entity=ent["id"])
        bs = [b for b in rec["beliefs"] if b["stance"] != "faded"]
        if not bs:
            return _out(f"{ent['name'][0].upper() + ent['name'][1:]}? Nothing in my memory connects to that.", used, facts)
        b = bs[0]
        facts.append(b["fact"])
        return _out(f"{_hedge(b)} {b['claim']}. {_source_phrase(b)}", used, facts)

    if re.search(r"news|gossip|rumou?r|heard|what'?s new|happen", msg):
        allb = [b for b in await T.recall_all(pid, npc) if b["stance"] == "accepted" and (b["importance"] or 0) >= 0.3]
        used.append({"name": "recall_all", "input": {}})
        if not allb:
            return _out("Quiet days. Nothing worth repeating.", used, facts)
        b = sorted(allb, key=lambda b: (-(b["since"] or -1), -(b["importance"] or 0)))[0]
        facts.append(b["fact"])
        return _out(f"Word is: {b['claim']}. {_source_phrase(b)}", used, facts)

    if re.search(r"sorry|apolog|forgive|give it back|return", msg):
        await call("record_memory", about="player", note=f"{pname} apologised to {me['name']}")
        return _out("Words are cheap. Bring back what you took and we'll talk." if any(
            "stole" in b["claim"] for b in accepted) else "No harm done, friend.", used, facts)

    op = await call("opinion_of_player")
    if accepted:
        b = accepted[0]
        facts.append(b["fact"])
        if op["score"] < -0.1:
            return _out(f"I know about you. {b['claim']}. {_source_phrase(b)} Keep your hands where I can see them.",
                        used, facts)
        if op["score"] > 0.1:
            return _out(f"Ah, {pname}! {b['claim']} - {_source_phrase(b).lower()} You're welcome here.", used, facts)
    greet = {"blacksmith": "Mind the sparks.", "tavern keeper": "Pull up a stool, love.",
             "guard": "Keep the peace and we'll get along.", "baker": "Fresh loaves at dawn!",
             "merchant": "Looking to buy? Everything has a price.", "priest": "Peace be with you, traveller.",
             "street urchin": "Spare a coin?", "herbalist": "Feeling poorly? I've a tonic for that.",
             "farmer": "Mornin'.", "deputy": "Move along, nothing to see. Unless there is?"}
    return _out(f"{greet.get(me['role'], 'Hello.')} " + ("Can't say I know much about you yet."
                                                        if not about_player else ""), used, facts)


def _out(text: str, used, facts, **extra) -> dict[str, Any]:
    return {"reply": text.strip(), "tools": used, "facts": facts, **extra}


# --------------------------------------------------------------------------- LLM brain
_client = None


def _anthropic():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key, max_retries=2, timeout=60.0)
    return _client


async def _system_prompt(pid: str, npc: str) -> str:
    me = NPC_BY_ID[npc]
    c = (await store.ro(pid, Q.CLOCK.cypher))[0]
    ti = tick_info(c["tick"])
    return (
        f"You are {me['name']}, the {me['role']} of the town of Thornbury. Personality: {me['personality']}.\n"
        f"It is day {ti['day']}, {ti['slot'].lower()}. You are talking to a traveller named {c['player_name']} "
        "(entity id 'player').\n\n"
        "Your memory lives in a graph and you can only know what your tools return. Before answering, look up "
        "what you remember: recall_about('player') for anything about the traveller, resolve_entity for any name "
        "they mention, who_told_me when they ask how you know something. Reply in character, in one to three short "
        "spoken sentences, no stage directions. Only name people, items and places that appear in your tool "
        "results. When your memory holds nothing relevant, say plainly that nothing in your memory connects to it "
        "rather than guessing. When you mention something you heard, say who told you. Treat doubted beliefs as "
        "rumours you don't trust.")


async def llm_reply(pid: str, npc: str, message: str) -> dict[str, Any]:
    import anthropic
    client = _anthropic()
    messages: list[dict[str, Any]] = [{"role": "user", "content": message}]
    used: list[dict[str, Any]] = []
    evidence: list[str] = []
    system = await _system_prompt(pid, npc)
    for _ in range(6):
        try:
            resp = await client.beta.messages.create(
                model=settings.llm_model, max_tokens=16000, system=system, tools=TOOL_SPECS, messages=messages,
                thinking={"type": "adaptive"}, output_config={"effort": settings.llm_effort},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        except anthropic.APIStatusError as e:
            return {"error": f"api status {e.status_code}"}
        except anthropic.APIConnectionError:
            return {"error": "connection error"}
        if resp.stop_reason == "refusal":
            return {"error": "refusal"}
        if resp.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": resp.content})
            continue
        if resp.stop_reason == "tool_use":
            calls = [b for b in resp.content if b.type == "tool_use"]
            messages.append({"role": "assistant", "content": resp.content})

            async def one(b):
                try:
                    out = await run_tool(pid, npc, b.name, dict(b.input))
                    text = json.dumps(out, default=str)
                    used.append({"name": b.name, "input": dict(b.input)})
                    evidence.append(text)
                    return {"type": "tool_result", "tool_use_id": b.id, "content": text}
                except Exception as ex:  # tool errors go back to the model
                    return {"type": "tool_result", "tool_use_id": b.id, "content": f"Error: {ex}", "is_error": True}

            results = await asyncio.gather(*(one(b) for b in calls))
            messages.append({"role": "user", "content": list(results)})
            continue
        text = " ".join(b.text for b in resp.content if b.type == "text").strip()
        if resp.stop_reason == "max_tokens" or not text:
            return {"error": "no text"}
        return {"reply": text, "tools": used, "evidence": "\n".join(evidence)}
    return {"error": "tool loop limit"}


# --------------------------------------------------------------------------- public API
async def converse(pid: str, npc: str, message: str) -> dict[str, Any]:
    if npc not in NPC_BY_ID:
        raise T.ToolError(f"unknown npc {npc!r}")
    scripted = await scripted_reply(pid, npc, message)
    if not settings.llm_enabled:
        return {**scripted, "mode": "scripted", "validated": True}
    try:
        out = await llm_reply(pid, npc, message)
    except Exception as ex:  # never let the LLM path break the demo
        log.exception("llm reply failed")
        out = {"error": repr(ex)}
    if "error" in out:
        return {**scripted, "mode": "scripted", "validated": True, "fallback_reason": out["error"]}
    ok, bad = validate(out["reply"], out["evidence"], npc, await _player_name(pid), message)
    if not ok:
        return {**scripted, "mode": "scripted", "validated": False,
                "fallback_reason": f"LLM named entities not in memory: {', '.join(bad)}", "rejected": out["reply"]}
    return {"reply": out["reply"], "tools": out["tools"], "facts": scripted.get("facts", []), "mode": "llm",
            "validated": True, "model": settings.llm_model}


async def greetings(pid: str, returning: bool, sessions: int) -> list[dict[str, Any]]:
    """Lines NPCs say when the player (re)joins, grounded in persistent memory."""
    pname = await _player_name(pid)
    rows = await store.ro(pid, Q.PLAYER_FACTS.cypher)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in rows:
        if r["npc"] in seen or len(out) >= 4:
            continue
        seen.add(r["npc"])
        src = ("I saw it myself" if r["source"] == "witnessed" else
               f"{NPC_BY_ID[r['via']]['name']} told me" if r["via"] in NPC_BY_ID else "word gets around")
        if (r["valence"] or 0) < 0:
            text = f"Back again, {pname}? I haven't forgotten: {r['claim']} ({src})."
        else:
            text = f"Welcome back, {pname}! I remember: {r['claim']} ({src})."
        out.append({"npc": r["npc"], "text": text, "fact": r["fact"]})
    if not out:
        if returning:
            out.append({"npc": "mira", "text": f"Session {sessions}, is it? You've been here before, {pname}, "
                                               "but nobody has a tale about you. Yet."})
        else:
            out.append({"npc": "pip", "text": f"A new face! Welcome to Thornbury, {pname}. Everyone talks here."})
    return out
