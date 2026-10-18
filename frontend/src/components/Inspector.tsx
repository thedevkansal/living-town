import { useEffect, useRef, useState } from 'react'
import { api, type Dialogue, type Memory, type MemNode, type Provenance } from '../api'
import { pct, type World } from '../useWorld'
import { MemoryGraph } from './MemoryGraph'

export function Inspector({ world, pid, npc, onClose }: { world: World; pid: string; npc: string; onClose: () => void }) {
  const [mem, setMem] = useState<Memory | null>(null)
  const [focus, setFocus] = useState<string | null>(null)
  const [asking, setAsking] = useState<string | null>(null)
  const [msg, setMsg] = useState('')
  const [dialogue, setDialogue] = useState<Dialogue | null>(null)
  const [sending, setSending] = useState(false)
  const tick = world.state?.clock.tick
  const here = world.state?.npcs.find((n) => n.id === npc)
  const prov = world.highlight && world.highlight.chain.at(-1) === npc ? world.highlight : null

  useEffect(() => {
    let live = true
    api.memory(pid, npc).then((m) => live && setMem(m)).catch(() => live && setMem(null))
    return () => { live = false }
  }, [pid, npc, tick])
  useEffect(() => { setDialogue(null); setFocus(null); setMsg('') }, [npc])

  const ask = async (fact: string) => {
    setAsking(fact); setFocus(fact)
    try {
      const p = await api.provenance(pid, npc, fact)
      world.setHighlight(p)
      world.setFocusFact(fact)
    } catch (e) { world.notify((e as Error).message, 'err') } finally { setAsking(null) }
  }

  const send = async (text: string) => {
    if (!text.trim()) return
    setSending(true)
    try {
      const d = await api.talk(pid, npc, text)
      setDialogue(d)
      // The talk reply carries only a summary of the provenance (no nodes/edges), so fetch the
      // full path for the cited fact before drawing the thread and the "How X knows" card.
      const fact = d.provenance?.found ? d.facts?.[0] : undefined
      if (fact) {
        const p = await api.provenance(pid, npc, fact)
        if (p.found) { world.setHighlight(p); world.setFocusFact(fact) }
      }
    } catch (e) { world.notify((e as Error).message, 'err') } finally { setSending(false); setMsg('') }
  }

  const pick = (n: MemNode) => {
    if (n.label === 'Fact') ask(n.id)
    else setFocus(n.id === focus ? null : n.id)
  }

  const op = mem?.opinion
  const count = (label: string) => mem?.nodes.filter((n) => n.label === label && !(label === 'Fact' && n.stance === 'faded')).length ?? 0
  return (
    <aside className="inspector" aria-label={`Memory of ${here?.name ?? npc}`}>
      <header className="insp__head" style={{ ['--c' as string]: here?.color ?? mem?.npc.color }}>
        <div className="insp__face">{(here?.name ?? npc).split(' ').pop()!.slice(0, 1)}</div>
        <div className="insp__who">
          <h2>{here?.name ?? mem?.npc.name}</h2>
          <p>{here?.role} at {world.state?.locations.find((l) => l.id === here?.location)?.name}</p>
          <p className="insp__persona">{mem?.npc.personality}</p>
        </div>
        <button className="icon-btn" onClick={onClose} aria-label="Close memory inspector">×</button>
      </header>

      {op && (
        <div className="opinion" title={`${op.facts} beliefs about you`}>
          <span>Feels {op.mood} you</span>
          <div className="opinion__bar"><i style={{ left: `${50 + Math.max(-1, Math.min(1, op.score)) * 50}%` }} /></div>
        </div>
      )}

      <form className="talk" onSubmit={(e) => { e.preventDefault(); send(msg) }}>
        <input value={msg} onChange={(e) => setMsg(e.target.value)} placeholder={`Say something to ${here?.name.split(' ').pop()}`} aria-label="Message" disabled={sending} />
        <button className="btn btn--ink" disabled={sending || !msg.trim()}>{sending ? 'Thinking' : 'Say'}</button>
      </form>
      <div className="talk__quick">
        {['How do you know?', 'Any news?', 'What about the hammer?', 'I am sorry.'].map((q) => (
          <button key={q} className="chip" onClick={() => send(q)} disabled={sending}>{q}</button>
        ))}
      </div>
      {dialogue && (
        <div className="reply">
          <p>{dialogue.reply}</p>
          <div className="reply__meta">
            <span className={`badge badge--${dialogue.mode}`}>{dialogue.mode === 'llm' ? `Claude (${dialogue.model})` : 'Scripted from memory'}</span>
            {dialogue.tools.map((t, i) => <code key={i}>{t.name}({Object.values(t.input).map(String).join(', ')})</code>)}
            {dialogue.fallback_reason && <span className="reply__warn">LLM answer rejected: {dialogue.fallback_reason}</span>}
          </div>
        </div>
      )}

      {prov && <ProvenanceCard prov={prov} world={world} />}

      <section className="insp__sec">
        <h3>Memory graph</h3>
        <div className="legend" aria-label="Memory types">
          <span className="lg lg--episodic">Saw <i>= episodic</i> <b className="num">{count('Event')}</b></span>
          <span className="lg lg--semantic">Believes <i>= semantic</i> <b className="num">{count('Fact')}</b></span>
          <span className="lg lg--procedural">Knows how <i>= procedural</i> <b className="num">{count('Procedure')}</b></span>
          <span className="lg lg--coordination">Tasks <b className="num">{count('Task')}</b></span>
          {mem?.nodes.some((n) => n.label === 'Fact' && n.stance === 'doubted') && <span className="lg lg--doubted">Doubts</span>}
        </div>
        {mem ? <MemoryGraph nodes={mem.nodes} edges={mem.edges} focus={focus} onPick={pick} /> : <div className="mgraph mgraph--empty">Reading memory</div>}
      </section>

      <section className="insp__sec">
        <h3>Beliefs</h3>
        <ul className="beliefs">
          {mem?.beliefs.filter((b) => b.stance !== 'faded').map((b) => (
            <li key={b.fact} className={`belief belief--${b.stance} ${focus === b.fact ? 'is-focus' : ''}`}>
              <div className="belief__claim">{b.claim}</div>
              <div className="belief__meta">
                <span className="conf"><i style={{ width: pct(b.confidence) }} /></span>
                <span>{pct(b.confidence)} {b.stance === 'doubted' ? 'and doubtful' : ''}</span>
                <span>{b.source === 'witnessed' ? 'saw it' : b.source === 'noted' ? 'own note' : b.via === 'player' ? 'from you' : `from ${b.via_name ?? b.via}`}</span>
              </div>
              <button className="why" onClick={() => ask(b.fact)} disabled={asking === b.fact}>
                {asking === b.fact ? 'Tracing' : 'Why do you believe that?'}
              </button>
            </li>
          ))}
          {mem && !mem.beliefs.length && <li className="empty">Nothing in memory yet.</li>}
        </ul>
      </section>

      <section className="insp__sec">
        <h3>Knows how to</h3>
        <ul className="procs">
          {mem?.procedures.map((p) => (
            <li key={p.id}>
              <span className="procs__name">{p.name}</span>
              <span className="conf conf--moss"><i style={{ width: pct(p.proficiency) }} /></span>
              <small>{p.learned_from === 'upbringing' ? 'since childhood' : `taught by ${p.learned_from_name ?? p.learned_from}`}</small>
            </li>
          ))}
        </ul>
      </section>

      {!!mem?.tasks.length && (
        <section className="insp__sec">
          <h3>Tasks</h3>
          <ul className="procs">
            {mem.tasks.filter((t, i, all) => all.findIndex((x) => x.id === t.id) === i).map((t) => {
              const rels = mem.tasks.filter((x) => x.id === t.id).map((x) => (x.rel === 'POSTED_BY' ? 'posted' : 'claimed'))
              return <li key={t.id}><span className="procs__name">{t.kind}</span><small>{rels.join(' and ')}, {t.status}{t.result ? `: ${t.result}` : ''}</small></li>
            })}
          </ul>
        </section>
      )}
    </aside>
  )
}

export function ProvenanceCard({ prov, world }: { prov: Provenance; world: World }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => { ref.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }) }, [prov])
  if (!prov.found) return <div className="prov prov--none" ref={ref}><p>{prov.narrative}</p></div>
  // render the path in reading order: actor -> event <- witness -> ... -> believer
  const steps: { id: string; label: string; name: string; edge?: string }[] = []
  const actorEdge = (prov.edges ?? []).find((e) => e.type !== 'TOLD' && e.type !== 'WITNESSED')
  const ev = (prov.nodes ?? []).find((n) => n.label === 'Event')
  // the event's own name already says who did it ("Ada stole Bram's silver hammer"), so the actor gets no chip of its own
  if (actorEdge && !ev) {
    const a = (prov.nodes ?? []).find((n) => n.id === actorEdge.from)!
    steps.push({ id: a.id, label: a.label, name: a.label === 'Player' ? world.state?.player.name ?? 'You' : a.name })
  }
  if (ev) steps.push({ id: ev.id, label: 'Event', name: prov.location ? `${ev.name} (${prov.location.name})` : ev.name })
  prov.chain.forEach((id, i) => {
    const n = (prov.nodes ?? []).find((x) => x.id === id)!
    steps.push({ id, label: n.label, name: n.label === 'Player' ? 'You' : n.name, edge: i === 0 ? (ev ? 'saw it' : undefined) : 'told' })
  })
  return (
    <div className="prov" ref={ref}>
      <h3>How {world.nameOf(prov.chain.at(-1)!)} knows
        {prov.belief && <span className={`prov__now ${prov.belief.stance === 'doubted' ? 'is-doubt' : ''}`}>now <b className="num">{pct(prov.belief.confidence)}</b> {prov.belief.stance === 'doubted' ? 'and doubtful' : 'sure'}</span>}
      </h3>
      <ol className="prov__path">
        {steps.map((s, i) => (
          <li key={s.id + i} className={`prov__step prov__step--${s.label.toLowerCase()}`}>
            {s.edge && <span className="prov__edge">{s.edge === 'saw it' ? 'saw it' : s.edge}</span>}
            <span className="prov__node">{s.name}</span>
          </li>
        ))}
      </ol>
      <p className="prov__text">{prov.narrative}</p>
      {!!prov.who_knows?.length && (
        <p className="prov__more">{prov.who_knows.filter((k) => k.stance === 'accepted').length} of 10 townsfolk believe it.
          {!!prov.forecast?.length && <> Likely next to hear: {prov.forecast.slice(0, 3).map((f) => f.name).join(', ')}.</>}</p>
      )}
      <button className="link" onClick={world.clearTrace}>Clear the thread</button>
    </div>
  )
}
