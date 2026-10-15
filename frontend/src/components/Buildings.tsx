// Little hand-drawn buildings for the town map. (x, y) is the building's door step.

const WALL = '#efe2c8'
const LINE = '#4a3426'

function Window({ x, y, lit }: { x: number; y: number; lit: boolean }) {
  return <rect x={x} y={y} width="9" height="10" rx="1.5" fill={lit ? '#ffcf6b' : '#5c7a8c'} stroke={LINE} strokeWidth="1.5" className={lit ? 'win win--lit' : 'win'} />
}

function House({ x, y, w, h, roof, lit, chimney }: { x: number; y: number; w: number; h: number; roof: string; lit: boolean; chimney?: boolean }) {
  const left = x - w / 2
  const top = y - h
  return (
    <g>
      <ellipse cx={x + 6} cy={y + 2} rx={w * 0.62} ry="7" fill="#3d3326" opacity=".22" />
      {chimney && <rect x={x + w * 0.18} y={top - 30} width="10" height="22" fill="#8a6a52" stroke={LINE} strokeWidth="2" />}
      <rect x={left} y={top} width={w} height={h} fill={WALL} stroke={LINE} strokeWidth="2.5" />
      <path d={`M${left - 8} ${top + 2} L${x} ${top - h * 0.75} L${left + w + 8} ${top + 2} Z`} fill={roof} stroke={LINE} strokeWidth="2.5" strokeLinejoin="round" />
      <rect x={x - 7} y={y - 18} width="14" height="18" rx="6" fill="#7a5638" stroke={LINE} strokeWidth="2" />
      <Window x={left + 7} y={top + 9} lit={lit} />
      <Window x={left + w - 16} y={top + 9} lit={lit} />
    </g>
  )
}

export function Building({ kind, x, y, lit }: { kind: string; x: number; y: number; lit: boolean }) {
  switch (kind) {
    case 'shop': // bakery: warm roof, a hanging loaf sign
      return (
        <g>
          <House x={x} y={y} w={62} h={38} roof="#c9733f" lit={lit} chimney />
          <path d={`M${x + 31} ${y - 30} h14`} stroke={LINE} strokeWidth="2" />
          <ellipse cx={x + 45} cy={y - 23} rx="8" ry="5" fill="#d9a35b" stroke={LINE} strokeWidth="1.5" />
        </g>
      )
    case 'workshop': // forge: dark roof, glowing hearth, anvil
      return (
        <g>
          <House x={x} y={y} w={70} h={36} roof="#5b4a44" lit chimney />
          <circle cx={x - 18} cy={y - 8} r="6" fill="#ff8c3a" className="hearth" />
          <path d={`M${x + 40} ${y - 6} h18 l-4 6 h-10 z`} fill="#3f3f46" stroke={LINE} strokeWidth="1.5" />
        </g>
      )
    case 'tavern': // the Crooked Kettle: big, two storeys, kettle sign
      return (
        <g>
          <House x={x} y={y} w={84} h={50} roof="#8e3b46" lit={lit} chimney />
          <path d={`M${x - 42} ${y - 25} h84`} stroke={LINE} strokeWidth="2" />
          <path d={`M${x + 44} ${y - 40} h12 v6`} stroke={LINE} strokeWidth="2" fill="none" />
          <path d={`M${x + 50} ${y - 33} q-7 0 -7 7 q0 6 7 6 q7 0 7 -6 q0 -7 -7 -7z`} fill="#b08d57" stroke={LINE} strokeWidth="1.5" />
        </g>
      )
    case 'market': // striped awnings
      return (
        <g>
          <ellipse cx={x + 4} cy={y + 2} rx="62" ry="7" fill="#3d3326" opacity=".22" />
          {[-34, 0, 34].map((dx, i) => (
            <g key={dx}>
              <rect x={x + dx - 15} y={y - 24} width="30" height="22" fill="#e8d3a6" stroke={LINE} strokeWidth="2" />
              <path d={`M${x + dx - 18} ${y - 24} l4 -14 h28 l4 14 z`} fill={['#d1495b', '#2e86ab', '#edae49'][i]} stroke={LINE} strokeWidth="2" strokeLinejoin="round" />
              <path d={`M${x + dx - 8} ${y - 38} v14 M${x + dx + 4} ${y - 38} v14`} stroke="#fff" strokeWidth="3" opacity=".6" />
            </g>
          ))}
        </g>
      )
    case 'guard': // stone tower with a banner
      return (
        <g>
          <ellipse cx={x + 6} cy={y + 2} rx="42" ry="7" fill="#3d3326" opacity=".22" />
          <rect x={x - 28} y={y - 66} width="56" height="66" fill="#b9b3a6" stroke={LINE} strokeWidth="2.5" />
          {[-28, -14, 0, 14].map((dx) => <rect key={dx} x={x + dx} y={y - 76} width="10" height="10" fill="#b9b3a6" stroke={LINE} strokeWidth="2" />)}
          <rect x={x - 7} y={y - 20} width="14" height="20" rx="6" fill="#5d4a3a" stroke={LINE} strokeWidth="2" />
          <Window x={x - 4.5} y={y - 52} lit={lit} />
          <path d={`M${x + 28} ${y - 76} v-26`} stroke={LINE} strokeWidth="2" />
          <path d={`M${x + 28} ${y - 102} h20 l-6 6 l6 6 h-20 z`} fill="#33658a" stroke={LINE} strokeWidth="1.5" className="flag" />
        </g>
      )
    case 'chapel': // steeple
      return (
        <g>
          <House x={x} y={y} w={58} h={42} roof="#6d7fa3" lit={lit} />
          <rect x={x - 9} y={y - 96} width="18" height="30" fill={WALL} stroke={LINE} strokeWidth="2" />
          <path d={`M${x - 12} ${y - 96} L${x} ${y - 124} L${x + 12} ${y - 96} Z`} fill="#6d7fa3" stroke={LINE} strokeWidth="2" strokeLinejoin="round" />
          <circle cx={x} cy={y - 84} r="4" fill={lit ? '#ffcf6b' : '#f2e3b3'} stroke={LINE} strokeWidth="1.5" />
        </g>
      )
    case 'homes': // a row of cottages
      return (
        <g>
          <House x={x - 36} y={y} w={40} h={28} roof="#9c6644" lit={lit} chimney />
          <House x={x + 12} y={y - 6} w={38} h={26} roof="#7f5539" lit={lit} />
          <House x={x + 54} y={y} w={34} h={24} roof="#b07d48" lit={lit} />
        </g>
      )
    default:
      return <House x={x} y={y} w={50} h={34} roof="#8a6a52" lit={lit} />
  }
}
