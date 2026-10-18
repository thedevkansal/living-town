// Typed client for the Living Town backend (FastAPI on :8002, proxied at /api and /ws).

export interface Location { id: string; name: string; kind: string; x: number; y: number }
export interface NPC {
  id: string; name: string; role: string; color: string; personality: string
  chattiness: number; location: string; opinion: number
}
export interface Tie { a: string; b: string; affinity: number; bond: string; trust: number }
export interface Item { id: string; name: string; owner?: string; owner_name?: string }
export interface Task {
  id: string; kind: string; status: string; posted_by: string; claimed_by: string | null
  target: string | null; note: string | null; result: string | null; posted_tick: number
  done_tick: number | null; claim: string | null; handed_to: string | null
}
export interface TownEvent {
  id: string; kind: string; summary: string; tick: number; day: number; slot: string
  location: string; witnesses: string[]
}
export interface Clock {
  tick: number; day: number; slot: string; slot_index: number; name: string; player_name: string; sessions: number
}
export interface Metrics {
  tick_ms: number; writes: number; queries: number; writes_per_sec: number; agents: number
  total_writes?: number; total_queries?: number; history?: { ms: number; writes: number }[]
}
export interface WorldState {
  player_id: string; clock: Clock; locations: Location[]; npcs: NPC[]; ties: Tie[]
  player: { name: string; location: string; items: Item[] }; items_here: Item[]
  tasks: Task[]; events: TownEvent[]; metrics: Metrics
}

// events streamed from the simulation
export type SimEvent =
  | { type: 'move'; npc: string; from: string; to: string; reason: string | null }
  | { type: 'player_move'; from: string; to: string }
  | { type: 'tell'; from: string; to: string; fact: string; claim: string; confidence: number; stance: string; hop: number; first_time: boolean; channel?: string }
  | { type: 'player_tell'; npc: string; fact: string; claim: string; confidence: number; stance: string }
  | { type: 'say'; npc: string; text: string; long?: boolean }
  | { type: 'deed'; kind: string; event: string; fact: string; claim: string; location: string; witnesses: string[] }
  | { type: 'conflict'; npc: string; fact: string; rival: string; winner: string; claim: string; rival_claim: string; c_fact: number; c_rival: number }
  | { type: 'task_posted'; task: string; kind: string; by: string; claim: string }
  | { type: 'task_claimed'; task: string; kind: string; by: string }
  | { type: 'claim_lost'; task: string; by: string }
  | { type: 'task_progress'; task: string; by: string; target: string; note: string }
  | { type: 'task_done'; task: string; by: string; result: string; handoff?: string }
  | { type: 'handoff'; from: string; to: string; task: string; kind: string }
  | { type: 'confront'; npc: string; fact: string; text: string; location: string }
  | { type: 'teach'; from: string; to: string; procedure: string; name: string }
  | { type: 'dawn'; day: number }
  | { type: 'error'; phase: string; error: string }

export interface TickResult { tick: number; day: number; slot: string; slot_index: number; events: SimEvent[]; metrics: Metrics }

export interface Belief {
  fact: string; claim: string; kind: string; confidence: number; stance: string; hop: number
  via: string | null; via_name?: string; source: string; truth: boolean | null; importance: number
}
export interface MemNode {
  id: string; label: string; name: string; memory?: string; color?: string; confidence?: number; stance?: string
  kind?: string; proficiency?: number; steps?: string[]; vividness?: number; place?: string; tick?: number
}
export interface MemEdge { from: string; to: string; type: string; confidence?: number; stance?: string }
export interface Memory {
  npc: { id: string; name: string; role: string; personality: string; color: string }
  nodes: MemNode[]; edges: MemEdge[]; beliefs: Belief[]
  procedures: { id: string; name: string; steps: string[]; proficiency: number; learned_from: string; learned_from_name?: string }[]
  relationships: { id: string; name: string; role: string; trust: number; affinity: number; bond: string }[]
  tasks: { id: string; kind: string; status: string; rel: string; result: string | null }[]
  opinion: { score: number; facts: number; mood: string }
}
export interface Provenance {
  found: boolean; narrative: string
  nodes: { id: string; name: string; label: string; kind?: string; tick?: number }[]
  edges: { from: string; to: string; type: string; tick?: number; confidence?: number; channel?: string }[]
  chain: string[]; fact?: { id: string; claim: string; kind: string; truth: boolean | null }
  belief?: { confidence: number; stance: string; hop: number; source: string }
  corroborations?: { id: string; name: string; channel: string }[]
  location?: { id: string; name: string } | null
  who_knows?: { npc: string; name: string; confidence: number; stance: string; hop: number; via: string | null }[]
  forecast?: { npc: string; name: string; distance: number; route: string[] }[]
}
export interface Dialogue {
  reply: string; mode: 'llm' | 'scripted'; validated: boolean; tools: { name: string; input: Record<string, unknown> }[]
  facts: string[]; fallback_reason?: string; model?: string; provenance?: Provenance
}
export interface Analytics {
  communities: Record<string, number>
  gossip_hubs: { npc: string; score: number }[]
  best_informed: { npc: string; score: number }[]
  broadcast_reach: { npc: string; score: number }[]
  most_talkative: { npc: string; told: number }[]
  rumour_islands: number
}
export interface Joined {
  returning: boolean; session: number; sessions: number; mode: string
  greetings: { npc: string; text: string; fact?: string }[]; state: WorldState
}
export interface Bench {
  tenants: number; agents: number; ticks_per_world: number; seconds: number; writes: number; queries: number
  writes_per_sec: number; queries_per_sec: number; p50_tick_ms: number; max_tick_ms: number
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { 'content-type': 'application/json', ...(init?.headers ?? {}) },
  })
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail ?? detail } catch { /* not json */ }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}
const post = <T,>(path: string, body?: unknown) =>
  call<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

export const api = {
  health: () => call<{ ok: boolean; db: boolean; mode: string; llm: boolean }>('/health'),
  join: (player_id: string, name: string, reset = false) => post<Joined>('/worlds', { player_id, name, reset }),
  reset: (pid: string) => post<{ session: number; state: WorldState }>(`/worlds/${pid}/reset`),
  state: (pid: string) => call<WorldState>(`/worlds/${pid}`),
  act: (pid: string, body: Record<string, unknown>) =>
    post<{ ok: boolean; message: string; events: SimEvent[]; fact?: string; dialogue?: Dialogue }>(`/worlds/${pid}/act`, body),
  tick: (pid: string, n = 1, delay = 0) => post<{ ticks: TickResult[]; clock: Clock }>(`/worlds/${pid}/tick?n=${n}&delay=${delay}`),
  advanceDay: (pid: string, delay = 1.4) => post<{ ticks: TickResult[]; clock: Clock }>(`/worlds/${pid}/advance-day?delay=${delay}`),
  autoplay: (pid: string, on: boolean, interval = 2.6) => post<{ autoplay: boolean }>(`/worlds/${pid}/autoplay`, { on, interval }),
  talk: (pid: string, npc: string, message: string) => post<Dialogue>(`/worlds/${pid}/npcs/${npc}/talk`, { message }),
  memory: (pid: string, npc: string) => call<Memory>(`/worlds/${pid}/npcs/${npc}/memory`),
  provenance: (pid: string, npc: string, fact: string) =>
    call<Provenance>(`/worlds/${pid}/provenance?npc=${encodeURIComponent(npc)}&fact=${encodeURIComponent(fact)}`),
  spread: (pid: string, fact: string) => call<Pick<Provenance, 'who_knows' | 'forecast'>>(`/worlds/${pid}/facts/${encodeURIComponent(fact)}`),
  analytics: (pid: string) => call<Analytics>(`/worlds/${pid}/analytics`),
  bench: (tenants = 4, ticks = 6) => post<Bench>('/bench', { tenants, ticks }),
}

export function openStream(pid: string, onMessage: (msg: { type: string } & Record<string, unknown>) => void): () => void {
  let ws: WebSocket | null = null
  let closed = false
  let ping: number | undefined
  const connect = () => {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    ws = new WebSocket(`${proto}://${location.host}/ws/${pid}`)
    ws.onmessage = (e) => { try { onMessage(JSON.parse(e.data)) } catch { /* ignore */ } }
    ws.onopen = () => { ping = window.setInterval(() => ws?.readyState === 1 && ws.send('ping'), 20000) }
    ws.onclose = () => { window.clearInterval(ping); if (!closed) window.setTimeout(connect, 1500) }
  }
  connect()
  return () => { closed = true; window.clearInterval(ping); ws?.close() }
}
