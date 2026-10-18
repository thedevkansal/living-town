import { useEffect, useState } from 'react'
import { api, type Analytics, type Bench } from '../api'
import { type LogLine, type World } from '../useWorld'

type Tab = 'talk' | 'stories' | 'board' | 'insights'

export function SidePanel({ world, pid, onSelect, showCliques, setShowCliques, analytics, setAnalytics }: {
  world: World; pid: string; onSelect: (npc: string) => void
  showCliques: boolean; setShowCliques: (v: boolean) => void
  analytics: Analytics | null; setAnalytics: (a: Analytics | null) => void
}) {
  const [tab, setTab] = useState<Tab>('talk')
  const s = world.state!
  const tabs: [Tab, string][] = [['talk', 'Town talk'], ['stories', 'Stories'], ['board', 'Task board'], ['insights', 'Graph insights']]
  return (
    <section className="side">
      <nav className="tabs" role="tablist">
        {tabs.map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} className={`tab ${tab === id ? 'is-on' : ''}`} onClick={() => setTab(id)}>
            {label}{id === 'board' && s.tasks.some((t) => t.status !== 'done') ? <i className="dot" /> : null}
          </button>
        ))}
      </nav>
      <div className="side__body">
        {tab === 'talk' && <TalkLog world={world} onSelect={onSelect} />}
        {tab === 'stories' && <Stories world={world} />}
        {tab === 'board' && <Board world={world} />}
        {tab === 'insights' && <Insights world={world} pid={pid} showCliques={showCliques} setShowCliques={setShowCliques} analytics={analytics} setAnalytics={setAnalytics} />}
      </div>
    </section>
  )
}

/** Lines that concern the player: their deeds, stories about them, and the agents acting on those stories. */
function aboutPlayer(l: LogLine, name: string) {
  if (['crime', 'claim', 'conflict', 'task', 'time', 'testimony', 'findings', 'report'].includes(l.tone)) return true
  const t = l.text.toLowerCase()
  return (!!name && t.includes(name.toLowerCase())) || /\byou\b/.test(t)
}

export function TalkLog({ world, onSelect, limit }: { world: World; onSelect?: (npc: string) => void; limit?: number }) {
  const name = world.state?.player.name ?? ''
  const all = limit ? world.log.slice(0, limit) : world.log
  const lines = world.aboutYou ? all.filter((l) => aboutPlayer(l, name)) : all
  if (!all.length) {
    return <p className="empty">Nothing has happened yet. Walk to the Forge, steal the silver hammer, then sleep till dawn and watch the story travel.</p>
  }
  return (
    <>
    {!limit && (
      <label className="switch log__filter">
        <input type="checkbox" checked={world.aboutYou} onChange={(e) => world.setAboutYou(e.target.checked)} /> Only stories about you
      </label>
    )}
    <ol className="log">
      {lines.map((l) => (
        <li key={l.id} className={`log__line log--${l.tone} ${aboutPlayer(l, name) ? '' : 'is-aside'}`}>
          <time>{l.slot}</time>
          <p>{l.text}</p>
          <div className="log__acts">
            {l.fact && <button className="link" onClick={() => world.setFocusFact(world.focusFact === l.fact ? null : l.fact!)}>{world.focusFact === l.fact ? 'Stop tracking' : 'Track this story'}</button>}
            {l.npc && onSelect && <button className="link" onClick={() => onSelect(l.npc!)}>Open {world.nameOf(l.npc)}'s memory</button>}
          </div>
        </li>
      ))}
    </ol>
    </>
  )
}

function Stories({ world }: { world: World }) {
  if (!world.stories.length) return <p className="empty">Stories appear here once something happens. Track one to see who believes it on the map.</p>
  return (
    <ul className="stories">
      {world.stories.map((st) => (
        <li key={st.fact} className={world.focusFact === st.fact ? 'is-on' : ''}>
          <button onClick={() => world.setFocusFact(world.focusFact === st.fact ? null : st.fact)}>
            <span className={`story__kind story__kind--${st.kind}`}>{st.kind === 'claim' ? 'Your claim' : st.kind === 'told' ? 'Hearsay' : 'Seen'}</span>
            {st.claim}
          </button>
          {world.focusFact === st.fact && world.spread && (
            <div className="story__spread">
              <p>{world.spread.who_knows?.filter((k) => k.stance === 'accepted').length ?? 0} believe it, {world.spread.who_knows?.filter((k) => k.stance === 'doubted').length ?? 0} doubt it.</p>
              <ul>
                {world.spread.who_knows?.map((k) => (
                  <li key={k.npc}><b>{k.name}</b> {k.hop === 0 ? 'saw it' : `heard it from ${world.nameOf(k.via ?? '')}`} <span className="muted">{Math.round(k.confidence * 100)}%</span></li>
                ))}
              </ul>
              {!!world.spread.forecast?.length && <p className="muted">Next likely to hear (shortest social paths): {world.spread.forecast.slice(0, 3).map((f) => f.name).join(', ')}</p>}
            </div>
          )}
        </li>
      ))}
    </ul>
  )
}

function Board({ world }: { world: World }) {
  const tasks = world.state!.tasks
  if (!tasks.length) return <p className="empty">The shared task board is empty. When the guard hears of a crime, an investigation lands here and agents race to claim it.</p>
  return (
    <ul className="board">
      {tasks.map((t) => (
        <li key={t.id} className={`task task--${t.status}`}>
          <div className="task__top"><b>{t.kind}</b><span className={`pill pill--${t.status}`}>{t.status}</span></div>
          <p>{t.note ?? t.claim}</p>
          <small>
            Posted by {world.nameOf(t.posted_by)}
            {t.claimed_by ? `, claimed by ${world.nameOf(t.claimed_by)}` : ''}
            {t.handed_to ? `, handed to ${world.nameOf(t.handed_to)}` : ''}
          </small>
          {t.result && <small className="task__result">{t.result}</small>}
        </li>
      ))}
    </ul>
  )
}

function Insights({ world, pid, showCliques, setShowCliques, analytics, setAnalytics }: {
  world: World; pid: string; showCliques: boolean; setShowCliques: (v: boolean) => void
  analytics: Analytics | null; setAnalytics: (a: Analytics | null) => void
}) {
  const [bench, setBench] = useState<Bench | null>(null)
  const [running, setRunning] = useState(false)
  const tick = world.state?.clock.tick
  useEffect(() => { api.analytics(pid).then(setAnalytics).catch(() => setAnalytics(null)) }, [pid, tick, setAnalytics])
  const name = world.nameOf
  const top = (rows: { npc: string; score: number }[] | undefined, n = 3) => rows?.slice(0, n) ?? []
  const groups = analytics ? Object.entries(analytics.communities).reduce<Record<number, string[]>>((acc, [npc, c]) => { (acc[c] ??= []).push(npc); return acc }, {}) : {}
  return (
    <div className="insights">
      <div className="ins">
        <h4>Cliques <small>label propagation</small></h4>
        {Object.values(groups).map((g, i) => <p key={i} className={`clique clique--${i}`}>{g.map(name).join(', ')}</p>)}
        <label className="switch"><input type="checkbox" checked={showCliques} onChange={(e) => setShowCliques(e.target.checked)} /> Show on the map</label>
      </div>
      <div className="ins">
        <h4>Gossip brokers <small>betweenness</small></h4>
        <Bars rows={top(analytics?.gossip_hubs)} name={name} />
      </div>
      <div className="ins">
        <h4>Best informed <small>PageRank over who-told-whom</small></h4>
        <Bars rows={top(analytics?.best_informed)} name={name} />
      </div>
      <div className="ins">
        <h4>Fastest to reach everyone <small>harmonic centrality</small></h4>
        <Bars rows={top(analytics?.broadcast_reach)} name={name} />
      </div>
      <div className="ins">
        <h4>Concurrency check</h4>
        <p className="muted">Creates four isolated worlds and ticks them at once: 40 agents reading and writing their graphs in parallel.</p>
        <button className="btn" disabled={running} onClick={async () => {
          setRunning(true)
          try { setBench(await api.bench(4, 6)) } catch (e) { world.notify((e as Error).message, 'err') } finally { setRunning(false) }
        }}>
          {running ? 'Running 40 agents' : 'Run the stress test'}
        </button>
        {bench && (
          <dl className="bench">
            <div><dt>Writes per second</dt><dd>{bench.writes_per_sec.toLocaleString()}</dd></div>
            <div><dt>Queries per second</dt><dd>{bench.queries_per_sec.toLocaleString()}</dd></div>
            <div><dt>Median tick</dt><dd>{bench.p50_tick_ms} ms</dd></div>
            <div><dt>Agents</dt><dd>{bench.agents}</dd></div>
          </dl>
        )}
      </div>
    </div>
  )
}

function Bars({ rows, name }: { rows: { npc: string; score: number }[]; name: (id: string) => string }) {
  const max = Math.max(...rows.map((r) => r.score), 0.0001)
  return (
    <ul className="bars">
      {rows.map((r) => <li key={r.npc}><span>{name(r.npc)}</span><i style={{ width: `${(r.score / max) * 100}%` }} /></li>)}
    </ul>
  )
}
