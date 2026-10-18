# Demo script (about 2.5 minutes)

The click-by-click path used for the submission video. Works on the live site
(https://living-town.onrender.com) or locally (http://localhost:5174). On the free
Render tier, open the site a minute before recording so it is awake.

**Recording setup.** Record at 1920x1080. Add `?slow` to the URL
(https://living-town.onrender.com/?slow) for recording pace: each hour takes about 2 s and
the beats inside an hour are spaced out, so a whole day plays in roughly 25 s and every
spark, bubble and log line can be narrated. Without `?slow` a day plays in about 10 s.
Use a fresh name per take (or *Start Ada's world over* on the join screen) so the story
starts from scratch.

1. **Join.** Type `Ada`, press *Enter the town*. (Pitch: every NPC's memory is a FalkorDB subgraph.)
2. **Walk.** Click *The Forge* on the map. Press *Wait an hour*: it is morning and Bram is at his anvil.
3. **Steal.** Press *Steal the silver hammer* in the action bar. Bram shouts; the log line reads
   "Ada stole Bram's silver hammer. Seen by Bram." Click *Track this story* under that line: from now on
   the map rings everyone who believes it (blue), doubts it (grey) or saw it (ember), and draws who told whom.
4. **Let time pass.** Press *Sleep till dawn* (after *Wait an hour* the day button says this; at dawn it
   reads *Live through the day*). Narrate as it plays:
   - midday: Bram tells Mira at the Crooked Kettle (blue spark, Mira's ring lights up);
   - evening: Mira tells Captain Oswin;
   - night: Oswin posts an *investigate* and a *confront* task, and Finn claims the investigation.
     Open the *Task board* tab here to show the shared board: who posted, who claimed;
   - dawn: Oswin and Finn walk to the Forge. Oswin confronts you ("Mira told me, and it traces back to
     Bram, who saw it"), then Bram gives Finn his eyewitness testimony and Finn hands his findings to
     Oswin, whose belief jumps (the log shows e.g. "Captain Oswin 41% → 97% sure").
   Switch back to *Town talk*; tick *Only stories about you* if the background gossip distracts.
5. **Ask how he knows.** Click Oswin's avatar. In his memory press *Why do you believe that?* on
   "Ada stole Bram's silver hammer". The red thread runs Ada, Bram, Mira, Oswin across the map; the
   "How Captain Oswin knows" card shows the path, the narrative and his confidence now. Point at the
   memory graph: each memory type has its own corner, *Saw · episodic* (ember triangles, things he
   witnessed), *Believes · semantic* (blue dots, sized by confidence), *Knows how · procedural* (green
   diamonds), *Tasks* (purple squares). The legend above it counts each type.
6. **Honesty.** Close the inspector (the thread clears), click Sister Agnes, press the quick question
   *What about the hammer?* She answers "Silver hammer? Nothing in my memory connects to that."
7. **Lie.** Walk to the Crooked Kettle, click *Mira* in the action bar, press *Spread a rumour*, leave
   "Pip … silver hammer" and press *Say it*. Mira trusts her own source more and calls you out; the log shows
   her weighing the two stories and the new story about you.
8. **Graph algorithms.** Open *Graph insights*: cliques (label propagation, tick *Show on the map*), gossip
   brokers (betweenness: Mira), best informed (PageRank over `TOLD`). Press *Run the stress test* (about 8 s):
   40 agents in 4 worlds reading and writing concurrently, with writes per second.
9. **Tenant isolation.** Press *Two players side by side*: a brand-new neighbour, Bo, opens next to Ada in
   its own graph `town:ada-2` (created fresh each time). Nobody there has heard of any hammer.
10. **Persistence.** Press *One world*, then *Switch player*, and return as `Ada` (*Return as Ada*). Bram,
    Mira, Oswin and Finn greet you with what they remember and who told them, and *Town talk* opens with
    those memories and the earlier events read back from the graph.

Optional close (10 seconds): show `docs/CYPHER.md` or the FalkorDB Browser on the `town:ada` graph to prove the memory is the graph.
