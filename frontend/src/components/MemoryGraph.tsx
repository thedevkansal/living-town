import { useMemo, useState } from 'react'
import { forceCollide, forceLink, forceManyBody, forceRadial, forceSimulation, forceX, forceY, type SimulationNodeDatum } from 'd3-force'
import type { MemEdge, MemNode } from '../api'

interface N extends SimulationNodeDatum { id: string; d: MemNode; r: number }
interface L { source: N | string; target: N | string; d: MemEdge }

const RING: Record<string, number> = { Self: 0, Event: 95, Fact: 120, Procedure: 80, Task: 110, NPC: 165, Player: 160, Item: 165, Location: 170 }

export function memoryClass(n: MemNode) {
  if (n.label === 'Self') return 'self'
  if (n.label === 'Event') return 'episodic'
  if (n.label === 'Fact') return n.stance === 'doubted' || n.stance === 'faded' ? 'semantic doubted' : 'semantic'
  if (n.label === 'Procedure') return 'procedural'
  if (n.label === 'Task') return 'coordination'
  return 'entity'
}

export function MemoryGraph({ nodes, edges, focus, onPick, width = 360, height = 300 }: {
  nodes: MemNode[]; edges: MemEdge[]; focus: string | null; onPick: (n: MemNode) => void; width?: number; height?: number
}) {
  const [hover, setHover] = useState<string | null>(null)
  const laid = useMemo(() => {
    const ns: N[] = nodes.map((d) => ({ id: d.id, d, r: d.label === 'Self' ? 17 : d.label === 'Fact' ? 7 + 6 * (d.confidence ?? 0.5) : d.label === 'Event' ? 9 : d.label === 'Procedure' ? 8 : 6 }))
    const ids = new Set(ns.map((n) => n.id))
    const ls: L[] = edges.filter((e) => ids.has(e.from) && ids.has(e.to)).map((e) => ({ source: e.from, target: e.to, d: e }))
    const self = ns.find((n) => n.d.label === 'Self')
    if (self) { self.fx = 0; self.fy = 0 }
    const sim = forceSimulation(ns)
      .force('link', forceLink<N, L>(ls).id((n) => n.id).distance((l) => (l.d.type === 'ABOUT' ? 38 : l.d.type === 'TOLD_ME' ? 45 : 70)).strength(0.5))
      .force('charge', forceManyBody().strength(-90))
      .force('radial', forceRadial<N>((n) => RING[n.d.label] ?? 150, 0, 0).strength(0.35))
      .force('collide', forceCollide<N>((n) => n.r + 4))
      .force('x', forceX(0).strength(0.02)).force('y', forceY(0).strength(0.03))
      .stop()
    for (let i = 0; i < 260; i++) sim.tick()
    // fit into the viewport
    const xs = ns.map((n) => n.x ?? 0); const ys = ns.map((n) => n.y ?? 0)
    const pad = 24
    const minX = Math.min(...xs) - pad, maxX = Math.max(...xs) + pad, minY = Math.min(...ys) - pad, maxY = Math.max(...ys) + pad
    const s = Math.min(width / (maxX - minX || 1), height / (maxY - minY || 1), 1.6)
    const tx = (x: number) => (x - (minX + maxX) / 2) * s + width / 2
    const ty = (y: number) => (y - (minY + maxY) / 2) * s + height / 2
    return { ns: ns.map((n) => ({ ...n, px: tx(n.x ?? 0), py: ty(n.y ?? 0) })), ls, scale: s }
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
        {laid.ns.map((n) => (
          <g key={n.id} transform={`translate(${n.px},${n.py})`} className={`mg-node mg-node--${memoryClass(n.d).replace(' ', ' mg-node--')} ${focus && !lit.has(n.id) ? 'is-dim' : ''} ${focus === n.id ? 'is-focus' : ''}`}
            onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(null)} onClick={() => onPick(n.d)}
            style={n.d.label === 'Self' ? { ['--c' as string]: n.d.color } : undefined}>
            {n.d.label === 'Procedure' ? <rect x={-n.r} y={-n.r} width={n.r * 2} height={n.r * 2} rx="2" transform="rotate(45)" />
              : n.d.label === 'Event' ? <path d={`M0 ${-n.r - 2} L${n.r + 1} ${n.r} L${-n.r - 1} ${n.r} Z`} />
                : n.d.label === 'Task' ? <rect x={-n.r} y={-n.r} width={n.r * 2} height={n.r * 2} rx="1" />
                  : <circle r={n.r} />}
            {n.d.label === 'Self' && <text dy="4" textAnchor="middle">{n.d.name.split(' ').pop()!.slice(0, 1)}</text>}
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
