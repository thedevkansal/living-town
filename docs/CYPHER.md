# Cypher catalogue

Every query Living Town runs, generated from `backend/app/queries.py`. Agents choose a query and its parameters; they never write Cypher. `read` queries go through `GRAPH.RO_QUERY`.

## Schema

```cypher
CREATE INDEX FOR (n:NPC) ON (n.id)
CREATE INDEX FOR (n:Location) ON (n.id)
CREATE INDEX FOR (n:Item) ON (n.id)
CREATE INDEX FOR (n:Fact) ON (n.id)
CREATE INDEX FOR (n:Fact) ON (n.predicate)
CREATE INDEX FOR (n:Event) ON (n.id)
CREATE INDEX FOR (n:Task) ON (n.id)
CREATE INDEX FOR (n:Procedure) ON (n.id)
CREATE INDEX FOR ()-[r:TOLD]-() ON (r.fact_id)
CALL db.idx.fulltext.createNodeIndex('NPC', 'name', 'role')
CALL db.idx.fulltext.createNodeIndex('Item', 'name')
CALL db.idx.fulltext.createNodeIndex('Location', 'name')
```

## Graph algorithms

### `communities`

Social cliques: label propagation over CLOSE (affinity >= .55) edges

```cypher
CALL algo.labelPropagation({nodeLabels:['NPC'], relationshipTypes:['CLOSE']}) YIELD node, communityId RETURN node.id AS npc, communityId AS community
```

### `gossip_hubs`

Gossip brokers: betweenness centrality over KNOWS

```cypher
CALL algo.betweenness({nodeLabels:['NPC'], relationshipTypes:['KNOWS']}) YIELD node, score RETURN node.id AS npc, score ORDER BY score DESC
```

### `best_informed`

Best-informed agents: PageRank over the TOLD (who-told-whom) graph

```cypher
CALL algo.pageRank('NPC', 'TOLD') YIELD node, score RETURN node.id AS npc, score ORDER BY score DESC
```

### `broadcast_reach`

Broadcast reach: harmonic centrality over CLOSE edges

```cypher
CALL algo.HarmonicCentrality({nodeLabels:['NPC'], relationshipTypes:['CLOSE']}) YIELD node, score RETURN node.id AS npc, score ORDER BY score DESC
```

### `rumour_islands`

Rumour islands: weakly connected components of the TOLD graph

```cypher
CALL algo.WCC({nodeLabels:['NPC'], relationshipTypes:['TOLD']}) YIELD node, componentId RETURN node.id AS npc, componentId AS component
```

### `forecast_spread`

Rumour forecast: weighted shortest paths (SSpaths over KNOWS.distance) from current believers

```cypher
MATCH (b:NPC)-[bel:BELIEVES]->(f:Fact {id:$fid}) WHERE bel.stance = 'accepted' CALL algo.SSpaths({sourceNode:b, relTypes:['KNOWS'], weightProp:'distance', maxLen:3, pathCount:40}) YIELD path, pathWeight WITH last(nodes(path)) AS t, pathWeight AS w, [n IN nodes(path) | n.id] AS route OPTIONAL MATCH (t)-[x:BELIEVES]->(:Fact {id:$fid}) WITH t, w, route, x WHERE x IS NULL RETURN t.id AS npc, t.name AS name, w AS distance, route ORDER BY distance ASC LIMIT 60
```

### `social_route`

Cheapest social route between two NPCs (SPpaths over KNOWS.distance)

```cypher
MATCH (a:NPC {id:$a}), (b:NPC {id:$b}) CALL algo.SPpaths({sourceNode:a, targetNode:b, relTypes:['KNOWS'], weightProp:'distance', pathCount:1}) YIELD path, pathWeight RETURN [n IN nodes(path) | n.id] AS route, pathWeight AS distance
```

### `reach_within`

Who an NPC can reach within N hops (BFS over CLOSE)

```cypher
MATCH (a:NPC {id:$a}) CALL algo.BFS(a, $depth, 'CLOSE') YIELD nodes RETURN [n IN nodes | n.id] AS reach
```

## Reads

### `clock`

Read the town clock

```cypher
MATCH (t:Town) RETURN t.tick AS tick, t.name AS name, t.player_name AS player_name, t.sessions AS sessions, t.seed AS seed
```

### `last_active`

When a tenant world was last used (for free-tier pruning)

```cypher
MATCH (t:Town) RETURN t.last_active AS last_active, t.created_at AS created_at
```

### `sessions`

List the player's past sessions

```cypher
MATCH (:Player)-[:HAD_SESSION]->(s:Session) OPTIONAL MATCH (e:Event)-[:DURING]->(s) RETURN s.n AS n, s.started_at AS started_at, s.start_tick AS start_tick, count(e) AS events ORDER BY n DESC LIMIT 5
```

### `where_is`

Current location of an NPC or the player

```cypher
MATCH (n {id:$id})-[:IS_AT]->(l:Location) RETURN l.id AS location
```

### `present_at`

NPCs currently at a location

```cypher
MATCH (n:NPC)-[:IS_AT]->(l:Location {id:$loc}) RETURN n.id AS id, n.name AS name, n.role AS role
```

### `player_state`

Player location and inventory

```cypher
MATCH (p:Player)-[:IS_AT]->(l:Location) OPTIONAL MATCH (i:Item)-[:OWNED_BY]->(p) RETURN p.name AS name, l.id AS location, collect({id:i.id, name:i.name}) AS items
```

### `agent_context`

An agent perceives who shares its location, with affinity/trust both ways

```cypher
MATCH (me:NPC {id:$id})-[:IS_AT]->(l:Location) OPTIONAL MATCH (l)<-[:IS_AT]-(o:NPC) WHERE o.id <> me.id OPTIONAL MATCH (me)-[k:KNOWS]->(o) OPTIONAL MATCH (o)-[k2:KNOWS]->(me) OPTIONAL MATCH (l)<-[:IS_AT]-(p:Player) RETURN l.id AS location, count(p) > 0 AS player_here, collect(CASE WHEN o IS NULL THEN NULL ELSE {id:o.id, role:o.role, affinity:coalesce(k.affinity, 0.1), trust_in_me:coalesce(k2.trust, 0.25)} END) AS company
```

### `agent_beliefs`

An agent's most salient accepted beliefs (gossip candidates)

```cypher
MATCH (me:NPC {id:$id})-[b:BELIEVES]->(f:Fact) WHERE b.stance = 'accepted' AND f.importance >= 0.2 RETURN f.id AS fact, f.claim AS claim, f.importance AS importance, f.kind AS kind, f.predicate AS predicate, f.subject AS subject, b.confidence AS confidence, b.hop AS hop, b.since_tick AS since ORDER BY f.importance * b.confidence DESC LIMIT 8
```

### `who_believes_among`

Which of these NPCs already hold which of these facts

```cypher
MATCH (o:NPC)-[:BELIEVES]->(f:Fact) WHERE o.id IN $ids AND f.id IN $facts RETURN o.id AS npc, f.id AS fact
```

### `tick_snapshot`

Shared state every agent plans from: where the player is, the task board, unseen thefts

```cypher
MATCH (p:Player)-[:IS_AT]->(pl:Location) OPTIONAL MATCH (t:Task) WITH pl, collect(t {.id, .kind, .status, .fact_id, .required, .posted_by, .claimed_by, .target, .note}) AS tasks OPTIONAL MATCH (i:Item {stolen_unseen:true})-[:KEPT_AT]->(il:Location) RETURN pl.id AS player_loc, tasks, collect(CASE WHEN i IS NULL THEN NULL ELSE {item:i.id, item_name:i.name, owner:i.original_owner, location:il.id} END) AS unseen
```

### `perceive`

One agent's whole perception in a single read: company (with trust both ways and their skills), salient beliefs (with which companions already hold them) and its own procedures

```cypher
MATCH (me:NPC {id:$id})-[:IS_AT]->(l:Location) OPTIONAL MATCH (l)<-[:IS_AT]-(o:NPC) WHERE o.id <> me.id OPTIONAL MATCH (me)-[k:KNOWS]->(o) OPTIONAL MATCH (o)-[k2:KNOWS]->(me) WITH me, l, collect(CASE WHEN o IS NULL THEN NULL ELSE {id:o.id, role:o.role, affinity:coalesce(k.affinity, 0.1), trust_in_me:coalesce(k2.trust, 0.25), procs:[(o)-[:KNOWS_HOW]->(op:Procedure) | op.id]} END) AS company OPTIONAL MATCH (l)<-[:IS_AT]-(pl:Player) WITH me, l, company, count(pl) > 0 AS player_here OPTIONAL MATCH (me)-[b:BELIEVES]->(f:Fact) WHERE b.stance = 'accepted' AND f.importance >= 0.2 WITH me, l, company, player_here, b, f ORDER BY f.importance * b.confidence DESC WITH me, l, company, player_here, collect(CASE WHEN f IS NULL THEN NULL ELSE {fact:f.id, claim:f.claim, importance:f.importance, kind:f.kind, predicate:f.predicate, subject:f.subject, object_name:f.object_name, confidence:b.confidence, hop:b.hop, since:b.since_tick, holders:[(h:NPC)-[:BELIEVES]->(f) | h.id]} END) AS beliefs RETURN l.id AS location, company, player_here, beliefs[0..10] AS beliefs, [(me)-[kh:KNOWS_HOW]->(p:Procedure) | {id:p.id, name:p.name, proficiency:kh.proficiency, teachable:p.teachable}] AS procs
```

### `act_context`

Everything a player action needs in one read

```cypher
MATCH (t:Town), (p:Player)-[:IS_AT]->(l:Location) OPTIONAL MATCH (l)<-[:IS_AT]-(n:NPC) WITH t, p, l, collect(n.id) AS present OPTIONAL MATCH (i:Item)-[:OWNED_BY]->(p) RETURN t.tick AS tick, t.sessions AS sessions, p.name AS name, l.id AS location, present, collect(CASE WHEN i IS NULL THEN NULL ELSE {id:i.id, name:i.name} END) AS items
```

### `recall_about`

What an NPC believes about an entity (with stance, confidence, source)

```cypher
MATCH (me:NPC {id:$npc})-[b:BELIEVES]->(f:Fact)-[:ABOUT]->(x) WHERE x.id = $entity RETURN DISTINCT f.id AS fact, f.claim AS claim, f.kind AS kind, f.importance AS importance, b.confidence AS confidence, b.stance AS stance, b.hop AS hop, b.via AS via, b.source AS source, b.since_tick AS since ORDER BY confidence DESC LIMIT 10
```

### `recall_all`

All of an NPC's beliefs

```cypher
MATCH (me:NPC {id:$npc})-[b:BELIEVES]->(f:Fact) RETURN f.id AS fact, f.claim AS claim, f.kind AS kind, f.importance AS importance, f.truth AS truth, b.confidence AS confidence, b.stance AS stance, b.hop AS hop, b.via AS via, b.source AS source, b.since_tick AS since, b.corroborations AS corroborations ORDER BY f.importance * b.confidence DESC
```

### `who_told_me`

Provenance: the primary TOLD chain from the original source to this NPC, plus the Event and actor

```cypher
MATCH (me:NPC {id:$npc})-[b:BELIEVES]->(f:Fact {id:$fid}) OPTIONAL MATCH chain = (src)-[:TOLD*0..10 {fact_id:$fid, primary:true}]->(me) WITH me, b, f, chain ORDER BY length(chain) DESC LIMIT 1 WITH me, b, f, chain, nodes(chain)[0] AS origin OPTIONAL MATCH (origin)-[w:WITNESSED]->(e:Event)<-[:DERIVED_FROM]-(f) OPTIONAL MATCH (e)-[inv:INVOLVED {role:'actor'}]->(actor) OPTIONAL MATCH (e)-[:AT]->(loc:Location) RETURN [n IN nodes(chain) | {id:n.id, name:n.name, label:labels(n)[0]}] AS chain, [r IN relationships(chain) | {tick:r.tick, confidence:r.confidence, channel:r.channel, times:r.times}] AS tellings, e {.id, .kind, .summary, .tick, .day, .slot} AS event, actor {.id, .name} AS actor, labels(actor)[0] AS actor_label, loc {.id, .name} AS location, w.tick AS witnessed_tick, b {.confidence, .stance, .hop, .via, .source, .corroborations} AS belief, f {.id, .claim, .kind, .truth} AS fact
```

### `corroborations`

Secondary (non-primary) tellers who repeated a Fact to this NPC

```cypher
MATCH (src)-[t:TOLD {fact_id:$fid}]->(me:NPC {id:$npc}) WHERE t.primary = false RETURN src.id AS id, src.name AS name, t.tick AS tick, t.channel AS channel, t.confidence AS confidence
```

### `what_happened_at`

Episodic recall: events an NPC witnessed or heard of at a place

```cypher
MATCH (me:NPC {id:$npc}), (l:Location {id:$loc}) OPTIONAL MATCH (me)-[w:WITNESSED]->(e:Event)-[:AT]->(l) WITH me, l, collect(DISTINCT {summary:e.summary, tick:e.tick, day:e.day, how:'saw'}) AS seen OPTIONAL MATCH (me)-[b:BELIEVES]->(f:Fact)-[:DERIVED_FROM]->(e2:Event)-[:AT]->(l) WHERE b.source <> 'witnessed' RETURN seen, collect(DISTINCT {summary:f.claim, tick:e2.tick, day:e2.day, how:'heard', via:b.via, confidence:b.confidence}) AS heard
```

### `my_relationships`

An NPC's social edges

```cypher
MATCH (me:NPC {id:$npc})-[k:KNOWS]->(o:NPC) RETURN o.id AS id, o.name AS name, o.role AS role, k.trust AS trust, k.affinity AS affinity, k.bond AS bond ORDER BY k.affinity DESC
```

### `my_procedures`

Procedural memory: what an NPC knows how to do and who taught them

```cypher
MATCH (me:NPC {id:$npc})-[k:KNOWS_HOW]->(p:Procedure) RETURN p.id AS id, p.name AS name, p.steps AS steps, k.proficiency AS proficiency, k.learned_from AS learned_from, k.tick AS tick ORDER BY k.proficiency DESC
```

### `opinion_of_player`

Reputation: an NPC's opinion of the player, derived from accepted beliefs about them

```cypher
MATCH (me:NPC {id:$npc}) OPTIONAL MATCH (me)-[b:BELIEVES]->(f:Fact)-[:ABOUT {role:'subject'}]->(:Player) WHERE b.stance = 'accepted' RETURN coalesce(sum(f.valence * b.confidence), 0.0) AS score, count(f) AS facts
```

### `town_reputation`

Every NPC's opinion of the player

```cypher
MATCH (n:NPC) OPTIONAL MATCH (n)-[b:BELIEVES]->(f:Fact)-[:ABOUT {role:'subject'}]->(:Player) WHERE b.stance = 'accepted' RETURN n.id AS npc, coalesce(sum(f.valence * b.confidence), 0.0) AS score, count(f) AS facts
```

### `player_facts`

Accepted beliefs about the player across the town (for greetings)

```cypher
MATCH (n:NPC)-[b:BELIEVES]->(f:Fact)-[:ABOUT {role:'subject'}]->(:Player) WHERE b.stance = 'accepted' RETURN n.id AS npc, n.name AS name, f.id AS fact, f.claim AS claim, f.valence AS valence, b.confidence AS confidence, b.via AS via, b.source AS source ORDER BY abs(f.valence) * b.confidence DESC
```

### `who_knows`

Everyone who holds a Fact, by hop distance from the source

```cypher
MATCH (n:NPC)-[b:BELIEVES]->(f:Fact {id:$fid}) RETURN n.id AS npc, n.name AS name, b.confidence AS confidence, b.stance AS stance, b.hop AS hop, b.via AS via, b.source AS source ORDER BY hop, confidence DESC
```

### `fact_info`

A Fact and its rivals

```cypher
MATCH (f:Fact {id:$fid}) OPTIONAL MATCH (f)-[:CONTRADICTS]-(g:Fact) RETURN f {.*} AS fact, collect(g {.id, .claim}) AS rivals
```

### `resolve_entity`

Resolve a name to a graph entity before any tool call

```cypher
MATCH (n:NPC) WHERE n.id = $q OR toLower(n.name) CONTAINS $q OR toLower(n.role) = $q RETURN n.id AS id, n.name AS name, 'NPC' AS label UNION ALL MATCH (i:Item) WHERE i.id = $q OR toLower(i.name) CONTAINS $q RETURN i.id AS id, i.name AS name, 'Item' AS label UNION ALL MATCH (l:Location) WHERE l.id = $q OR toLower(l.name) CONTAINS $q RETURN l.id AS id, l.name AS name, 'Location' AS label UNION ALL MATCH (p:Player) WHERE toLower(p.name) = $q OR $q IN ['me', 'myself', 'player', 'i'] RETURN p.id AS id, p.name AS name, 'Player' AS label
```

### `resolve_fulltext_npc`

Full-text (prefix/fuzzy) NPC lookup

```cypher
CALL db.idx.fulltext.queryNodes('NPC', $q) YIELD node, score RETURN node.id AS id, node.name AS name, 'NPC' AS label, score ORDER BY score DESC LIMIT 3
```

### `resolve_fulltext_item`

Full-text (prefix/fuzzy) Item lookup

```cypher
CALL db.idx.fulltext.queryNodes('Item', $q) YIELD node, score RETURN node.id AS id, node.name AS name, 'Item' AS label, score ORDER BY score DESC LIMIT 3
```

### `resolve_fulltext_location`

Full-text (prefix/fuzzy) Location lookup

```cypher
CALL db.idx.fulltext.queryNodes('Location', $q) YIELD node, score RETURN node.id AS id, node.name AS name, 'Location' AS label, score ORDER BY score DESC LIMIT 3
```

### `open_tasks`

Open/claimed tasks on the shared board

```cypher
MATCH (t:Task) WHERE t.status <> 'done' RETURN t.id AS id, t.kind AS kind, t.status AS status, t.fact_id AS fact, t.required AS required, t.posted_by AS posted_by, t.claimed_by AS claimed_by, t.target AS target, t.note AS note, t.posted_tick AS posted_tick
```

### `all_tasks`

Every task (for the board UI)

```cypher
MATCH (t:Task) OPTIONAL MATCH (t)-[:CONCERNS]->(f:Fact) OPTIONAL MATCH (t)-[:HANDED_TO]->(h:NPC) WITH t, collect(DISTINCT f)[0] AS f, collect(DISTINCT h)[0] AS h RETURN t.id AS id, t.kind AS kind, t.status AS status, t.posted_by AS posted_by, t.claimed_by AS claimed_by, t.target AS target, t.note AS note, t.result AS result, t.posted_tick AS posted_tick, t.done_tick AS done_tick, f.claim AS claim, h.id AS handed_to ORDER BY t.posted_tick DESC LIMIT 30
```

### `tasks_for_fact`

Tasks that already concern a Fact (avoid duplicate work)

```cypher
MATCH (t:Task)-[:CONCERNS]->(f:Fact {id:$fid}) RETURN t.id AS id, t.kind AS kind, t.status AS status
```

### `guard_cases`

Crimes a guard believes, with any investigation already on the board

```cypher
MATCH (g:NPC {id:$guard})-[b:BELIEVES]->(f:Fact) WHERE b.stance = 'accepted' AND f.predicate IN ['stole', 'lied_about'] AND b.confidence >= $min_conf OPTIONAL MATCH (t:Task {kind:'investigate'})-[:CONCERNS]->(f) OPTIONAL MATCH (c:Task {kind:'confront'})-[:CONCERNS]->(f) RETURN f.id AS fact, f.claim AS claim, f.subject AS subject, f.object_name AS object_name, b.confidence AS confidence, b.via AS via, t.id AS investigation, c.id AS confrontation
```

### `crime_beliefs`

Crimes an NPC believes (reporting candidates)

```cypher
MATCH (me:NPC {id:$id})-[b:BELIEVES]->(f:Fact) WHERE b.stance = 'accepted' AND f.predicate IN ['stole', 'lied_about'] AND b.confidence >= 0.5 RETURN f.id AS fact, f.claim AS claim, b.confidence AS confidence
```

### `first_witness`

Trace a Fact back to an eyewitness of the Event it derives from

```cypher
MATCH (w:NPC)-[x:WITNESSED]->(e:Event)<-[:DERIVED_FROM]-(f:Fact {id:$fid}) OPTIONAL MATCH (e)-[:INVOLVED {role:'actor'}]->(a) WHERE a.id = w.id WITH w, x, a ORDER BY CASE WHEN a IS NULL THEN 0 ELSE 1 END, x.tick RETURN w.id AS id, w.name AS name LIMIT 1
```

### `teachable`

Procedures an NPC is proficient enough to teach

```cypher
MATCH (me:NPC {id:$id})-[k:KNOWS_HOW]->(p:Procedure) WHERE p.teachable = true AND k.proficiency >= 0.7 RETURN p.id AS id, p.name AS name, k.proficiency AS proficiency
```

### `skills_of`

Which procedures each of these NPCs knows

```cypher
MATCH (o:NPC) WHERE o.id IN $ids OPTIONAL MATCH (o)-[:KNOWS_HOW]->(p:Procedure) RETURN o.id AS id, collect(p.id) AS procs
```

### `unseen_thefts`

Owners who are back where their unseen-stolen item was kept

```cypher
MATCH (i:Item {stolen_unseen:true})-[:KEPT_AT]->(l:Location)<-[:IS_AT]-(o:NPC) WHERE o.id = i.original_owner RETURN i.id AS item, i.name AS item_name, o.id AS owner, o.name AS owner_name, l.id AS location
```

### `item_info`

Item with owner and where it is kept

```cypher
MATCH (i:Item {id:$id})-[:OWNED_BY]->(o) OPTIONAL MATCH (i)-[:KEPT_AT]->(l:Location) RETURN i.id AS id, i.name AS name, o.id AS owner, o.name AS owner_name, labels(o)[0] AS owner_label, l.id AS location, i.original_owner AS original_owner
```

### `items_at`

Items kept at a location

```cypher
MATCH (i:Item)-[:KEPT_AT]->(l:Location {id:$loc}) MATCH (i)-[:OWNED_BY]->(o) RETURN i.id AS id, i.name AS name, o.id AS owner, o.name AS owner_name
```

### `state_npcs`

NPCs with positions

```cypher
MATCH (n:NPC)-[:IS_AT]->(l:Location) RETURN n.id AS id, n.name AS name, n.role AS role, n.color AS color, n.personality AS personality, n.chattiness AS chattiness, l.id AS location ORDER BY n.id
```

### `state_locations`

Locations

```cypher
MATCH (l:Location) RETURN l.id AS id, l.name AS name, l.kind AS kind, l.x AS x, l.y AS y
```

### `state_ties`

Undirected view of the social graph

```cypher
MATCH (a:NPC)-[k:KNOWS]->(b:NPC) WHERE a.id < b.id RETURN a.id AS a, b.id AS b, k.affinity AS affinity, k.bond AS bond, k.trust AS trust
```

### `recent_events`

Recent episodic events in town

```cypher
MATCH (e:Event)-[:AT]->(l:Location) WHERE e.tick >= 0 OPTIONAL MATCH (w:NPC)-[:WITNESSED]->(e) RETURN e.id AS id, e.kind AS kind, e.summary AS summary, e.tick AS tick, e.day AS day, e.slot AS slot, l.id AS location, collect(w.id) AS witnesses ORDER BY e.tick DESC LIMIT 25
```

### `graph_counts`

Node counts by label

```cypher
MATCH (n) RETURN labels(n)[0] AS label, count(n) AS n
```

### `edge_counts`

Edge counts by type

```cypher
MATCH ()-[r]->() RETURN type(r) AS type, count(r) AS n
```

### `memory_episodic`

Inspector: an NPC's episodic memories

```cypher
MATCH (me:NPC {id:$npc})-[w:WITNESSED]->(e:Event) OPTIONAL MATCH (e)-[:AT]->(l:Location) OPTIONAL MATCH (e)-[inv:INVOLVED]->(x) RETURN e.id AS id, e.summary AS summary, e.kind AS kind, e.tick AS tick, e.day AS day, l.name AS place, w.vividness AS vividness, collect({id:x.id, name:x.name, label:labels(x)[0], role:inv.role}) AS involved
```

### `memory_semantic`

Inspector: an NPC's beliefs with what they are about

```cypher
MATCH (me:NPC {id:$npc})-[b:BELIEVES]->(f:Fact) OPTIONAL MATCH (f)-[:DERIVED_FROM]->(e:Event) OPTIONAL MATCH (f)-[a:ABOUT]->(x) RETURN f.id AS id, f.claim AS claim, f.kind AS kind, f.truth AS truth, f.importance AS importance, b.confidence AS confidence, b.stance AS stance, b.hop AS hop, b.via AS via, b.source AS source, e.id AS event, collect({id:x.id, name:x.name, label:labels(x)[0], role:a.role}) AS about
```

### `memory_tellers`

Inspector: who told this NPC what, and whom it told

```cypher
MATCH (me:NPC {id:$npc}) OPTIONAL MATCH (src)-[t:TOLD]->(me) WITH me, collect(DISTINCT {id:src.id, name:src.name, label:labels(src)[0], fact:t.fact_id, tick:t.tick, primary:t.primary}) AS heard_from OPTIONAL MATCH (me)-[t2:TOLD]->(dst:NPC) RETURN heard_from, collect(DISTINCT {id:dst.id, name:dst.name, fact:t2.fact_id, tick:t2.tick}) AS told
```

### `memory_tasks`

Inspector: tasks an NPC posted or claimed

```cypher
MATCH (t:Task)-[r:POSTED_BY|CLAIMED_BY]->(me:NPC {id:$npc}) RETURN DISTINCT t.id AS id, t.kind AS kind, t.status AS status, type(r) AS rel, t.fact_id AS fact, t.result AS result
```

### `most_talkative`

Out-degree of TOLD: who passes on the most stories

```cypher
MATCH (n:NPC) OPTIONAL MATCH (n)-[t:TOLD]->(:NPC) RETURN n.id AS npc, count(t) AS told ORDER BY told DESC
```

## Writes

### `create_town`

Create the per-tenant Town node holding the clock

```cypher
CREATE (:Town {id:'town', name:$name, tick:0, seed:$seed, player_name:$player_name, created_at:$now, last_active:$now, sessions:0})
```

### `create_locations`

Create locations

```cypher
UNWIND $rows AS r CREATE (:Location {id:r.id, name:r.name, kind:r.kind, x:r.x, y:r.y})
```

### `create_npcs`

Create NPC agents and place them

```cypher
UNWIND $rows AS r MATCH (l:Location {id:r.start}) CREATE (n:NPC {id:r.id, name:r.name, role:r.role, personality:r.personality, chattiness:r.chattiness, credibility:r.credibility, color:r.color, schedule:r.schedule}) CREATE (n)-[:IS_AT]->(l)
```

### `create_player`

Create the player

```cypher
MATCH (l:Location {id:'square'}) CREATE (p:Player {id:'player', name:$name})-[:IS_AT]->(l)
```

### `create_ties`

Create directed social edges (trust, affinity) and CLOSE edges

```cypher
UNWIND $rows AS r MATCH (a:NPC {id:r.a}), (b:NPC {id:r.b}) CREATE (a)-[:KNOWS {trust:r.trust, affinity:r.affinity, bond:r.bond, distance:r.distance}]->(b) FOREACH (_ IN CASE WHEN r.affinity >= 0.55 THEN [1] ELSE [] END | CREATE (a)-[:CLOSE]->(b))
```

### `create_items_npc`

Create NPC-owned items

```cypher
UNWIND $rows AS r MATCH (o:NPC {id:r.owner}), (l:Location {id:r.location}) CREATE (i:Item {id:r.id, name:r.name, value:r.value, original_owner:r.owner, stolen_unseen:false}) CREATE (i)-[:OWNED_BY]->(o) CREATE (i)-[:KEPT_AT]->(l)
```

### `create_items_player`

Create player-owned items

```cypher
UNWIND $rows AS r MATCH (p:Player) CREATE (i:Item {id:r.id, name:r.name, value:r.value, original_owner:'player', stolen_unseen:false}) CREATE (i)-[:OWNED_BY]->(p)
```

### `create_procedures`

Create procedural-memory nodes

```cypher
UNWIND $rows AS r CREATE (:Procedure {id:r.id, name:r.name, steps:r.steps, teachable:r.teachable})
```

### `create_skills`

Give NPCs procedures (KNOWS_HOW)

```cypher
UNWIND $rows AS r MATCH (n:NPC {id:r.npc}), (p:Procedure {id:r.proc}) CREATE (n)-[:KNOWS_HOW {proficiency:r.proficiency, learned_from:'upbringing', tick:0}]->(p)
```

### `build_world`

Create the whole town in one round trip: clock, places, agents, player, social graph, items, skills

```cypher
CREATE (:Town {id:'town', name:$name, tick:0, seed:$seed, player_name:$player_name, created_at:$now, last_active:$now, sessions:0}) WITH count(*) AS _ UNWIND $locations AS r CREATE (:Location {id:r.id, name:r.name, kind:r.kind, x:r.x, y:r.y}) WITH count(*) AS _ UNWIND $npcs AS r MATCH (l:Location {id:r.start}) CREATE (n:NPC {id:r.id, name:r.name, role:r.role, personality:r.personality, chattiness:r.chattiness, credibility:r.credibility, color:r.color, schedule:r.schedule})-[:IS_AT]->(l) WITH count(*) AS _ MATCH (sq:Location {id:'square'}) CREATE (p:Player {id:'player', name:$player_name})-[:IS_AT]->(sq) WITH count(*) AS _ UNWIND $ties AS r MATCH (a:NPC {id:r.a}), (b:NPC {id:r.b}) CREATE (a)-[:KNOWS {trust:r.trust, affinity:r.affinity, bond:r.bond, distance:r.distance}]->(b) FOREACH (_x IN CASE WHEN r.affinity >= 0.55 THEN [1] ELSE [] END | CREATE (a)-[:CLOSE]->(b)) WITH count(*) AS _ UNWIND $npc_items AS r MATCH (o:NPC {id:r.owner}), (l:Location {id:r.location}) CREATE (i:Item {id:r.id, name:r.name, value:r.value, original_owner:r.owner, stolen_unseen:false}) CREATE (i)-[:OWNED_BY]->(o) CREATE (i)-[:KEPT_AT]->(l) WITH count(*) AS _ UNWIND $player_items AS r MATCH (p:Player) CREATE (i:Item {id:r.id, name:r.name, value:r.value, original_owner:'player', stolen_unseen:false}) CREATE (i)-[:OWNED_BY]->(p) WITH count(*) AS _ UNWIND $procedures AS r CREATE (:Procedure {id:r.id, name:r.name, steps:r.steps, teachable:r.teachable}) WITH count(*) AS _ UNWIND $skills AS r MATCH (n:NPC {id:r.npc}), (pr:Procedure {id:r.proc}) CREATE (n)-[:KNOWS_HOW {proficiency:r.proficiency, learned_from:'upbringing', tick:0}]->(pr) RETURN count(*) AS skills
```

### `advance_clock`

Advance the clock one tick

```cypher
MATCH (t:Town) SET t.tick = t.tick + 1, t.last_active = $now RETURN t.tick AS tick
```

### `start_session`

Open a new play session (persistent history across sessions)

```cypher
MATCH (p:Player), (t:Town) SET t.sessions = coalesce(t.sessions, 0) + 1, t.last_active = $now CREATE (s:Session {id:'s' + toString(t.sessions), n:t.sessions, started_at:$now, start_tick:t.tick}) CREATE (p)-[:HAD_SESSION]->(s) RETURN s.n AS n, s.start_tick AS start_tick
```

### `move_npc`

Move an NPC (re-point its IS_AT edge)

```cypher
MATCH (n:NPC {id:$id})-[r:IS_AT]->(old:Location) MATCH (l:Location {id:$loc}) WHERE old.id <> $loc DELETE r CREATE (n)-[:IS_AT]->(l) RETURN old.id AS from
```

### `move_player`

Move the player

```cypher
MATCH (p:Player)-[r:IS_AT]->(old:Location) MATCH (l:Location {id:$loc}) WHERE old.id <> $loc DELETE r CREATE (p)-[:IS_AT]->(l) RETURN old.id AS from
```

### `create_event`

Record an episodic Event at a location

```cypher
MATCH (l:Location {id:$loc}) OPTIONAL MATCH (s:Session {n:$session}) CREATE (e:Event {id:$eid, tick:$tick, day:$day, slot:$slot, kind:$kind, summary:$summary, session:$session})-[:AT]->(l) FOREACH (_ IN CASE WHEN s IS NULL THEN [] ELSE [1] END | CREATE (e)-[:DURING]->(s)) RETURN e.id AS id
```

### `involve_npc`

Link an Event to the NPCs involved (actor/victim/recipient)

```cypher
UNWIND $rows AS r MATCH (e:Event {id:$eid}), (n:NPC {id:r.id}) CREATE (e)-[:INVOLVED {role:r.role}]->(n)
```

### `involve_player`

Link an Event to the player

```cypher
MATCH (e:Event {id:$eid}), (p:Player) CREATE (e)-[:INVOLVED {role:$role}]->(p)
```

### `involve_item`

Link an Event to an item

```cypher
MATCH (e:Event {id:$eid}), (i:Item {id:$item}) CREATE (e)-[:INVOLVED {role:'object'}]->(i)
```

### `witness`

NPCs present form an episodic memory (WITNESSED)

```cypher
UNWIND $ids AS wid MATCH (n:NPC {id:wid}), (e:Event {id:$eid}) CREATE (n)-[:WITNESSED {tick:$tick, vividness:1.0}]->(e)
```

### `create_fact`

Create (or reuse) a semantic Fact, linked to the Event it derives from

```cypher
MERGE (f:Fact {id:$fid}) ON CREATE SET f.subject=$subject, f.subject_name=$subject_name, f.predicate=$predicate, f.object=$object, f.object_name=$object_name, f.claim=$claim, f.truth=$truth, f.importance=$importance, f.valence=$valence, f.kind=$kind, f.created_tick=$tick WITH f OPTIONAL MATCH (e:Event {id:$eid}) FOREACH (_ IN CASE WHEN e IS NULL THEN [] ELSE [1] END | MERGE (f)-[:DERIVED_FROM]->(e)) RETURN f.id AS id
```

### `about_npc`

Link a Fact to the NPC it is about

```cypher
MATCH (f:Fact {id:$fid}), (n:NPC {id:$id}) MERGE (f)-[:ABOUT {role:$role}]->(n)
```

### `about_player`

Link a Fact to the player

```cypher
MATCH (f:Fact {id:$fid}), (p:Player) MERGE (f)-[:ABOUT {role:$role}]->(p)
```

### `about_item`

Link a Fact to an item

```cypher
MATCH (f:Fact {id:$fid}), (i:Item {id:$id}) MERGE (f)-[:ABOUT {role:$role}]->(i)
```

### `link_contradictions`

Mark conflicting claims: same predicate+object, different subject

```cypher
MATCH (f:Fact {id:$fid}), (g:Fact {predicate:f.predicate, object:f.object}) WHERE g.id <> f.id AND g.subject <> f.subject AND g.subject <> 'someone' AND f.subject <> 'someone' MERGE (f)-[:CONTRADICTS]->(g) RETURN g.id AS rival
```

### `believe`

Create/strengthen BELIEVES edges (witnesses, notes)

```cypher
UNWIND $rows AS r MATCH (n:NPC {id:r.id}), (f:Fact {id:$fid}) MERGE (n)-[b:BELIEVES]->(f) ON CREATE SET b.confidence=r.conf, b.stance='accepted', b.hop=r.hop, b.via=r.via, b.since_tick=$tick, b.source=r.source, b.corroborations=0 ON MATCH SET b.confidence = CASE WHEN b.confidence < r.conf THEN r.conf ELSE b.confidence END, b.source = CASE WHEN r.source = 'witnessed' THEN 'witnessed' ELSE b.source END, b.hop = CASE WHEN r.source = 'witnessed' THEN 0 ELSE b.hop END, b.stance = 'accepted' RETURN count(b) AS n
```

### `tell`

Gossip handoff: speaker passes a Fact to a listener; confidence decays per hop; TOLD edge keeps provenance

```cypher
MATCH (a:NPC {id:$from})-[:BELIEVES]->(f:Fact {id:$fid}), (b:NPC {id:$to}) MERGE (b)-[bel:BELIEVES]->(f) ON CREATE SET bel.confidence=$conf, bel.stance = CASE WHEN $conf >= $accept THEN 'accepted' ELSE 'doubted' END, bel.hop=$hop, bel.via=$from, bel.since_tick=$tick, bel.source='told', bel.corroborations=0 ON MATCH SET bel.confidence = CASE WHEN bel.source = 'witnessed' THEN bel.confidence ELSE bel.confidence + (1.0 - bel.confidence) * $conf * 0.5 END, bel.corroborations = coalesce(bel.corroborations, 0) + 1 MERGE (a)-[t:TOLD {fact_id:$fid}]->(b) ON CREATE SET t.tick=$tick, t.confidence=$conf, t.times=1, t.channel=$channel ON MATCH SET t.times = t.times + 1 SET t.primary = coalesce(t.primary, false) OR coalesce(bel.via = $from, false) RETURN bel.confidence AS confidence, bel.stance AS stance, bel.hop AS hop, coalesce(bel.via = $from AND bel.since_tick = $tick, false) AS first_time
```

### `player_tell`

The player tells an NPC a claim (possibly a lie)

```cypher
MATCH (p:Player), (f:Fact {id:$fid}), (b:NPC {id:$to}) MERGE (b)-[bel:BELIEVES]->(f) ON CREATE SET bel.confidence=$conf, bel.stance = CASE WHEN $conf >= $accept THEN 'accepted' ELSE 'doubted' END, bel.hop=1, bel.via='player', bel.since_tick=$tick, bel.source='told', bel.corroborations=0 ON MATCH SET bel.corroborations = coalesce(bel.corroborations, 0) + 1 MERGE (p)-[t:TOLD {fact_id:$fid}]->(b) ON CREATE SET t.tick=$tick, t.confidence=$conf, t.times=1, t.channel='claim', t.primary = coalesce(bel.via = 'player', false) ON MATCH SET t.times = t.times + 1 RETURN bel.confidence AS confidence, bel.stance AS stance
```

### `promote_beliefs`

Doubted beliefs that gathered enough corroboration become accepted

```cypher
MATCH (n:NPC {id:$npc})-[b:BELIEVES]->(f:Fact {id:$fid}) WHERE b.stance = 'doubted' AND b.confidence >= $accept SET b.stance = 'accepted' RETURN count(b) AS n
```

### `resolve_conflict`

Conflicting beliefs held by one NPC: the highest-confidence (trust-weighted) claim wins, every rival is doubted

```cypher
MATCH (n:NPC {id:$npc})-[b1:BELIEVES]->(f:Fact {id:$fid})-[:CONTRADICTS]-(g:Fact)<-[b2:BELIEVES]-(n) WHERE b1.stance <> 'faded' AND b2.stance <> 'faded' WITH n, b1, f, g, b2 ORDER BY b2.confidence DESC, g.id WITH n, b1, f, collect(g.id)[0] AS best_rival, max(b2.confidence) AS top WITH n, b1, f, CASE WHEN b1.confidence > top THEN f.id ELSE best_rival END AS winner MATCH (n)-[b2:BELIEVES]->(g:Fact)-[:CONTRADICTS]-(f) WHERE b2.stance <> 'faded' WITH DISTINCT b1, f, winner, b2, g SET b1.stance = CASE WHEN winner = f.id THEN 'accepted' ELSE 'doubted' END, b2.stance = CASE WHEN winner = g.id THEN 'accepted' ELSE 'doubted' END RETURN f.id AS fact, g.id AS rival, winner, b1.confidence AS c_fact, b2.confidence AS c_rival, f.claim AS fact_claim, g.claim AS rival_claim
```

### `decay_beliefs`

Forgetting: hearsay confidence decays each dawn; weak beliefs fade

```cypher
MATCH (:NPC)-[b:BELIEVES]->(:Fact) WHERE b.source <> 'witnessed' AND b.stance <> 'faded' SET b.confidence = b.confidence * $factor, b.stance = CASE WHEN b.confidence * $factor < 0.12 THEN 'faded' ELSE b.stance END RETURN count(b) AS n
```

### `decay_episodic`

Episodic vividness decays each dawn

```cypher
MATCH (:NPC)-[w:WITNESSED]->(:Event) SET w.vividness = w.vividness * $factor RETURN count(w) AS n
```

### `advance_clock_read`

Advance the clock and return what a tick needs to know

```cypher
MATCH (t:Town) SET t.tick = t.tick + 1, t.last_active = $now RETURN t.tick AS tick, t.seed AS seed, t.sessions AS sessions, t.player_name AS player_name
```

### `tell_and_settle`

Gossip handoff in one statement: belief + provenance edge + promotion of corroborated doubts; reports whether the fact is contested

```cypher
MATCH (a:NPC {id:$from})-[:BELIEVES]->(f:Fact {id:$fid}), (b:NPC {id:$to}) MERGE (b)-[bel:BELIEVES]->(f) ON CREATE SET bel.confidence=$conf, bel.stance = CASE WHEN $conf >= $accept THEN 'accepted' ELSE 'doubted' END, bel.hop=$hop, bel.via=$from, bel.since_tick=$tick, bel.source='told', bel.corroborations=0 ON MATCH SET bel.confidence = CASE WHEN bel.source = 'witnessed' THEN bel.confidence ELSE bel.confidence + (1.0 - bel.confidence) * $conf * 0.5 END, bel.corroborations = coalesce(bel.corroborations, 0) + 1 MERGE (a)-[t:TOLD {fact_id:$fid}]->(b) ON CREATE SET t.tick=$tick, t.confidence=$conf, t.times=1, t.channel=$channel ON MATCH SET t.times = t.times + 1 SET t.primary = coalesce(t.primary, false) OR coalesce(bel.via = $from, false), bel.stance = CASE WHEN bel.stance = 'doubted' AND bel.confidence >= $accept THEN 'accepted' ELSE bel.stance END WITH bel, f OPTIONAL MATCH (f)-[:CONTRADICTS]-(g:Fact) RETURN bel.confidence AS confidence, bel.stance AS stance, bel.hop AS hop, coalesce(bel.via = $from AND bel.since_tick = $tick, false) AS first_time, count(g) > 0 AS contested
```

### `claim_and_trace`

Atomic claim of an investigation that also traces the story to its first eyewitness

```cypher
MATCH (t:Task {id:$tid}) WHERE t.status = 'open' MATCH (n:NPC {id:$npc})-[:KNOWS_HOW]->(p:Procedure) WHERE p.id = t.required SET t.status = 'claimed', t.claimed_tick = $tick, t.claimed_by = $npc CREATE (t)-[:CLAIMED_BY]->(n) WITH t OPTIONAL MATCH (w:NPC)-[x:WITNESSED]->(:Event)<-[:DERIVED_FROM]-(:Fact {id:t.fact_id}) WITH t, w, x ORDER BY x.tick, w.id LIMIT 1 FOREACH (_x IN CASE WHEN w IS NULL THEN [] ELSE [1] END | SET t.target = w.id, t.note = 'Story traced back to eyewitness ' + w.name MERGE (t)-[:FOUND]->(w)) RETURN t.id AS id, w.id AS witness, w.name AS witness_name
```

### `record_deed`

Episodic Event and the semantic Fact derived from it, in one statement

```cypher
MATCH (l:Location {id:$loc}) OPTIONAL MATCH (s:Session {n:$session}) CREATE (e:Event {id:$eid, tick:$tick, day:$day, slot:$slot, kind:$kind, summary:$summary, session:$session})-[:AT]->(l) FOREACH (_x IN CASE WHEN s IS NULL THEN [] ELSE [1] END | CREATE (e)-[:DURING]->(s)) MERGE (f:Fact {id:$fid}) ON CREATE SET f.subject=$subject, f.subject_name=$subject_name, f.predicate=$predicate, f.object=$object, f.object_name=$object_name, f.claim=$claim, f.truth=$truth, f.importance=$importance, f.valence=$valence, f.kind=$fkind, f.created_tick=$tick MERGE (f)-[:DERIVED_FROM]->(e) RETURN e.id AS event, f.id AS fact
```

### `witness_and_believe`

Witnesses form the episodic memory and believe the derived Fact

```cypher
UNWIND $ids AS wid MATCH (n:NPC {id:wid}), (e:Event {id:$eid}), (f:Fact {id:$fid}) CREATE (n)-[:WITNESSED {tick:$tick, vividness:1.0}]->(e) MERGE (n)-[b:BELIEVES]->(f) ON CREATE SET b.confidence=$conf, b.stance='accepted', b.hop=0, b.via=null, b.since_tick=$tick, b.source='witnessed', b.corroborations=0 ON MATCH SET b.confidence=$conf, b.stance='accepted', b.hop=0, b.source='witnessed' RETURN count(b) AS n
```

### `record_memory`

An NPC records a note about an entity as its own semantic memory

```cypher
MATCH (me:NPC {id:$npc}) MERGE (f:Fact {id:$fid}) ON CREATE SET f.subject=$subject, f.subject_name=$subject_name, f.predicate='noted', f.object=$npc, f.object_name=me.name, f.claim=$claim, f.truth=null, f.importance=0.25, f.valence=$valence, f.kind='note', f.created_tick=$tick MERGE (me)-[b:BELIEVES]->(f) ON CREATE SET b.confidence=0.9, b.stance='accepted', b.hop=0, b.via=null, b.since_tick=$tick, b.source='noted', b.corroborations=0 RETURN f.id AS fact
```

### `note_about_npc`

Link a note to the NPC it is about

```cypher
MATCH (f:Fact {id:$fid}), (n:NPC {id:$id}) MERGE (f)-[:ABOUT {role:'subject'}]->(n)
```

### `note_about_player`

Link a note to the player

```cypher
MATCH (f:Fact {id:$fid}), (p:Player) MERGE (f)-[:ABOUT {role:'subject'}]->(p)
```

### `post_task`

Post a Task on the shared board (idempotent per id)

```cypher
MATCH (n:NPC {id:$by}), (f:Fact {id:$fid}) MERGE (t:Task {id:$tid}) ON CREATE SET t.kind=$kind, t.status=$status, t.fact_id=$fid, t.posted_tick=$tick, t.required=$required, t.target=$target, t.note=$note, t.posted_by=$by, t.claimed_by=$claimed_by MERGE (t)-[:POSTED_BY]->(n) MERGE (t)-[:CONCERNS]->(f) FOREACH (_x IN CASE WHEN $claimed_by IS NULL THEN [] ELSE [1] END | MERGE (t)-[:CLAIMED_BY]->(n)) RETURN t.posted_tick = $tick AND t.posted_by = $by AS created, t.status AS status
```

### `self_assign`

Poster assigns a task to itself

```cypher
MATCH (t:Task {id:$tid}), (n:NPC {id:$npc}) MERGE (t)-[:CLAIMED_BY]->(n)
```

### `claim_task`

Atomic claim: only succeeds while the task is open and the claimer KNOWS_HOW the required procedure

```cypher
MATCH (t:Task {id:$tid}) WHERE t.status = 'open' MATCH (n:NPC {id:$npc})-[:KNOWS_HOW]->(p:Procedure) WHERE p.id = t.required SET t.status = 'claimed', t.claimed_tick = $tick, t.claimed_by = $npc CREATE (t)-[:CLAIMED_BY]->(n) RETURN t.id AS id
```

### `set_task_target`

Record where a claimed task must go next

```cypher
MATCH (t:Task {id:$tid}) SET t.target = $target, t.note = $note RETURN t.id AS id
```

### `complete_task`

Complete a task, optionally handing its result to another agent

```cypher
MATCH (t:Task {id:$tid}) WHERE t.status <> 'done' SET t.status = 'done', t.done_tick = $tick, t.result = $result WITH t OPTIONAL MATCH (h:NPC {id:$handoff}) FOREACH (_ IN CASE WHEN h IS NULL THEN [] ELSE [1] END | CREATE (t)-[:HANDED_TO {tick:$tick}]->(h)) RETURN t.id AS id
```

### `task_found`

Investigation result: link the Task to the first witness it found

```cypher
MATCH (t:Task {id:$tid}), (n:NPC {id:$npc}) MERGE (t)-[:FOUND]->(n)
```

### `corroborate`

Handoff of findings raises the receiver's confidence

```cypher
MATCH (n:NPC {id:$npc})-[b:BELIEVES]->(f:Fact {id:$fid}) SET b.confidence = CASE WHEN b.confidence < $conf THEN $conf ELSE b.confidence END, b.corroborated_by = $by, b.stance = 'accepted' RETURN b.confidence AS confidence
```

### `teach`

Procedural handoff: a teacher passes a procedure on at reduced proficiency

```cypher
MATCH (a:NPC {id:$teacher})-[k:KNOWS_HOW]->(p:Procedure {id:$proc}), (b:NPC {id:$learner}) WHERE NOT (b)-[:KNOWS_HOW]->(p) CREATE (b)-[:KNOWS_HOW {proficiency: k.proficiency * 0.5, learned_from:$teacher, tick:$tick}]->(p) RETURN p.name AS name
```

### `mark_item`

Set an item's unseen-theft flag

```cypher
MATCH (i:Item {id:$id}) SET i.stolen_unseen = $flag
```

### `transfer_to_player`

Ownership of an item moves to the player

```cypher
MATCH (i:Item {id:$id})-[r:OWNED_BY]->() MATCH (p:Player) DELETE r CREATE (i)-[:OWNED_BY]->(p)
```

### `transfer_to_npc`

Ownership of an item moves to an NPC

```cypher
MATCH (i:Item {id:$id})-[r:OWNED_BY]->() MATCH (n:NPC {id:$npc}) DELETE r CREATE (i)-[:OWNED_BY]->(n)
```

