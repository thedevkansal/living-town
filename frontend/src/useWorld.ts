import { useCallback, useEffect, useRef, useState } from 'react'
import { api, openStream, type Metrics, type Provenance, type SimEvent, type TickResult, type WorldState } from './api'

export interface Bubble { id: number; npc: string; text: string; long?: boolean }
export interface Pulse { id: number; from: string; to: string; tone: 'gossip' | 'report' | 'testimony' | 'findings' | 'teach' | 'claim'; doubted?: boolean }
export interface Ripple { id: number; npc: string; tone: 'witness' | 'learn' | 'doubt' | 'conflict' | 'task' }
export interface LogLine { id: number; tick: number; slot: string; text: string; tone: string; fact?: string; npc?: string }
export interface Story { fact: string; claim: string; tick: number; kind: string }

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))
const reduced = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

let uid = 1
const nid = () => uid++

export function useWorld(pid: string | null) {
  const [state, setState] = useState<WorldState | null>(null)
  const [bubbles, setBubbles] = useState<Bubble[]>([])
  const [pulses, setPulses] = useState<Pulse[]>([])
  const [ripples, setRipples] = useState<Ripple[]>([])
  const [log, setLog] = useState<LogLine[]>([])
  const [stories, setStories] = useState<Story[]>([])
  const [busy, setBusy] = useState(false)
  const [autoplay, setAutoplay] = useState(false)
  const [toast, setToast] = useState<{ id: number; text: string; tone: 'ok' | 'err' } | null>(null)
  const [metrics, setMetrics] = useState<Metrics | null>(null)
  const [highlight, setHighlight] = useState<Provenance | null>(null)
  const [focusFact, setFocusFact] = useState<string | null>(null)
  const [spread, setSpread] = useState<Pick<Provenance, 'who_knows' | 'forecast'> | null>(null)
  const queue = useRef<Promise<void>>(Promise.resolve())
  const stateRef = useRef<WorldState | null>(null)
  stateRef.current = state
  const whenRef = useRef<{ tick: number; day: number; slot: string } | null>(null)

  const nameOf = useCallback((id: string) => {
    if (id === 'player') return stateRef.current?.player.name ?? 'You'
    return stateRef.current?.npcs.find((n) => n.id === id)?.name ?? id
  }, [])

  const refresh = useCallback(async () => {
    if (!pid) return
    const s = await api.state(pid)
    setState(s)
    if (s.metrics.tick_ms) setMetrics(s.metrics)
  }, [pid])

  // Speech bubbles: at most two at once per place; the rest wait their turn (a short queue per place).
  const shown = useRef<Bubble[]>([])
  const waiting = useRef<Bubble[]>([])
  const placeOf = useCallback((npc: string) => (npc === 'player'
    ? stateRef.current?.player.location
    : stateRef.current?.npcs.find((n) => n.id === npc)?.location), [])
  const showBubble = useCallback((b: Bubble) => {
    shown.current = [...shown.current.filter((x) => x.npc !== b.npc), b]
    setBubbles(shown.current)
    window.setTimeout(() => {
      shown.current = shown.current.filter((x) => x.id !== b.id)
      setBubbles(shown.current)
      // let the next queued speaker at a free place talk
      const next = waiting.current.find((w) => shown.current.filter((x) => placeOf(x.npc) === placeOf(w.npc)).length < 2)
      if (next) { waiting.current = waiting.current.filter((w) => w.id !== next.id); showBubble(next) }
    }, b.long ? 7000 : 3300)
  }, [placeOf])
  const say = useCallback((npc: string, text: string, long?: boolean) => {
    const b = { id: nid(), npc, text, long }
    const place = placeOf(npc)
    const busy = shown.current.filter((x) => x.npc !== npc && placeOf(x.npc) === place).length
    if (busy < 2 || long) { showBubble(b); return }
    // queue (newest wins if a place is very chatty)
    const samePlace = waiting.current.filter((w) => placeOf(w.npc) === place)
    if (samePlace.length >= 2) waiting.current = waiting.current.filter((w) => w.id !== samePlace[0].id)
    waiting.current = [...waiting.current.filter((w) => w.npc !== npc), b]
  }, [placeOf, showBubble])
  const pulse = useCallback((p: Omit<Pulse, 'id'>) => {
    const x = { ...p, id: nid() }
    setPulses((ps) => [...ps, x])
    setTimeout(() => setPulses((ps) => ps.filter((y) => y.id !== x.id)), 1700)
  }, [])
  const ripple = useCallback((npc: string, tone: Ripple['tone']) => {
    const r = { id: nid(), npc, tone }
    setRipples((rs) => [...rs, r])
    setTimeout(() => setRipples((rs) => rs.filter((y) => y.id !== r.id)), 1600)
  }, [])
  const addLog = useCallback((line: Omit<LogLine, 'id' | 'tick' | 'slot'>) => {
    const c = whenRef.current ?? stateRef.current?.clock
    setLog((l) => [{ ...line, id: nid(), tick: c?.tick ?? 0, slot: c ? `Day ${c.day}, ${c.slot.toLowerCase()}` : '' }, ...l].slice(0, 120))
  }, [])
  const addStory = useCallback((fact: string, claim: string, kind: string) => {
    setStories((ss) => (ss.some((s) => s.fact === fact) ? ss : [{ fact, claim, kind, tick: stateRef.current?.clock.tick ?? 0 }, ...ss].slice(0, 20)))
  }, [])

  // ---------------------------------------------------------------- event choreography
  const play = useCallback(async (events: SimEvent[]) => {
    // pacing: a whole day should play out in ~10 s, however busy the town is
    const paced = events.filter((e) => e.type === 'deed' || e.type === 'tell' || e.type === 'task_posted' || e.type === 'teach').length
    const step = reduced ? 40 : Math.min(240, Math.round(1100 / Math.max(1, paced)))
    // moves first so avatars glide together
    const moves = events.filter((e) => e.type === 'move') as Extract<SimEvent, { type: 'move' }>[]
    if (moves.length) {
      setState((s) => s && { ...s, npcs: s.npcs.map((n) => { const m = moves.find((x) => x.npc === n.id); return m ? { ...n, location: m.to } : n }) })
      for (const m of moves.filter((m) => m.reason)) {
        addLog({ tone: 'task', npc: m.npc, text: `${nameOf(m.npc)} heads to ${locName(stateRef.current, m.to)} to ${m.reason === 'confront' ? 'find you' : m.reason === 'report' ? 'report a crime' : 'question a witness'}.` })
      }
      await wait(reduced ? 100 : 720)
    }
    for (const e of events) {
      switch (e.type) {
        case 'player_move':
          setState((s) => s && { ...s, player: { ...s.player, location: e.to } })
          break
        case 'dawn':
          addLog({ tone: 'time', text: `Dawn breaks on day ${e.day}. Hearsay fades a little overnight.` })
          break
        case 'deed':
          addStory(e.fact, e.claim, e.kind)
          e.witnesses.forEach((w) => ripple(w, 'witness'))
          addLog({ tone: e.kind === 'theft' || e.kind === 'caught_lying' ? 'crime' : e.kind === 'missing' ? 'crime' : 'deed', fact: e.fact,
            text: `${e.claim}.` + (e.witnesses.length ? ` Seen by ${e.witnesses.map(nameOf).join(', ')}.` : ' Nobody saw it.') })
          await wait(step)
          break
        case 'tell': {
          const tone = (e.channel as Pulse['tone']) ?? 'gossip'
          pulse({ from: e.from, to: e.to, tone, doubted: e.stance === 'doubted' })
          setTimeout(() => ripple(e.to, e.stance === 'doubted' ? 'doubt' : 'learn'), reduced ? 0 : 900)
          addStory(e.fact, e.claim, 'told')
          const verb = e.channel === 'report' ? 'reports to' : e.channel === 'testimony' ? 'gives testimony to' : e.channel === 'findings' ? 'hands findings to' : 'tells'
          // Testimony is always taken from the eyewitness: show the speaker's certainty, then where the
          // listener's belief now stands (the listener may already have heard it as hearsay, so their
          // hop count stays on the chain they first heard it through).
          const eyewitness = e.channel === 'testimony'
          const outcome = e.stance === 'doubted'
            ? `${nameOf(e.to)} doubts it, ${pct(e.confidence)}`
            : eyewitness ? `${nameOf(e.to)} now ${pct(e.confidence)} sure` : `${pct(e.confidence)} sure, hop ${e.hop}`
          addLog({ tone: e.channel ?? 'gossip', fact: e.fact, npc: e.to,
            text: `${nameOf(e.from)} ${verb} ${nameOf(e.to)}: "${e.claim}" (${eyewitness ? `eyewitness, 100% sure; ${outcome}` : outcome})` })
          await wait(step)
          break
        }
        case 'player_tell':
          pulse({ from: 'player', to: e.npc, tone: 'claim', doubted: e.stance === 'doubted' })
          addStory(e.fact, e.claim, 'claim')
          addLog({ tone: 'claim', fact: e.fact, text: `You tell ${nameOf(e.npc)}: "${e.claim}" (${e.stance}, ${pct(e.confidence)})` })
          break
        case 'say':
          say(e.npc, e.text, e.long)
          break
        case 'conflict':
          ripple(e.npc, 'conflict')
          addLog({ tone: 'conflict', npc: e.npc, text: `${nameOf(e.npc)} weighs two stories. Keeps "${e.winner === e.fact ? e.claim : e.rival_claim}" (${pct(Math.max(e.c_fact, e.c_rival))}) over the other (${pct(Math.min(e.c_fact, e.c_rival))}).` })
          break
        case 'task_posted':
          ripple(e.by, 'task')
          addLog({ tone: 'task', npc: e.by, text: `${nameOf(e.by)} posts ${/^[aeiou]/.test(e.kind) ? 'an' : 'a'} ${e.kind} task: ${e.claim}` })
          await wait(step)
          break
        case 'task_claimed':
          ripple(e.by, 'task')
          addLog({ tone: 'task', npc: e.by, text: `${nameOf(e.by)} claims the ${e.kind === 'investigate' ? 'investigation' : e.kind} from the shared task board.` })
          break
        case 'claim_lost':
          addLog({ tone: 'task', npc: e.by, text: `${nameOf(e.by)} tried to claim a task, but another agent got there first.` })
          break
        case 'task_progress':
          addLog({ tone: 'task', npc: e.by, text: `${nameOf(e.by)} ${e.note}.` })
          break
        case 'task_done':
          addLog({ tone: 'task', npc: e.by, text: `${nameOf(e.by)} completes a task: ${e.result}` })
          break
        case 'handoff':
          pulse({ from: e.from, to: e.to, tone: 'findings' })
          break
        case 'confront':
          ripple(e.npc, 'conflict')
          addLog({ tone: 'crime', npc: e.npc, fact: e.fact, text: `${nameOf(e.npc)} confronts you. "${e.text}"` })
          break
        case 'teach':
          pulse({ from: e.from, to: e.to, tone: 'teach' })
          addLog({ tone: 'teach', npc: e.to, text: `${nameOf(e.from)} teaches ${nameOf(e.to)} how to ${e.name.toLowerCase()}.` })
          await wait(step)
          break
      }
    }
  }, [addLog, addStory, nameOf, pulse, ripple, say])

  const pending = useRef(0)
  const enqueue = useCallback((fn: () => Promise<void>) => {
    pending.current += 1
    queue.current = queue.current.then(fn).catch((err) => console.error(err)).finally(() => { pending.current -= 1 })
    return queue.current
  }, [])
  // only resync from the server once the animation has caught up, so avatars never jump ahead of the story
  const settle = useCallback(async () => { if (pending.current <= 1) await refresh() }, [refresh])

  // ---------------------------------------------------------------- stream
  useEffect(() => {
    if (!pid) return
    setLog([]); setStories([]); setHighlight(null); setFocusFact(null)
    refresh().catch(() => setState(null))
    const stop = openStream(pid, (msg) => {
      if (msg.type === 'tick') {
        const t = msg as unknown as TickResult
        enqueue(async () => {
          setState((s) => s && { ...s, clock: { ...s.clock, tick: t.tick, day: t.day, slot: t.slot, slot_index: t.slot_index } })
          setMetrics(t.metrics)
          whenRef.current = { tick: t.tick, day: t.day, slot: t.slot }
          await play(t.events)
          whenRef.current = null
          await settle()
        })
      } else if (msg.type === 'action') {
        const events = (msg.events as SimEvent[]) ?? []
        enqueue(async () => { await play(events); await settle() })
      } else if (msg.type === 'say') {
        say(msg.npc as string, msg.text as string)
      } else if (msg.type === 'autoplay') {
        setAutoplay(Boolean(msg.on))
      } else if (msg.type === 'reset') {
        setLog([]); setStories([]); refresh()
      }
    })
    return stop
  }, [pid, enqueue, play, refresh, settle, say])

  // spread of the focused story (who knows it, who hears next)
  useEffect(() => {
    if (!pid || !focusFact) { setSpread(null); return }
    let live = true
    api.spread(pid, focusFact).then((s) => live && setSpread(s)).catch(() => live && setSpread(null))
    return () => { live = false }
  }, [pid, focusFact, state?.clock.tick])

  const notify = useCallback((text: string, tone: 'ok' | 'err' = 'ok') => setToast({ id: nid(), text, tone }), [])

  const act = useCallback(async (body: Record<string, unknown>) => {
    if (!pid) return
    try {
      const r = await api.act(pid, body)
      notify(r.message)
      return r
    } catch (e) { notify((e as Error).message, 'err') }
  }, [pid, notify])

  const runTicks = useCallback(async (kind: 'tick' | 'day') => {
    if (!pid || busy) return
    setBusy(true)
    try {
      if (kind === 'tick') await api.tick(pid, 1)
      else await api.advanceDay(pid, reduced ? 0.3 : 0.9)
      await queue.current
    } catch (e) { notify((e as Error).message, 'err') }
    finally { setBusy(false) }
  }, [pid, busy, notify])

  const toggleAuto = useCallback(async () => {
    if (!pid) return
    const on = !autoplay
    setAutoplay(on)
    await api.autoplay(pid, on).catch((e) => notify((e as Error).message, 'err'))
  }, [pid, autoplay, notify])

  const reset = useCallback(async () => {
    if (!pid) return
    if (autoplay) await api.autoplay(pid, false)
    await api.reset(pid)
    setLog([]); setStories([]); setHighlight(null); setFocusFact(null)
    await refresh()
  }, [pid, autoplay, refresh])

  return {
    state, bubbles, pulses, ripples, log, stories, busy, autoplay, toast, metrics, highlight, focusFact, spread,
    setHighlight, setFocusFact, act, runTicks, toggleAuto, reset, refresh, say, notify, nameOf,
  }
}

export type World = ReturnType<typeof useWorld>

export const pct = (x: number) => `${Math.round((x ?? 0) * 100)}%`
export function locName(s: WorldState | null, id: string) {
  return s?.locations.find((l) => l.id === id)?.name ?? id
}
