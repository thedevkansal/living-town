import { useState } from 'react'
import type { World } from '../useWorld'

const SLOT_ICON: Record<string, string> = { Dawn: '◒', Morning: '☀', Midday: '☀', Afternoon: '☀', Evening: '◓', Night: '☾' }

export function ActionBar({ world, onTalk, compact }: { world: World; onTalk: (npc: string) => void; compact?: boolean }) {
  const s = world.state!
  const [who, setWho] = useState<string | null>(null)
  const [mode, setMode] = useState<'none' | 'gift' | 'rumour'>('none')
  const [blame, setBlame] = useState('pip')
  const [thing, setThing] = useState('hammer')
  const here = s.npcs.filter((n) => n.location === s.player.location)
  const target = here.find((n) => n.id === who) ?? null
  const loc = s.locations.find((l) => l.id === s.player.location)

  const run = async (body: Record<string, unknown>) => { await world.act(body); setMode('none') }

  return (
    <div className={`actions ${compact ? 'actions--compact' : ''}`}>
      <div className="actions__where">
        <label className="walk">
          <span>You are at</span>
          <select value={s.player.location} onChange={(e) => world.act({ action: 'move', location: e.target.value })} aria-label="Walk to">
            {s.locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
          </select>
        </label>
        {!compact && <small>{loc?.name === 'Town Square' ? 'Click any building on the map to walk there.' : here.length ? `${here.map((n) => n.name).join(', ')} ${here.length === 1 ? 'is' : 'are'} here.` : 'Nobody is here.'}</small>}
      </div>

      <div className="actions__do">
        {s.items_here.map((i) => (
          <button key={i.id} className="btn btn--thread" onClick={() => run({ action: 'steal', item: i.id })} title={`Belongs to ${i.owner_name}`}>
            Steal the {i.name}
          </button>
        ))}
        {here.length > 0 && (
          <div className="people" role="group" aria-label="People here">
            {here.map((n) => (
              <button key={n.id} className={`person ${who === n.id ? 'is-on' : ''}`} style={{ ['--c' as string]: n.color }}
                onClick={() => { setWho(who === n.id ? null : n.id); setMode('none') }} aria-pressed={who === n.id}>
                {n.name.replace('Captain ', '').replace('Sister ', '')}
              </button>
            ))}
          </div>
        )}
        {target && (
          // a floating tray above the bar: choosing someone never reflows the bar or resizes the map
          <div className="tray">
            {mode === 'gift' && (
              <div className="pop">
                Give {target.name.split(' ').pop()}
                {s.player.items.map((i) => <button key={i.id} className="chip" onClick={() => run({ action: 'gift', npc: target.id, item: i.id })}>{i.name}</button>)}
              </div>
            )}
            {mode === 'rumour' && (
              <form className="pop" onSubmit={(e) => { e.preventDefault(); run({ action: 'lie', npc: target.id, subject: blame, item: thing }) }}>
                Tell {target.name.split(' ').pop()} that
                <select value={blame} onChange={(e) => setBlame(e.target.value)} aria-label="Who to blame">
                  {s.npcs.filter((n) => n.id !== target.id).map((n) => <option key={n.id} value={n.id}>{n.name}</option>)}
                </select>
                stole the
                <select value={thing} onChange={(e) => setThing(e.target.value)} aria-label="Which item">
                  {[['hammer', 'silver hammer'], ['loaf', 'honey loaf'], ['apples', 'apples'], ['cask', 'cask of ale'], ['herbs', 'herbs'], ['candlestick', 'candlestick']].map(([id, n]) => <option key={id} value={id}>{n}</option>)}
                </select>
                <button className="btn btn--thread">Say it</button>
              </form>
            )}
            <div className="verbs">
              <span className="verbs__who">{target.name.split(' ').pop()}:</span>
              <button className="btn" onClick={() => onTalk(target.id)}>Talk</button>
              <button className="btn" onClick={() => setMode(mode === 'gift' ? 'none' : 'gift')} disabled={!s.player.items.length}>Give</button>
              <button className="btn" onClick={() => run({ action: 'help', npc: target.id })}>Help</button>
              <button className="btn" onClick={() => run({ action: 'trade', npc: target.id })}>Trade</button>
              <button className={`btn btn--thread-ghost ${mode === 'rumour' ? 'is-on' : ''}`} onClick={() => setMode(mode === 'rumour' ? 'none' : 'rumour')}>Spread a rumour</button>
            </div>
          </div>
        )}
      </div>

      <div className="actions__time">
        <div className="clock" aria-live="polite">
          <span className="clock__icon" aria-hidden>{SLOT_ICON[s.clock.slot]}</span>
          <span><b>Day <span className="num">{s.clock.day}</span></b> {s.clock.slot}</span>
        </div>
        <button className="btn" onClick={() => world.runTicks('tick')} disabled={world.busy || world.autoplay}>Wait an hour</button>
        <button className="btn btn--ember btn--day" onClick={() => world.runTicks('day')} disabled={world.busy || world.autoplay}>
          {world.busy ? 'Time passes' : s.clock.slot_index === 0 ? 'Live through the day' : 'Sleep till dawn'}
        </button>
        <button className={`btn btn--toggle ${world.autoplay ? 'is-on' : ''}`} onClick={world.toggleAuto} aria-pressed={world.autoplay}>
          {world.autoplay ? 'Pause' : 'Auto-play'}
        </button>
      </div>
    </div>
  )
}
