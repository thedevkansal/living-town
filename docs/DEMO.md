# Demo script (about 2.5 minutes)

The click-by-click path used for the submission video. Works on the live site
(https://living-town.onrender.com) or locally (http://localhost:5174). On the free
Render tier, open the site a minute before recording so it is awake.

1. **Join.** Type `Ada`, press *Enter the town*. (Pitch: every NPC's memory is a FalkorDB subgraph.)
2. **Walk.** Click *The Forge* on the map. Press *Wait an hour*: it is morning and Bram is at his anvil.
3. **Steal.** Press *Steal the silver hammer* in the action bar. Bram shouts; the log says "Seen by Bram".
4. **Let time pass.** Press *Live through the day* and narrate as it plays: at midday Bram tells Mira at the Crooked Kettle (blue spark); in the evening Mira tells Captain Oswin; Oswin posts an investigation; Finn claims it from the task board, walks to Bram at night and takes his testimony; at dawn Oswin walks to the Forge and confronts you.
5. **Ask how he knows.** Click Oswin's avatar. In his memory press *Why do you believe that?* on "Ada stole Bram's silver hammer". The red thread runs Ada, Bram, Mira, Oswin across the map; the card shows the path and the narrative. Point at the memory graph: ember triangles are things he saw, blue dots are beliefs, green diamonds are procedures, purple squares are tasks.
6. **Honesty.** Close the inspector, click Sister Agnes, press the quick question *What about the hammer?* She answers "Silver hammer? Nothing in my memory connects to that."
7. **Lie.** Walk to the Crooked Kettle, select Mira, press *Spread a rumour*: "Pip stole the silver hammer". Mira trusts her own source more and calls you out; the conflict and the new story appear in the log.
8. **Graph algorithms.** Open *Graph insights*: cliques (label propagation, toggle *Show on the map*), gossip brokers (betweenness: Mira), best informed (PageRank over `TOLD`). Press *Run the stress test*: 40 agents in 4 worlds reading and writing concurrently, with writes per second.
9. **Tenant isolation.** Press *Two players side by side*: Bo's Thornbury opens next to Ada's, in its own graph `town:bo`. Nobody there has heard of any hammer.
10. **Persistence.** Press *Switch player*, return as `Ada`. Bram, Mira and Oswin greet you with what they remember and who told them.

Optional close (10 seconds): show `docs/CYPHER.md` or the FalkorDB Browser on the `town:ada` graph to prove the memory is the graph.
