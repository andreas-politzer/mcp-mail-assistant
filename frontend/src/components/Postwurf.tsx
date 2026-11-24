import { useCallback, useEffect, useRef, useState } from 'react'
import { Win95Window } from './Win95Window'
import { BOOM_TICKS, H, HALL_OF_FAME, MAX_NAME, PLAYER_W, PLAYER_Y, UFO_W, UFO_Y, W, cleanName, hallWithPlayer, newGame, step, type Game, type Input } from '../logic/postwurf'

const BEST_KEY = 'mail-assistant-postwurf-best'
const readBest = (): number => {
  try {
    const n = Number(localStorage.getItem(BEST_KEY))
    return Number.isFinite(n) && n > 0 ? n : 0
  } catch {
    return 0
  }
}
const NAME_KEY = 'mail-assistant-postwurf-name'
const readName = (): string => {
  try {
    return (localStorage.getItem(NAME_KEY) ?? '').slice(0, MAX_NAME)
  } catch {
    return ''
  }
}
const writeName = (v: string) => {
  try {
    localStorage.setItem(NAME_KEY, v)
  } catch {
    /* storage can be unavailable; the name then only lasts until the window closes */
  }
}
const writeBest = (n: number) => {
  try {
    localStorage.setItem(BEST_KEY, String(n))
  } catch {
    /* storage can be unavailable; the game still works */
  }
}

// Our own alien figures, three kinds with two leg poses each. Drawn from text rows, bright colours with a glow.
const KIND_A = [
  '..K........K..',
  '...K......K...',
  '..KKKKKKKKKK..',
  '.KGGGGGGGGGGK.',
  'KGGWWGGGGWWGGK',
  'KGGWKGGGGWKGGK',
  'KGGGGGGGGGGGGK',
  '.KGGKKKKKKGGK.',
]
const KIND_B = [
  '....KKKKKK....',
  '..KKGGGGGGKK..',
  '.KGGGWGGWGGGK.',
  'KKKKKKKKKKKKKK',
  'KGYGKGYGKGYGKK',
  '.KKK.KKKK.KKK.',
]
const KIND_C = [
  '..K...KK...K..',
  '...K.KKKK.K...',
  '..KKKKKKKKKK..',
  '.KGGGGGGGGGGK.',
  'KGGKWGGGGWKGGK',
  'KGGGGGKKGGGGGK',
  'KGGGGGGGGGGGGK',
  '.KK.KKKKKK.KK.',
]
const LEGS: Record<string, [string[], string[]]> = {
  A: [['.KK..K..K..KK.', '.K...K..K...K.'], ['..KK.K..K.KK..', '..K..K..K..K..']],
  B: [['..K..K..K..K..', '.K..K....K..K.'], ['.K..K..K..K..K', '..K..K..K.K...']],
  C: [['..K.K....K.K..', '.K...K..K...K.'], ['.K.K......K.K.', '..K..K..K..K..']],
}
// Rows from the top: one of kind C, two of B, two of A.
const KIND_OF_ROW = ['C', 'B', 'B', 'A', 'A']
const BODIES: Record<string, string[]> = { A: KIND_A, B: KIND_B, C: KIND_C }
const COLORS = ['#ff4f6d', '#ffb43c', '#5dff8a', '#4fd5ff', '#c78bff']

function Alien({ x, y, row, pose }: { x: number; y: number; row: number; pose: 0 | 1 }) {
  const kind = KIND_OF_ROW[row] ?? 'A'
  const rows = [...BODIES[kind], ...LEGS[kind][pose]]
  const color = COLORS[row % COLORS.length]
  const fill: Record<string, string> = { K: '#1b1b2b', G: color, W: '#ffffff', Y: '#fff27a' }
  return (
    <g transform={`translate(${x} ${y})`} shapeRendering="crispEdges" style={{ filter: `drop-shadow(0 0 1.5px ${color})` }} data-alien>
      {rows.flatMap((r, ry) => [...r].map((ch, rx) => (fill[ch] ? <rect key={`${rx}-${ry}`} x={rx} y={ry} width={1} height={1} fill={fill[ch]} /> : null)))}
    </g>
  )
}

// A fixed sky of stars, the same on every start.
const STARS = (() => {
  let seed = 7
  const next = () => ((seed = (seed * 16807) % 2147483647) / 2147483647)
  return Array.from({ length: 70 }, () => ({ x: Math.round(next() * W), y: Math.round(next() * H), r: next() < 0.15 ? 1.1 : 0.6, o: 0.4 + next() * 0.6 }))
})()

export function Postwurf({ onClose, onFocus, initial }: { onClose: () => void; onFocus?: () => void; initial?: { x: number; y: number } }) {
  const [game, setGame] = useState<Game>(() => newGame())
  const [phase, setPhase] = useState<'ready' | 'playing' | 'over'>('ready')
  const [best, setBest] = useState(readBest)
  const [name, setName] = useState(readName)
  const input = useRef<Input>({ left: false, right: false, fire: false })
  const box = useRef<HTMLDivElement>(null)

  const start = useCallback(() => {
    input.current = { left: false, right: false, fire: false }
    setGame(newGame())
    setPhase('playing')
    box.current?.focus()
  }, [])

  useEffect(() => {
    if (phase !== 'playing') return
    const id = setInterval(() => setGame((g) => step(g, input.current)), 40)
    return () => clearInterval(id)
  }, [phase])

  useEffect(() => {
    if (phase === 'playing' && game.over) {
      setPhase('over')
      if (game.score > best) {
        setBest(game.score)
        writeBest(game.score)
      }
    }
  }, [phase, game.over, game.score, best])

  const key = (down: boolean) => (e: React.KeyboardEvent) => {
    if ((e.target as HTMLElement).tagName === 'INPUT') return // typing the name must not move or fire
    const k = e.key
    if (k === 'ArrowLeft' || k === 'a') input.current.left = down
    else if (k === 'ArrowRight' || k === 'd') input.current.right = down
    else if (k === ' ' || k === 'ArrowUp') input.current.fire = down
    else return
    e.preventDefault()
  }
  const hold = (name: keyof Input) => ({
    onPointerDown: () => { input.current[name] = true },
    onPointerUp: () => { input.current[name] = false },
    onPointerLeave: () => { input.current[name] = false },
  })

  const hall = hallWithPlayer(best, cleanName(name))
  const rank = hall.findIndex((h) => h.me)
  const pose = (Math.floor(game.tick / 8) % 2) as 0 | 1

  return (
    <Win95Window title="Alienalarm" width={400} onClose={onClose} onFocus={onFocus} initial={initial}>
      <div ref={box} tabIndex={0} onKeyDown={key(true)} onKeyUp={key(false)} className="outline-none" aria-label="Spielfeld Alienalarm">
        <div className="flex justify-between text-[12px] mb-1">
          <span>Punkte: <b data-score>{game.score}</b></span>
          <span>Level {game.level}</span>
          <span aria-label={`Leben: ${game.lives}`}>{'▲'.repeat(Math.max(0, game.lives))}</span>
        </div>
        <svg viewBox={`0 0 ${W} ${H}`} width={360} height={Math.round((360 * H) / W)} className="block mx-auto" style={{ background: '#000000', border: '2px solid', borderColor: 'var(--bevel-dark) var(--bevel-light) var(--bevel-light) var(--bevel-dark)' }} role="img" aria-label="Spielfeld">
          {STARS.map((st, i) => <circle key={i} cx={st.x} cy={st.y} r={st.r} fill="#ffffff" opacity={st.o} />)}
          {game.ufo && (
            <g transform={`translate(${game.ufo.x} ${UFO_Y})`} style={{ filter: 'drop-shadow(0 0 2px #ff4f6d)' }} data-ufo>
              <ellipse cx={UFO_W / 2} cy={6} rx={UFO_W / 2} ry={3.5} fill="#ff4f6d" stroke="#1b1b2b" strokeWidth={0.8} />
              <path d={`M${UFO_W / 2 - 4} 4.5 Q${UFO_W / 2} -3 ${UFO_W / 2 + 4} 4.5 Z`} fill="#9fe8ff" stroke="#1b1b2b" strokeWidth={0.6} />
              <circle cx={4} cy={6.5} r={0.9} fill="#fff27a" /><circle cx={UFO_W / 2} cy={7.5} r={0.9} fill="#fff27a" /><circle cx={UFO_W - 4} cy={6.5} r={0.9} fill="#fff27a" />
            </g>
          )}
          {game.aliens.filter((a) => a.alive).map((a, i) => <Alien key={i} x={a.x} y={a.y} row={a.row} pose={pose} />)}
          {game.shot && <rect x={game.shot.x - 1} y={game.shot.y} width={2} height={6} fill="#ffe27a" data-shot />}
          {game.bombs.map((b, i) => <rect key={i} x={b.x - 1.5} y={b.y} width={3} height={6} fill="#ff6b81" />)}
          <g
            transform={`translate(${game.player - PLAYER_W / 2} ${PLAYER_Y - 4})`}
            style={{ filter: 'drop-shadow(0 0 1.5px #ffb43c)' }}
            opacity={game.hurt > 0 && Math.floor(game.hurt / 2) % 2 === 1 ? 0.25 : 1}
            data-ship
            data-damage={Math.min(2, Math.max(0, 3 - game.lives))}
          >
            <polygon points={`${PLAYER_W / 2},-4 ${PLAYER_W / 2 + 3},4 ${PLAYER_W / 2 + 3},11 ${PLAYER_W / 2 - 3},11 ${PLAYER_W / 2 - 3},4`} fill="#e8ecff" stroke="#1b1b2b" strokeWidth={0.8} />
            <polygon points={`${PLAYER_W / 2 - 3},7 0,13 0,15 ${PLAYER_W / 2 - 3},11`} fill="#ff4f6d" stroke="#1b1b2b" strokeWidth={0.8} />
            <polygon points={`${PLAYER_W / 2 + 3},7 ${PLAYER_W},13 ${PLAYER_W},15 ${PLAYER_W / 2 + 3},11`} fill="#ff4f6d" stroke="#1b1b2b" strokeWidth={0.8} />
            <circle cx={PLAYER_W / 2} cy={4} r={1.6} fill="#4fd5ff" />
            <polygon points={`${PLAYER_W / 2 - 2},11 ${PLAYER_W / 2},${pose ? 17 : 15} ${PLAYER_W / 2 + 2},11`} fill="#ffb43c" />
            {game.lives <= 2 && (
              <g data-scorch>
                <polygon points={`${PLAYER_W / 2 + 3},9 ${PLAYER_W - 1},13.5 ${PLAYER_W / 2 + 3},11.5`} fill="#2b2b3a" />
                <circle cx={PLAYER_W / 2 + 6} cy={pose ? 2 : 0} r={2} fill="#9aa0b4" opacity={0.7} />
                <circle cx={PLAYER_W / 2 + 8} cy={pose ? -3 : -5} r={2.6} fill="#c3c8d8" opacity={0.45} />
              </g>
            )}
            {game.lives <= 1 && (
              <g data-wrecked>
                <polygon points={`${PLAYER_W / 2 - 3},9 1,13.5 ${PLAYER_W / 2 - 3},11.5`} fill="#2b2b3a" />
                <polyline points={`${PLAYER_W / 2 - 1},0 ${PLAYER_W / 2 + 1},4 ${PLAYER_W / 2 - 1},7 ${PLAYER_W / 2 + 1},10`} fill="none" stroke="#1b1b2b" strokeWidth={0.9} />
                <circle cx={PLAYER_W / 2 - 6} cy={pose ? -1 : 1} r={2.2} fill="#9aa0b4" opacity={0.7} />
                <circle cx={PLAYER_W / 2 - 4} cy={pose ? -6 : -4} r={2.8} fill="#c3c8d8" opacity={0.45} />
                <polygon points={`${PLAYER_W / 2 - 2},11 ${PLAYER_W / 2},${pose ? 14 : 12} ${PLAYER_W / 2 + 2},11`} fill="#ff6b3c" />
              </g>
            )}
          </g>
          {game.boom && (() => {
            const p = 1 - game.boom.ttl / BOOM_TICKS
            return (
              <g data-boom>
                <circle cx={game.boom.x} cy={game.boom.y} r={3 + p * 11} fill="#ffb43c" opacity={0.9 * (1 - p)} />
                <circle cx={game.boom.x} cy={game.boom.y} r={1.5 + p * 6} fill="#fff27a" opacity={1 - p} />
                <text x={game.boom.x} y={game.boom.y - 6 - p * 10} textAnchor="middle" fill="#fff27a" fontSize={9} fontWeight="bold" fontFamily="Tahoma, sans-serif" data-boom-points>+{game.boom.points}</text>
              </g>
            )
          })()}
          {phase === 'playing' && game.clear > 0 && (
            <g data-clear>
              <text x={W / 2} y={H / 2 - 6} textAnchor="middle" fill="#fff" fontSize={15} fontWeight="bold" fontFamily="Tahoma, sans-serif">Level {game.level} geschafft!</text>
              <text x={W / 2} y={H / 2 + 14} textAnchor="middle" fill="#ffe27a" fontSize={12} fontFamily="Tahoma, sans-serif">Nächste Runde …</text>
            </g>
          )}
          {phase !== 'playing' && (
            <text x={W / 2} y={H / 2} textAnchor="middle" fill="#fff" fontSize={14} fontFamily="Tahoma, sans-serif">
              {phase === 'ready' ? 'Die Außerirdischen kommen!' : 'Die Erde ist verloren'}
            </text>
          )}
        </svg>

        <div className="flex gap-2 justify-center mt-2">
          <button className="bevel" {...hold('left')} aria-label="Nach links">◀</button>
          <button className="bevel font-bold" {...hold('fire')} aria-label="Feuer">Feuer</button>
          <button className="bevel" {...hold('right')} aria-label="Nach rechts">▶</button>
        </div>
        <div className="text-[11px] mt-1 text-center" style={{ opacity: 0.75 }}>Pfeiltasten und Leertaste gehen auch.</div>

        {phase !== 'playing' && (
          <div className="mt-3">
            <label className="flex items-center gap-2 text-[12px] mb-2">
              <span>Dein Name:</span>
              <input
                type="text"
                className="flex-1"
                style={{ border: '2px inset #fff', padding: '1px 3px', background: '#fff' }}
                value={name}
                maxLength={MAX_NAME}
                placeholder="Spieler"
                aria-label="Dein Name für den Highscore"
                onChange={(e) => { setName(e.target.value); writeName(e.target.value) }}
              />
            </label>
            <div className="font-bold text-[12px]">Highscore</div>
            <ol className="text-[12px] mt-1" style={{ listStyle: 'none', padding: 0, margin: 0 }} aria-label="Highscore">
              {hall.map((h, i) => (
                <li key={h.name + h.score} className="flex justify-between" style={h.me ? { fontWeight: 700, color: '#0a246a' } : undefined}>
                  <span>{i + 1}. {h.name}</span><span>{h.score}</span>
                </li>
              ))}
            </ol>
            {phase === 'over' && rank === 0 && <div className="text-[12px] mt-1" role="status">Neue Spitze! Dein Name steht jetzt ganz oben.</div>}
            {phase === 'over' && rank > 0 && <div className="text-[12px] mt-1" role="status">Platz {rank + 1}! Dein Name steht jetzt im Highscore.</div>}
            {phase === 'over' && rank < 0 && best > 0 && <div className="text-[12px] mt-1">Dein Bestwert: {best}. Bis in den Highscore fehlen noch {HALL_OF_FAME[HALL_OF_FAME.length - 1].score - best + 1}.</div>}
          </div>
        )}
        <div className="flex justify-end gap-2 mt-3">
          {phase !== 'playing' && <button className="bevel font-bold" onClick={start}>{phase === 'ready' ? 'Start' : 'Noch einmal'}</button>}
          <button className="bevel" onClick={onClose}>Schließen</button>
        </div>
      </div>
    </Win95Window>
  )
}
