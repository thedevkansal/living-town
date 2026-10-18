import { useCallback, useEffect, useState } from 'react'
import { api, type Analytics, type Metrics } from './api'
import { useWorld, type World } from './useWorld'
import { TownMap } from './components/TownMap'
import { ActionBar } from './components/ActionBar'
import { Inspector } from './components/Inspector'
import { SidePanel, TalkLog } from './components/SidePanel'

const slug = (name: string) => name.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 32) || 'wanderer'
const store = {
  get: (k: string) => { try { return localStorage.getItem(k) } catch { return null } },
  set: (k: string, v: string) => { try { localStorage.setItem(k, v) } catch { /* private mode */ } },
}

interface Joined { pid: string; name: string; returning: boolean; session: number }

export default function App() {
  const [health, setHealth] = useState<{ ok: boolean; mode: string } | null>(null)
  const [primary, setPrimary] = useState<Joined | null>(null)
  const [second, setSecond] = useState<Joined | null>(null)
  const [split, setSplit] = useState(false)
  const [greet, setGreet] = useState<Record<string, { npc: string; text: string }[]>>({})

  useEffect(() => { api.health().then(setHealth).catch(() => setHealth({ ok: false, mode: 'offline' })) }, [])

  const join = useCallback(async (name: string, reset = false): Promise<Joined> => {
    const pid = slug(name)
    const r = await api.join(pid, name.trim() || 'Wanderer', reset)
    setGreet((g) => ({ ...g, [pid]: r.greetings }))
    return { pid, name: r.state.player.name, returning: r.returning, session: r.session }
  }, [])

  const enterSplit = async () => {
    if (!second) {
      const other = primary?.pid === 'bo' ? 'Cy' : 'Bo'
      setSecond(await join(store.get('lt.player2') ?? other))
    }
    setSplit(true)
  }

  if (!primary) return <JoinScreen health={health} onJoin={async (n, reset) => { const j = await join(n, reset); store.set('lt.player', j.name); setPrimary(j) }} />

  return (
    <div className={`app ${split ? 'app--split' : ''}`}>
      <TopBar health={health} split={split} onSplit={() => (split ? setSplit(false) : enterSplit())} onLeave={() => { setPrimary(null); setSecond(null); setSplit(false) }} />
      <main className={split ? 'split' : 'single'}>
        <WorldPane key={primary.pid} joined={primary} greetings={greet[primary.pid]} compact={split} />
        {split && second && <WorldPane key={second.pid} joined={second} greetings={greet[second.pid]} compact />}
      </main>
    </div>
  )
}

function JoinScreen({ health, onJoin }: { health: { ok: boolean; mode: string } | null; onJoin: (name: string, reset: boolean) => Promise<void> }) {
  const remembered = store.get('lt.player')
  const [name, setName] = useState(remembered ?? '')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const go = async (reset: boolean) => {
    setBusy(true); setErr(null)
    try { await onJoin(name || 'Wanderer', reset) } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  return (
    <div className="join">
      <div className="join__card">
        <h1>Thornbury</h1>
        <p className="join__lede">Ten townsfolk. Every one of them remembers what they saw, what they were told, and who told them. Their memories live in a FalkorDB graph, and stories travel along it.</p>
        <form onSubmit={(e) => { e.preventDefault(); go(false) }}>
          <label htmlFor="name">Your name</label>
          <input id="name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Ada" autoFocus maxLength={24} />
          <button className="btn btn--ember btn--big" disabled={busy || health?.ok === false}>{busy ? 'Opening the gates' : remembered && slug(remembered) === slug(name) ? `Return as ${name}` : 'Enter the town'}</button>
        </form>
        <p className="join__hint">Use the same name next time and the town will remember you. Each name gets its own isolated world.</p>
        {remembered && slug(remembered) === slug(name) && <button className="link" onClick={() => go(true)} disabled={busy}>Start {name}'s world over</button>}
        {err && <p className="err">{err}</p>}
        {health?.ok === false && <p className="err">The town's memory store is not reachable right now. Try again in a moment (locally: start FalkorDB and <code>uv run uvicorn app.main:app --port 8002</code>).</p>}
      </div>
    </div>
  )
}

function TopBar({ health, split, onSplit, onLeave }: { health: { ok: boolean; mode: string } | null; split: boolean; onSplit: () => void; onLeave: () => void }) {
  return (
    <header className="top">
      <div className="top__brand">
        <h1>Thornbury</h1>
        <p>a town whose memory is a graph</p>
      </div>
      <div className="top__right">
        <span className={`badge badge--${health?.mode ?? 'scripted'}`} title={health?.mode === 'llm' ? 'NPC dialogue uses Claude with a fixed tool catalogue' : 'No ANTHROPIC_API_KEY: deterministic agents and templated replies from graph memory'}>
          {health?.mode === 'llm' ? 'Claude dialogue' : 'Scripted agents'}
        </span>
        <button className={`btn btn--toggle ${split ? 'is-on' : ''}`} onClick={onSplit} aria-pressed={split}>{split ? 'One world' : 'Two players side by side'}</button>
        <button className="btn btn--quiet" onClick={onLeave}>Switch player</button>
      </div>
    </header>
  )
}

function WorldPane({ joined, greetings, compact }: { joined: Joined; greetings?: { npc: string; text: string }[]; compact?: boolean }) {
  const world = useWorld(joined.pid)
  const [selected, setSelected] = useState<string | null>(null)
  const [showCliques, setShowCliques] = useState(false)
  const [analytics, setAnalytics] = useState<Analytics | null>(null)
  const [banner, setBanner] = useState(true)
  const act = world.act
  const walk = useCallback((loc: string) => { act({ action: 'move', location: loc }) }, [act])

  // greet the player from persistent memory once the town is drawn
  useEffect(() => {
    if (!world.state || !greetings?.length) return
    const ts = greetings.map((g, i) => window.setTimeout(() => world.say(g.npc, g.text, true), 600 + i * 1400))
    const t = window.setTimeout(() => setBanner(false), 9000)
    return () => { ts.forEach(clearTimeout); clearTimeout(t) }
  }, [!!world.state, greetings])

  if (!world.state) return <div className="pane pane--loading">Unrolling the map</div>

  return (
    <section className={`pane ${compact ? 'pane--compact' : ''}`} aria-label={`${joined.name}'s world`}>
      <div className="pane__stage">
        <div className="pane__hud">
          <div className="hud__who">
            <b>{joined.name}'s Thornbury</b>
            <span>graph <code>town:{joined.pid}</code>, session {joined.session}</span>
          </div>
          <MetricStrip m={world.metrics} />
        </div>
        {banner && joined.returning && (
          <div className="banner" role="status">Welcome back. This is visit {joined.session}, and the town remembers what you did.</div>
        )}
        <TownMap state={world.state} bubbles={world.bubbles} pulses={world.pulses} ripples={world.ripples} selected={selected}
          onSelect={(id) => setSelected(selected === id ? null : id)} onWalk={walk}
          highlight={world.highlight} spread={world.spread} communities={showCliques ? analytics?.communities : null} compact={compact} />
        {world.toast && <Toast key={world.toast.id} text={world.toast.text} tone={world.toast.tone} />}
        {compact && selected && (
          <div className="overlay-insp"><Inspector world={world} pid={joined.pid} npc={selected} onClose={() => setSelected(null)} /></div>
        )}
      </div>
      <ActionBar world={world} onTalk={setSelected} compact={compact} />
      {compact ? (
        <div className="pane__log"><TalkLog world={world} limit={4} /></div>
      ) : (
        <div className="pane__side">
          {selected
            ? <Inspector world={world} pid={joined.pid} npc={selected} onClose={() => { setSelected(null) }} />
            : <SidePanel world={world} pid={joined.pid} onSelect={setSelected} showCliques={showCliques} setShowCliques={setShowCliques} analytics={analytics} setAnalytics={setAnalytics} />}
        </div>
      )}
    </section>
  )
}

function Toast({ text, tone }: { text: string; tone: 'ok' | 'err' }) {
  const [shown, setShown] = useState(true)
  useEffect(() => { const t = window.setTimeout(() => setShown(false), 3600); return () => clearTimeout(t) }, [])
  if (!shown) return null
  return <div className={`toast toast--${tone}`} role="status">{text}</div>
}

function MetricStrip({ m }: { m: Metrics | null }) {
  const hist = m?.history ?? []
  const max = Math.max(...hist.map((h) => h.ms), 1)
  return (
    <dl className="metrics" aria-label="Live engine metrics">
      <div><dt>Agents</dt><dd>{m?.agents ?? 10}</dd></div>
      <div><dt>Tick</dt><dd>{m?.tick_ms ? `${Math.round(m.tick_ms)} ms` : 'idle'}</dd></div>
      <div><dt>Writes/s</dt><dd>{m?.writes_per_sec ? Math.round(m.writes_per_sec).toLocaleString() : 0}</dd></div>
      <div><dt>Queries</dt><dd>{m?.queries ?? 0}</dd></div>
      {hist.length > 1 && (
        <svg className="spark" viewBox={`0 0 ${hist.length * 6} 20`} aria-hidden>
          <polyline points={hist.map((h, i) => `${i * 6 + 3},${19 - (h.ms / max) * 17}`).join(' ')} />
        </svg>
      )}
    </dl>
  )
}

export type { World }
