import { useMemo } from 'react'
import type { Location, NPC, Provenance, WorldState } from '../api'
import type { Bubble, Pulse, Ripple } from '../useWorld'
import { Building } from './Buildings'

const W = 1000
const H = 640

// time-of-day light laid over the painted map
const LIGHT: Record<string, { tint: string; alpha: number; sky: string }> = {
  Dawn: { tint: '#f4a98a', alpha: 0.26, sky: 'dawn' },
  Morning: { tint: '#fff3c4', alpha: 0.1, sky: 'day' },
  Midday: { tint: '#ffffff', alpha: 0.0, sky: 'day' },
  Afternoon: { tint: '#ffd27a', alpha: 0.16, sky: 'day' },
  Evening: { tint: '#e0703f', alpha: 0.32, sky: 'dusk' },
  Night: { tint: '#1b2550', alpha: 0.58, sky: 'night' },
}

const TREES: [number, number, number][] = [
  [60, 90, 22], [95, 120, 16], [40, 170, 18], [620, 70, 18], [655, 95, 14], [940, 90, 20], [965, 140, 15],
  [960, 580, 22], [915, 610, 15], [70, 560, 20], [110, 600, 16], [30, 610, 12], [380, 250, 12], [585, 250, 11],
  [210, 300, 14], [800, 320, 13], [450, 600, 16], [490, 625, 12], [760, 600, 12], [30, 300, 16], [170, 240, 10],
]

export interface Spot { x: number; y: number }

/** Avatar slots: a gentle arc under each building, two rows when crowded. */
export function layout(state: WorldState): Record<string, Spot> {
  const spots: Record<string, Spot> = {}
  for (const loc of state.locations) {
    const here: string[] = state.npcs.filter((n) => n.location === loc.id).map((n) => n.id).sort()
    if (state.player.location === loc.id) here.unshift('player')
    const perRow = 6
    here.forEach((id, i) => {
      const row = Math.floor(i / perRow)
      const inRow = Math.min(perRow, here.length - row * perRow)
      const k = i % perRow - (inRow - 1) / 2
      spots[id] = { x: loc.x + k * 34, y: loc.y + 52 + row * 36 + Math.abs(k) * -3 }
    })
  }
  return spots
}

function arc(a: Spot, b: Spot, lift = 0.35) {
  const mx = (a.x + b.x) / 2
  const my = (a.y + b.y) / 2
  const dx = b.x - a.x
  const dy = b.y - a.y
  const len = Math.hypot(dx, dy) || 1
  const bend = Math.max(34, len * lift)
  const cx = mx + (dy / len) * bend * 0.4
  const cy = my - bend
  return `M${a.x},${a.y - 6} Q${cx},${cy} ${b.x},${b.y - 6}`
}

const PULSE_COLOR: Record<Pulse['tone'], string> = {
  gossip: 'var(--ink)', report: 'var(--plum)', testimony: 'var(--ember)', findings: 'var(--plum)', teach: 'var(--moss)', claim: 'var(--thread)',
}

interface Props {
  state: WorldState
  bubbles: Bubble[]
  pulses: Pulse[]
  ripples: Ripple[]
  selected: string | null
  onSelect: (npc: string) => void
  onWalk: (loc: string) => void
  highlight: Provenance | null
  spread: Pick<Provenance, 'who_knows' | 'forecast'> | null
  communities?: Record<string, number> | null
  compact?: boolean
}

export function TownMap({ state, bubbles, pulses, ripples, selected, onSelect, onWalk, highlight, spread, communities, compact }: Props) {
  const spots = useMemo(() => layout(state), [state])
  const light = LIGHT[state.clock.slot] ?? LIGHT.Midday
  const square = state.locations.find((l) => l.id === 'square')
  const npcById = useMemo(() => Object.fromEntries(state.npcs.map((n) => [n.id, n])), [state.npcs])
  const knows = useMemo(() => Object.fromEntries((spread?.who_knows ?? []).map((k) => [k.npc, k])), [spread])
  const next = useMemo(() => new Set((spread?.forecast ?? []).slice(0, 3).map((f) => f.npc)), [spread])
  const chain = highlight?.found ? highlight.chain : []
  const threadIds = useMemo(() => {
    if (!highlight?.found) return []
    const ids = [...chain]
    const actor = highlight.nodes.find((n) => n.label === 'Player' || (n.label === 'NPC' && !chain.includes(n.id)))
    if (actor && highlight.edges.some((e) => e.from === actor.id && e.type !== 'TOLD' && e.type !== 'WITNESSED')) ids.unshift(actor.id)
    return ids.filter((id) => spots[id])
  }, [highlight, chain, spots])
  const threadPath = threadIds.map((id) => spots[id]).reduce((acc, p, i, arr) => (i === 0 ? `M${p.x},${p.y - 6}` : acc + ' ' + arc(arr[i - 1], p, 0.28).replace(/^M[^Q]+/, '')), '')

  const COMM = ['#e9c46a', '#8ecae6', '#cdb4db', '#b7e4c7']

  return (
    <div className={`map ${compact ? 'map--compact' : ''}`} data-sky={light.sky}>
      <div className="map__frame">
        <svg viewBox={`0 0 ${W} ${H}`} className="map__svg" role="img" aria-label={`Map of ${state.clock.name}`}>
          <defs>
            <filter id="paper" x="0" y="0" width="100%" height="100%">
              <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" seed="4" result="n" />
              <feColorMatrix type="saturate" values="0" />
              <feComponentTransfer><feFuncA type="table" tableValues="0 0.07" /></feComponentTransfer>
              <feBlend in="SourceGraphic" mode="multiply" />
            </filter>
            <radialGradient id="vignette" cx="50%" cy="50%" r="70%">
              <stop offset="60%" stopColor="#000" stopOpacity="0" />
              <stop offset="100%" stopColor="#1a120d" stopOpacity="0.35" />
            </radialGradient>
            <radialGradient id="lamp" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="#ffd27a" stopOpacity="0.55" />
              <stop offset="100%" stopColor="#ffd27a" stopOpacity="0" />
            </radialGradient>
            <pattern id="cobble" width="14" height="14" patternUnits="userSpaceOnUse">
              <rect width="14" height="14" fill="#d8c59c" />
              <path d="M0 7h14M7 0v7M3 7v7M11 7v7" stroke="#bea877" strokeWidth="1" opacity=".55" />
            </pattern>
          </defs>

          {/* ground */}
          <rect width={W} height={H} fill="#a8c08a" />
          <ellipse cx="520" cy="330" rx="420" ry="250" fill="#b4ca93" opacity=".6" />
          {/* stream with a footbridge */}
          <path d="M1000 250 C 930 280, 960 330, 900 360 S 760 470, 800 520 S 870 600, 840 640" fill="none" stroke="#7fb2c9" strokeWidth="26" strokeLinecap="round" opacity=".9" />
          <path d="M1000 250 C 930 280, 960 330, 900 360 S 760 470, 800 520 S 870 600, 840 640" fill="none" stroke="#bfe0ea" strokeWidth="6" strokeDasharray="18 30" opacity=".7" />

          {/* roads from the square */}
          {square && state.locations.filter((l) => l.id !== 'square').map((l) => (
            <g key={l.id}>
              <path d={road(square, l)} fill="none" stroke="#b89d6c" strokeWidth="20" strokeLinecap="round" opacity=".55" />
              <path d={road(square, l)} fill="none" stroke="url(#cobble)" strokeWidth="15" strokeLinecap="round" />
            </g>
          ))}
          {square && <circle cx={square.x} cy={square.y} r="58" fill="url(#cobble)" stroke="#b89d6c" strokeWidth="5" />}
          {square && <Well x={square.x} y={square.y} />}

          {TREES.map(([x, y, r], i) => <Tree key={i} x={x} y={y} r={r} />)}

          {/* buildings */}
          {state.locations.map((l) => (
            <g key={l.id} className={`bld ${state.player.location === l.id ? 'bld--here' : ''}`} onClick={() => onWalk(l.id)}
              role="button" tabIndex={0} aria-label={`Walk to ${l.name}`}
              onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && onWalk(l.id)}>
              {l.id !== 'square' && <Building kind={l.kind} x={l.x} y={l.y} lit={light.sky === 'night' || light.sky === 'dusk'} />}
              <text x={l.x} y={l.id === 'square' ? l.y - 66 : l.y + 22} className="bld__label" textAnchor="middle">{l.name}</text>
            </g>
          ))}

          {/* light of the hour */}
          <rect width={W} height={H} fill={light.tint} opacity={light.alpha} className="map__light" pointerEvents="none" style={{ mixBlendMode: 'multiply' }} />
          {light.sky === 'night' && state.locations.filter((l) => l.id !== 'square').map((l) => (
            <circle key={l.id} cx={l.x} cy={l.y - 6} r="70" fill="url(#lamp)" className="lamp" pointerEvents="none" />
          ))}
          <rect width={W} height={H} fill="url(#vignette)" pointerEvents="none" />
          <rect width={W} height={H} filter="url(#paper)" fill="transparent" pointerEvents="none" />

          {/* social cliques (label propagation) as soft ground halos */}
          {communities && state.npcs.map((n) => spots[n.id] && (
            <circle key={'c' + n.id} cx={spots[n.id].x} cy={spots[n.id].y} r="21" fill={COMM[(communities[n.id] ?? 0) % COMM.length]} opacity=".55" />
          ))}

          {/* story tracker: TOLD edges of the focused fact, drawn between current positions */}
          {spread?.who_knows?.filter((k) => k.via && spots[k.npc] && spots[k.via]).map((k) => (
            <path key={'t' + k.npc} d={arc(spots[k.via!], spots[k.npc])} className={`told told--${k.stance}`} />
          ))}

          {/* provenance: the red thread */}
          {threadPath && <path key={threadIds.join('-')} d={threadPath} className="thread" />}

          {/* live gossip pulses */}
          {pulses.map((p) => {
            const a = spots[p.from]; const b = spots[p.to]
            if (!a || !b) return null
            const d = arc(a, b)
            return (
              <g key={p.id} className={`pulse ${p.doubted ? 'pulse--doubt' : ''}`} style={{ color: PULSE_COLOR[p.tone] }}>
                <path d={d} className="pulse__trail" />
                <circle r="7" className="pulse__spark"><animateMotion dur="1.1s" path={d} fill="freeze" calcMode="spline" keySplines="0.4 0 0.2 1" keyTimes="0;1" /></circle>
              </g>
            )
          })}
        </svg>

        {/* avatars + bubbles (HTML so they glide with CSS transitions) */}
        <div className="map__layer">
          <div className="avatar avatar--player" style={pos(spots.player)} title="You">
            <span>{state.player.name.slice(0, 1).toUpperCase()}</span>
            {ripples.filter((r) => r.npc === 'player').map((r) => <i key={r.id} className={`ripple ripple--${r.tone}`} />)}
          </div>
          {state.npcs.map((n) => (
            <Avatar key={n.id} npc={n} spot={spots[n.id]} selected={selected === n.id} onSelect={onSelect}
              knows={knows[n.id]} next={next.has(n.id)} inThread={chain.includes(n.id)}
              ripples={ripples.filter((r) => r.npc === n.id)} />
          ))}
          {bubbles.map((b) => {
            const s = b.npc === 'player' ? spots.player : spots[b.npc]
            if (!s) return null
            const n = npcById[b.npc]
            const near = bubbles.filter((o) => { const t = o.npc === 'player' ? spots.player : spots[o.npc]; return t && Math.abs(t.y - s.y) < 60 && Math.abs(t.x - s.x) < 240 })
            const stack = near.findIndex((o) => o.id === b.id)
            return (
              <div key={b.id} className={`bubble ${b.long ? 'bubble--long' : ''} ${s.x > 760 ? 'bubble--left' : s.x < 200 ? 'bubble--right' : ''}`}
                style={{ ...pos(s), ['--stack' as string]: stack }}>
                <b>{n?.name ?? 'You'}</b> {b.text}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

function pos(s?: Spot): React.CSSProperties {
  if (!s) return { display: 'none' }
  return { left: `${(s.x / W) * 100}%`, top: `${(s.y / H) * 100}%` }
}

function road(a: Location, b: Location) {
  const mx = (a.x + b.x) / 2 + (b.y - a.y) * 0.12
  const my = (a.y + b.y) / 2 - (b.x - a.x) * 0.08
  return `M${a.x},${a.y} Q${mx},${my} ${b.x},${b.y + 8}`
}

function Avatar({ npc, spot, selected, onSelect, knows, next, inThread, ripples }: {
  npc: NPC; spot?: Spot; selected: boolean; onSelect: (id: string) => void
  knows?: { confidence: number; stance: string; hop: number; via: string | null }
  next: boolean; inThread: boolean; ripples: Ripple[]
}) {
  const cls = ['avatar']
  if (selected) cls.push('avatar--selected')
  if (knows) cls.push(knows.hop === 0 ? 'avatar--witness' : knows.stance === 'accepted' ? 'avatar--knows' : 'avatar--doubts')
  if (next) cls.push('avatar--next')
  if (inThread) cls.push('avatar--thread')
  const op = npc.opinion
  return (
    <button className={cls.join(' ')} style={{ ...pos(spot), ['--c' as string]: npc.color, ['--k' as string]: knows?.confidence ?? 0 }}
      onClick={() => onSelect(npc.id)} aria-label={`${npc.name}, ${npc.role}. Open memory`} aria-pressed={selected}>
      <span>{initials(npc.name)}</span>
      {op !== 0 && <em className={`mood ${op < -0.1 ? 'mood--bad' : op > 0.1 ? 'mood--good' : ''}`} aria-hidden />}
      <small className="avatar__name">{npc.name.replace('Captain ', '').replace('Sister ', '')}</small>
      {ripples.map((r) => <i key={r.id} className={`ripple ripple--${r.tone}`} />)}
    </button>
  )
}

function initials(name: string) {
  const parts = name.split(' ')
  return parts[parts.length - 1].slice(0, 1)
}

function Tree({ x, y, r }: { x: number; y: number; r: number }) {
  return (
    <g>
      <ellipse cx={x + r * 0.3} cy={y + r * 0.8} rx={r * 0.9} ry={r * 0.35} fill="#5d7a4a" opacity=".35" />
      <circle cx={x} cy={y} r={r} fill="#6f9a57" />
      <circle cx={x - r * 0.35} cy={y - r * 0.3} r={r * 0.6} fill="#86b067" />
    </g>
  )
}

function Well({ x, y }: { x: number; y: number }) {
  return (
    <g>
      <circle cx={x} cy={y} r="16" fill="#8c8a83" stroke="#5f5c56" strokeWidth="3" />
      <circle cx={x} cy={y} r="9" fill="#3d5a6b" />
      <path d={`M${x - 18} ${y - 4}h36`} stroke="#6b4f36" strokeWidth="4" strokeLinecap="round" />
    </g>
  )
}
