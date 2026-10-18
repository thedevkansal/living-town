import { useMemo, useState } from 'react'
import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, type SimulationNodeDatum } from 'd3-force'
import type { MemEdge, MemNode } from '../api'

interface N extends SimulationNodeDatum { id: string; d: MemNode; r: number }
interface L { source: N | string; target: N | string; d: MemEdge }

// Each memory type lives in its own zone of the panel, so episodic / semantic / procedural read at a glance.
// Zones are fractions of the viewBox; the zone captions sit in the corners and never move.
const ZONE: Record<string, { x: number; y: number; caption: string; cls: string; cx: number; cy: number; anchor: 'start' | 'end' | 'middle' }> = {
  Event: { x: 0.2, y: 0.3, caption: 'Saw · episodic', cls: 'episodic', cx: 0.03, cy: 0.06, anchor: 'start' },
  Procedure: { x: 0.22, y: 0.7, caption: 'Knows how · procedural', cls: 'procedural', cx: 0.03, cy: 0.975, anchor: 'start' },
  Fact: { x: 0.74, y: 0.6, caption: 'Believes · semantic', cls: 'semantic', cx: 0.97, cy: 0.975, anchor: 'end' },
  Task: { x: 0.72, y: 0.18, caption: 'Tasks', cls: 'coordination', cx: 0.97, cy: 0.06, anchor: 'end' },
}
const SELF = { x: 0.45, y: 0.5 }

export function memoryClass(n: MemNode) {
  if (n.label === 'Self') return 'self'
  if (n.label === 'Event') return 'episodic'
  if (n.label === 'Fact') return n.stance === 'doubted' || n.stance === 'faded' ? 'semantic doubted' : 'semantic'
  if (n.label === 'Procedure') return 'procedural'
  if (n.label === 'Task') return 'coordination'
  return 'entity'
}

export function MemoryGraph({ nodes, edges, focus, onPick, width = 360, height = 330 }: {
  nodes: MemNode[]; edges: MemEdge[]; focus: string | null; onPick: (n: MemNode) => void; width?: number; height?: number
}) {
  const [hover, setHover] = useState<string | null>(null)
  const laid = useMemo(() => {
    const ns: N[] = nodes.map((d) => ({ id: d.id, d, r: d.label === 'Self' ? 17 : d.label === 'Fact' ? 7 + 6 * (d.confidence ?? 0.5) : d.label === 'Event' ? 9 : d.label === 'Procedure' ? 8 : 6 }))
    const ids = new Set(ns.map((n) => n.id))
    const ls: L[] = edges.filter((e) => ids.has(e.from) && ids.has(e.to)).map((e) => ({ source: e.from, target: e.to, d: e }))
    const self = ns.find((n) => n.d.label === 'Self')
    if (self) { self.fx = SELF.x * width; self.fy = SELF.y * height }
    // start every node in its zone, so the simulation only has to tidy up
    ns.forEach((n, i) => {
      const z = ZONE[n.d.label]
      n.x = (z ? z.x : 0.5) * width + (((i * 37) % 31) - 15)
      n.y = (z ? z.y : 0.5) * height + (((i * 53) % 31) - 15)
    })
    const zx = (n: N) => (ZONE[n.d.label]?.x ?? SELF.x) * width
    const zy = (n: N) => (ZONE[n.d.label]?.y ?? SELF.y) * height
    const sim = forceSimulation(ns)
      .force('link', forceLink<N, L>(ls).id((n) => n.id).distance((l) => (l.d.type === 'ABOUT' ? 34 : l.d.type === 'TOLD_ME' ? 40 : 60)).strength((l) => ((l.source as N).d?.label === 'Self' || (l.target as N).d?.label === 'Self' ? 0.05 : 0.3)))
      .force('charge', forceManyBody().strength(-70))
      .force('collide', forceCollide<N>((n) => (memoryClass(n.d) === 'entity' || n.d.label === 'Self' ? n.r + 12 : Math.max(n.r + 16, shortLabel(n.d).length * 2.8))).iterations(3))
      .force('x', forceX<N>(zx).strength((n) => (ZONE[n.d.label] ? 0.45 : 0.03)))
      .force('y', forceY<N>(zy).strength((n) => (ZONE[n.d.label] ? 0.45 : 0.03)))
      .stop()
    const mx = 46, top = 30, bottom = 48 // keep labels and corner captions clear of the edges
    for (let i = 0; i < 300; i++) {
      sim.tick()
      for (const n of ns) {
        n.x = Math.max(mx, Math.min(width - mx, n.x ?? 0))
        n.y = Math.max(top, Math.min(height - bottom, n.y ?? 0))
      }
    }
    const placed = ns.map((n) => ({ ...n, px: n.x ?? 0, py: n.y ?? 0 }))
    const captions = Object.entries(ZONE).filter(([label]) => ns.some((n) => n.d.label === label))
      .map(([label, z]) => ({ label, text: z.caption, cls: z.cls, x: z.cx * width, y: z.cy * height, anchor: z.anchor }))
    return { ns: placed, ls, captions }
  }, [nodes, edges, width, height])

  const byId = Object.fromEntries(laid.ns.map((n) => [n.id, n]))
  const lit = new Set<string>()
  if (focus) {
    lit.add(focus)
    for (const e of edges) if (e.from === focus || e.to === focus) { lit.add(e.from); lit.add(e.to) }
  }
  const hoverNode = hover ? byId[hover] : null

  return (
    <div className="mgraph">
      <svg viewBox={`0 0 ${width} ${height}`} className="mgraph__svg" role="img" aria-label="Memory graph">
        {laid.ls.map((l, i) => {
          const a = byId[(l.source as N).id ?? (l.source as string)]; const b = byId[(l.target as N).id ?? (l.target as string)]
          if (!a || !b) return null
          const on = focus ? lit.has(a.id) && lit.has(b.id) : false
          return <line key={i} x1={a.px} y1={a.py} x2={b.px} y2={b.py} className={`mg-edge mg-edge--${l.d.type.toLowerCase()} ${on ? 'is-on' : ''} ${focus && !on ? 'is-dim' : ''}`} />
        })}
        {laid.captions.map((c) => (
          <text key={c.label} x={c.x} y={c.y} textAnchor={c.anchor} className={`mg-caption mg-caption--${c.cls}`}>{c.text}</text>
        ))}
        {laid.ns.map((n) => (
          <g key={n.id} transform={`translate(${n.px},${n.py})`} className={`mg-node mg-node--${memoryClass(n.d).replace(' ', ' mg-node--')} ${focus && !lit.has(n.id) ? 'is-dim' : ''} ${focus === n.id ? 'is-focus' : ''}`}
            onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(null)} onClick={() => onPick(n.d)}
            style={n.d.label === 'Self' ? { ['--c' as string]: n.d.color } : undefined}>
            {n.d.label === 'Procedure' ? <rect x={-n.r} y={-n.r} width={n.r * 2} height={n.r * 2} rx="2" transform="rotate(45)" />
              : n.d.label === 'Event' ? <path d={`M0 ${-n.r - 2} L${n.r + 1} ${n.r} L${-n.r - 1} ${n.r} Z`} />
                : n.d.label === 'Task' ? <rect x={-n.r} y={-n.r} width={n.r * 2} height={n.r * 2} rx="1" />
                  : <circle r={n.r} />}
            {n.d.label === 'Self'
              ? <text dy="4" textAnchor="middle">{n.d.name.split(' ').pop()!.slice(0, 1)}</text>
              : (memoryClass(n.d) !== 'entity' || lit.has(n.id) || hover === n.id) && <text className="mg-label" dy={n.r + 11} textAnchor="middle">{shortLabel(n.d)}</text>}
          </g>
        ))}
      </svg>
      {hoverNode && (
        <div className="mgraph__tip" style={{ left: `${(hoverNode.px / width) * 100}%`, top: `${(hoverNode.py / height) * 100}%` }}>
          <b>{tipTitle(hoverNode.d)}</b>{hoverNode.d.name}
          {hoverNode.d.confidence !== undefined && <span> {Math.round(hoverNode.d.confidence * 100)}% {hoverNode.d.stance}</span>}
          {hoverNode.d.proficiency !== undefined && <span> proficiency {Math.round(hoverNode.d.proficiency * 100)}%</span>}
        </div>
      )}
    </div>
  )
}

function tipTitle(n: MemNode) {
  switch (n.label) {
    case 'Event': return 'Saw: '
    case 'Fact': return n.stance === 'doubted' ? 'Doubts: ' : 'Believes: '
    case 'Procedure': return 'Knows how: '
    case 'Task': return 'Task: '
    case 'Self': return ''
    default: return `${n.label}: `
  }
}

function shortLabel(n: MemNode) {
  const t = n.label === 'Event' ? (n.kind ?? n.name) : n.label === 'Task' ? n.name.split(' ')[0] : n.name
  const words = t.replace(/\b(Captain|Sister) /g, '').split(' ')
  let out = ''
  for (const w of words) { if ((out + ' ' + w).trim().length > 20) break; out = (out + ' ' + w).trim() }
  // never end on a dangling little word ("Investigate a")
  out = out.replace(/ (a|an|the|of|to|at|for|and|on|in)$/i, '')
  return out || t.slice(0, 20)
}
