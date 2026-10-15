"""The town template: locations, NPC agents, social ties, items, procedures and
seed memories. ``build_world`` turns it into a FalkorDB graph for one tenant.

Everything here is synthetic (invented characters), as required by the rules.
"""
from __future__ import annotations

from typing import Any

TOWN_NAME = "Thornbury"

# One day = 6 ticks.
SLOTS = ["Dawn", "Morning", "Midday", "Afternoon", "Evening", "Night"]
TICKS_PER_DAY = len(SLOTS)

LOCATIONS: list[dict[str, Any]] = [
    {"id": "square", "name": "Town Square", "kind": "plaza", "x": 500, "y": 330},
    {"id": "bakery", "name": "Elsie's Bakery", "kind": "shop", "x": 255, "y": 175},
    {"id": "forge", "name": "The Forge", "kind": "workshop", "x": 150, "y": 400},
    {"id": "tavern", "name": "The Crooked Kettle", "kind": "tavern", "x": 770, "y": 185},
    {"id": "market", "name": "Market Row", "kind": "market", "x": 640, "y": 520},
    {"id": "guardhouse", "name": "Guardhouse", "kind": "guard", "x": 880, "y": 410},
    {"id": "chapel", "name": "Chapel of Dawn", "kind": "chapel", "x": 470, "y": 95},
    {"id": "cottages", "name": "Willow Cottages", "kind": "homes", "x": 310, "y": 545},
]

# schedule: location per slot [dawn, morning, midday, afternoon, evening, night]
NPCS: list[dict[str, Any]] = [
    {
        "id": "bram", "name": "Bram", "role": "blacksmith", "color": "#c8553d",
        "personality": "gruff, honest, holds grudges; distrusts the captain since being fined",
        "chattiness": 0.65, "credibility": 0.95,
        "schedule": ["forge", "forge", "tavern", "forge", "tavern", "cottages"],
    },
    {
        "id": "mira", "name": "Mira", "role": "tavern keeper", "color": "#e09f3e",
        "personality": "warm, nosy, the town's living newspaper; loves a good story",
        "chattiness": 0.95, "credibility": 0.85,
        "schedule": ["tavern", "market", "tavern", "tavern", "tavern", "tavern"],
    },
    {
        "id": "oswin", "name": "Captain Oswin", "role": "guard", "color": "#33658a",
        "personality": "stern, by-the-book, fair; acts on reports, never on whispers alone",
        "chattiness": 0.35, "credibility": 1.0,
        "schedule": ["guardhouse", "square", "guardhouse", "square", "tavern", "guardhouse"],
    },
    {
        "id": "elsie", "name": "Elsie", "role": "baker", "color": "#d4a373",
        "personality": "kind, anxious, protective of her family",
        "chattiness": 0.6, "credibility": 0.95,
        "schedule": ["bakery", "bakery", "bakery", "market", "cottages", "cottages"],
    },
    {
        "id": "tobias", "name": "Tobias", "role": "merchant", "color": "#6a4c93",
        "personality": "shrewd, charming, trades in goods and in rumours",
        "chattiness": 0.8, "credibility": 0.75,
        "schedule": ["market", "market", "tavern", "market", "tavern", "cottages"],
    },
    {
        "id": "agnes", "name": "Sister Agnes", "role": "priest", "color": "#8d99ae",
        "personality": "discreet, wise, hard to fool; keeps confidences",
        "chattiness": 0.25, "credibility": 1.0,
        "schedule": ["chapel", "chapel", "square", "chapel", "chapel", "chapel"],
    },
    {
        "id": "pip", "name": "Pip", "role": "street urchin", "color": "#7fb069",
        "personality": "quick, cheeky, everywhere at once; not always believed",
        "chattiness": 0.85, "credibility": 0.6,
        "schedule": ["square", "market", "bakery", "forge", "tavern", "square"],
    },
    {
        "id": "hilda", "name": "Hilda", "role": "herbalist", "color": "#2a9d8f",
        "personality": "practical, sharp-tongued, quietly caring",
        "chattiness": 0.5, "credibility": 0.9,
        "schedule": ["chapel", "market", "market", "chapel", "cottages", "cottages"],
    },
    {
        "id": "rowan", "name": "Rowan", "role": "farmer", "color": "#a7c957",
        "personality": "slow to speak, loyal, Elsie's husband",
        "chattiness": 0.4, "credibility": 0.9,
        "schedule": ["market", "market", "square", "bakery", "cottages", "cottages"],
    },
    {
        "id": "finn", "name": "Finn", "role": "deputy", "color": "#4f7cac",
        "personality": "eager young deputy, Captain Oswin's nephew, wants to prove himself",
        "chattiness": 0.6, "credibility": 0.9,
        "schedule": ["square", "guardhouse", "guardhouse", "square", "guardhouse", "guardhouse"],
    },
]

NPC_BY_ID = {n["id"]: n for n in NPCS}
GUARD_ROLES = {"guard", "deputy"}

# (a, b, trust, affinity, bond) -- symmetric base; directed trust is scaled by the
# *told-by* party's credibility so everyone trusts Pip a little less.
TIES: list[tuple[str, str, float, float, str]] = [
    ("bram", "mira", 0.85, 0.85, "friend"),
    ("bram", "tobias", 0.65, 0.6, "drinking mate"),
    ("bram", "pip", 0.6, 0.5, "acquaintance"),
    ("bram", "oswin", 0.35, 0.1, "rival"),
    ("bram", "finn", 0.5, 0.35, "acquaintance"),
    ("mira", "tobias", 0.65, 0.7, "friend"),
    ("mira", "oswin", 0.8, 0.8, "friend"),
    ("mira", "pip", 0.55, 0.6, "acquaintance"),
    ("mira", "elsie", 0.75, 0.6, "friend"),
    ("mira", "hilda", 0.6, 0.5, "acquaintance"),
    ("oswin", "finn", 0.9, 0.9, "family"),
    ("oswin", "agnes", 0.85, 0.7, "colleague"),
    ("finn", "pip", 0.55, 0.6, "acquaintance"),
    ("agnes", "hilda", 0.9, 0.8, "friend"),
    ("agnes", "elsie", 0.75, 0.6, "acquaintance"),
    ("elsie", "rowan", 0.95, 0.95, "family"),
    ("elsie", "hilda", 0.75, 0.7, "friend"),
    ("rowan", "tobias", 0.55, 0.45, "trade"),
    ("rowan", "hilda", 0.6, 0.55, "acquaintance"),
    ("tobias", "elsie", 0.6, 0.5, "trade"),
    ("pip", "rowan", 0.45, 0.4, "acquaintance"),
]

ITEMS: list[dict[str, Any]] = [
    {"id": "hammer", "name": "silver hammer", "owner": "bram", "location": "forge", "value": 30},
    {"id": "loaf", "name": "honey loaf", "owner": "elsie", "location": "bakery", "value": 3},
    {"id": "apples", "name": "basket of apples", "owner": "tobias", "location": "market", "value": 5},
    {"id": "cask", "name": "cask of ale", "owner": "mira", "location": "tavern", "value": 12},
    {"id": "herbs", "name": "moonpetal herbs", "owner": "hilda", "location": "market", "value": 8},
    {"id": "candlestick", "name": "chapel candlestick", "owner": "agnes", "location": "chapel", "value": 20},
    {"id": "coins", "name": "pouch of coins", "owner": "player", "location": None, "value": 10},
    {"id": "flowers", "name": "bunch of wildflowers", "owner": "player", "location": None, "value": 1},
]

PROCEDURES: list[dict[str, Any]] = [
    {"id": "report_crime", "name": "Report a crime",
     "steps": ["Walk to wherever the captain is", "Tell the guard what you saw", "Answer their questions"],
     "teachable": True},
    {"id": "investigate", "name": "Investigate a crime",
     "steps": ["Take the case from the task board", "Trace the story back to its first witness",
               "Hand the findings to the captain"],
     "teachable": True},
    {"id": "discern_truth", "name": "Discern a lie",
     "steps": ["Weigh who is telling you", "Compare with what you saw yourself", "Doubt claims that conflict"],
     "teachable": True},
    {"id": "forge_tools", "name": "Forge tools", "steps": ["Heat", "Hammer", "Quench"], "teachable": False},
    {"id": "bake_bread", "name": "Bake bread", "steps": ["Knead", "Prove", "Bake at dawn"], "teachable": True},
    {"id": "brew_ale", "name": "Brew ale", "steps": ["Mash", "Boil with hops", "Ferment"], "teachable": False},
    {"id": "haggle", "name": "Haggle", "steps": ["Ask high", "Feign leaving", "Settle"], "teachable": True},
    {"id": "herbal_remedy", "name": "Herbal remedy", "steps": ["Gather moonpetal", "Steep", "Dose"],
     "teachable": True},
]

# npc -> [(procedure, proficiency)]
SKILLS: dict[str, list[tuple[str, float]]] = {
    "bram": [("forge_tools", 0.95)],
    "mira": [("brew_ale", 0.9), ("haggle", 0.6)],
    "oswin": [("investigate", 0.9), ("report_crime", 0.9), ("discern_truth", 0.6)],
    "elsie": [("bake_bread", 0.95), ("report_crime", 0.7)],
    "tobias": [("haggle", 0.95)],
    "agnes": [("discern_truth", 0.9), ("report_crime", 0.8), ("herbal_remedy", 0.5)],
    "pip": [("haggle", 0.4)],
    "hilda": [("herbal_remedy", 0.95), ("discern_truth", 0.5)],
    "rowan": [("bake_bread", 0.35)],
    "finn": [("investigate", 0.55), ("report_crime", 0.9)],
}

# Seed episodic memories (day 0) so the town has a past. Each becomes an Event, a
# Fact derived from it, WITNESSED edges and BELIEVES edges (witnesses + hearsay).
SEED_EVENTS: list[dict[str, Any]] = [
    {
        "id": "e-seed-fine", "kind": "fined", "location": "tavern",
        "actor": "oswin", "target": "bram", "item": None,
        "claim": "Captain Oswin fined Bram for brawling at the Crooked Kettle",
        "predicate": "fined", "importance": 0.35, "valence": 0.0,
        "witnesses": ["mira", "tobias", "bram", "oswin"], "hearsay": {"finn": ("oswin", 0.8)},
    },
    {
        "id": "e-seed-tonic", "kind": "healed", "location": "cottages",
        "actor": "hilda", "target": "rowan", "item": "herbs",
        "claim": "Hilda's moonpetal tonic cured Rowan's fever",
        "predicate": "healed", "importance": 0.3, "valence": 0.0,
        "witnesses": ["hilda", "rowan", "elsie"], "hearsay": {"agnes": ("hilda", 0.85)},
    },
    {
        "id": "e-seed-ale", "kind": "cheated", "location": "market",
        "actor": "tobias", "target": "rowan", "item": "cask",
        "claim": "Tobias sold Rowan watered-down ale",
        "predicate": "cheated", "importance": 0.3, "valence": 0.0,
        "witnesses": ["rowan"], "hearsay": {"elsie": ("rowan", 0.9)},
    },
]


def tick_info(tick: int) -> dict[str, Any]:
    return {"tick": tick, "day": tick // TICKS_PER_DAY + 1, "slot": SLOTS[tick % TICKS_PER_DAY],
            "slot_index": tick % TICKS_PER_DAY}


def schedule_location(npc_id: str, tick: int) -> str:
    return NPC_BY_ID[npc_id]["schedule"][tick % TICKS_PER_DAY]


def directed_ties() -> list[dict[str, Any]]:
    """KNOWS edges a->b: a's trust in b (scaled by b's credibility) and affinity."""
    out = []
    for a, b, trust, aff, bond in TIES:
        for x, y in ((a, b), (b, a)):
            t = round(trust * NPC_BY_ID[y]["credibility"], 3)
            out.append({"a": x, "b": y, "trust": t, "affinity": aff, "bond": bond,
                        "distance": round(1.1 - aff, 3)})
    return out
