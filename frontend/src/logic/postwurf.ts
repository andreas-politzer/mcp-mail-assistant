// Alienalarm (file name postwurf.ts is from an earlier idea): a small shooting game, aliens come down, you shoot up. Pure game rules, no drawing and no timers, so they can be tested.
// Everything here is our own invention: the idea "things come down from above and you shoot them" is free, the
// figures, names and look are ours.

export const W = 240
export const H = 260
export const PLAYER_Y = 238
export const PLAYER_W = 16
const ALIEN_W = 14
const ALIEN_H = 10
const COLS = 8
const ROWS = 5
const PLAYER_SPEED = 3
const SHOT_SPEED = 12
const SHOT_SAMPLE = 3 // the flight is checked every few pixels, so a fast shot cannot skip over an alien
const BOMB_SPEED = 2.5

export interface Alien { x: number; y: number; row: number; alive: boolean }
export interface Shot { x: number; y: number }
export interface Game {
  player: number
  shot: Shot | null
  bombs: Shot[]
  aliens: Alien[]
  dir: 1 | -1
  score: number
  lives: number
  level: number
  tick: number
  over: boolean
  hurt: number // ticks left of the blinking after a hit, only for the picture
  ufo: { x: number; dir: 1 | -1 } | null
  boom: { x: number; y: number; ttl: number; points: number } | null // the saucer's explosion, only for the picture
  clear: number // ticks left of the pause between two waves; 0 while the wave is going on
}
export interface Input { left: boolean; right: boolean; fire: boolean }

export const POINTS_BY_ROW = [50, 40, 30, 20, 10]
export const UFO_Y = 12
export const UFO_W = 16
const UFO_SPEED = 1.4
const UFO_POINTS = [50, 100, 150, 300]
export const HURT_TICKS = 16
export const BOOM_TICKS = 20
export const CLEAR_TICKS = 50

export function newWave(level: number): Alien[] {
  const out: Alien[] = []
  for (let r = 0; r < ROWS; r++) for (let c = 0; c < COLS; c++) out.push({ x: 20 + c * 22, y: 28 + r * 17, row: r, alive: true })
  // Later waves start a little lower.
  const drop = Math.min(5, level - 1) * 6
  return out.map((a) => ({ ...a, y: a.y + drop }))
}

export function newGame(level = 1, keep?: Pick<Game, 'score' | 'lives'>): Game {
  return {
    player: W / 2,
    shot: null,
    bombs: [],
    aliens: newWave(level),
    dir: 1,
    score: keep?.score ?? 0,
    lives: keep?.lives ?? 3,
    level,
    tick: 0,
    over: false,
    hurt: 0,
    ufo: null,
    boom: null,
    clear: 0,
  }
}

export const aliveCount = (g: Game) => g.aliens.filter((a) => a.alive).length

// How many ticks pass between two steps of the crowd: fewer aliens and higher levels mean faster.
export function period(g: Game): number {
  const n = aliveCount(g)
  return Math.max(2, Math.round(2 + n * 0.55 - (g.level - 1)))
}

const hit = (px: number, py: number, a: Alien) => px >= a.x - 1 && px <= a.x + ALIEN_W + 1 && py >= a.y - 1 && py <= a.y + ALIEN_H + 1

// One tick of the game. `rng` returns a number in [0, 1); tests pass their own.
export function step(g: Game, input: Input, rng: () => number = Math.random): Game {
  if (g.over) return g
  if (g.clear > 0) {
    // Between two waves: nothing moves, the banner shows. Then the next wave starts with score and lives kept.
    if (g.clear === 1) return { ...newGame(g.level + 1, { score: g.score, lives: g.lives }), player: g.player, tick: g.tick + 1 }
    return { ...g, clear: g.clear - 1, tick: g.tick + 1, boom: g.boom && g.boom.ttl > 1 ? { ...g.boom, ttl: g.boom.ttl - 1 } : null }
  }
  let { player, shot, dir, score, lives, level, ufo } = g
  let boom = g.boom && g.boom.ttl > 1 ? { ...g.boom, ttl: g.boom.ttl - 1 } : null
  let aliens = g.aliens.map((a) => ({ ...a }))
  let bombs = g.bombs.map((b) => ({ ...b }))
  const tick = g.tick + 1
  let hurt = Math.max(0, g.hurt - 1)

  if (input.left) player -= PLAYER_SPEED
  if (input.right) player += PLAYER_SPEED
  player = Math.max(PLAYER_W / 2, Math.min(W - PLAYER_W / 2, player))

  // The shot flies up. `path` holds the heights it passed this tick, so a fast shot still hits what is in between.
  let path: number[] = []
  let shotX = shot ? shot.x : player
  if (input.fire && !shot) {
    shot = { x: player, y: PLAYER_Y - 6 }
    shotX = shot.x
    path = [shot.y]
  } else if (shot) {
    const to = shot.y - SHOT_SPEED
    for (let y = shot.y - SHOT_SAMPLE; y > to; y -= SHOT_SAMPLE) path.push(y)
    path.push(to)
    shot = to < 0 ? null : { x: shot.x, y: to }
  }
  let live = path.length > 0 // the shot has not hit anything yet

  if (live) {
    const target = path.map((y) => aliens.find((a) => a.alive && hit(shotX, y, a))).find((a) => a !== undefined)
    if (target) {
      target.alive = false
      score += POINTS_BY_ROW[target.row]
      shot = null
      live = false
    }
  }

  // The crowd moves in steps.
  const living = aliens.filter((a) => a.alive)
  if (living.length > 0 && tick % period({ ...g, aliens }) === 0) {
    const minX = Math.min(...living.map((a) => a.x))
    const maxX = Math.max(...living.map((a) => a.x + ALIEN_W))
    if ((dir === 1 && maxX + 4 > W - 4) || (dir === -1 && minX - 4 < 4)) {
      aliens.forEach((a) => (a.y += 8))
      dir = dir === 1 ? -1 : 1
    } else {
      aliens.forEach((a) => (a.x += 4 * dir))
    }
  }

  // The lowest alien of a column may drop a bomb.
  const chance = 0.02 + 0.006 * level
  if (living.length > 0 && rng() < chance) {
    const cols = new Map<number, Alien>()
    for (const a of aliens) if (a.alive && (!cols.has(a.x) || cols.get(a.x)!.y < a.y)) cols.set(a.x, a)
    const bottoms = [...cols.values()]
    const from = bottoms[Math.floor(rng() * bottoms.length)]
    if (from) bombs.push({ x: from.x + ALIEN_W / 2, y: from.y + ALIEN_H })
  }
  bombs = bombs.map((b) => ({ x: b.x, y: b.y + BOMB_SPEED })).filter((b) => b.y < H)

  // The bonus saucer crosses the top now and then. A hit gives 50, 100, 150 or 300 points.
  if (ufo) {
    ufo = { x: ufo.x + UFO_SPEED * ufo.dir, dir: ufo.dir }
    if (ufo.x < -UFO_W || ufo.x > W + UFO_W) ufo = null
  } else if (living.length > 0 && rng() < 0.004) {
    ufo = Math.floor(rng() * 2) === 0 ? { x: -UFO_W, dir: 1 } : { x: W, dir: -1 }
  }
  if (ufo && live && shotX >= ufo.x && shotX <= ufo.x + UFO_W && path.some((y) => y >= UFO_Y - 2 && y <= UFO_Y + 10)) {
    const points = UFO_POINTS[Math.min(UFO_POINTS.length - 1, Math.floor(rng() * UFO_POINTS.length))]
    score += points
    boom = { x: ufo.x + UFO_W / 2, y: UFO_Y + 4, ttl: BOOM_TICKS, points }
    ufo = null
    shot = null
    live = false
  }

  // A bomb that reaches the player costs a life and clears the sky for a moment.
  const struck = bombs.some((b) => b.y >= PLAYER_Y - 4 && b.y <= PLAYER_Y + 8 && Math.abs(b.x - player) <= PLAYER_W / 2)
  if (struck) {
    lives -= 1
    hurt = HURT_TICKS
    bombs = []
    shot = null
  }

  const invaded = aliens.some((a) => a.alive && a.y + ALIEN_H >= PLAYER_Y - 6)
  const over = lives <= 0 || invaded

  let next: Game = { player, shot, bombs, aliens, dir, score, lives, level, tick, over, hurt, ufo, boom, clear: 0 }
  if (!over && aliveCount(next) === 0) next = { ...next, shot: null, bombs: [], ufo: null, clear: CLEAR_TICKS }
  return next
}

// ---- The hall of fame ------------------------------------------------------------------------------------------------
// The scores are invented (the list is a gimmick, nobody has reached them). The names are a wink at figures from popular fiction, a conscious exception to ADR 0005
// (see docs/decisions/0005-look-and-assets.md). Only names appear here: no pictures, no text from the works. All of them
// are fictional characters, never real people. Swap this list in one place if you like.
export const HALL_OF_FAME: { name: string; score: number }[] = [
  { name: 'Zaphod Beeblebrox', score: 4200 },
  { name: 'Jean-Luc Picard', score: 3900 },
  { name: 'Malcolm Reynolds', score: 3600 },
  { name: 'John Sheppard', score: 3100 },
  { name: 'William Riker', score: 2700 },
  { name: 'Deanna Troi', score: 2200 },
  { name: 'Rodney McKay', score: 1700 },
  { name: 'Han Solo', score: 1300 },
]

export const PLAYER_NAME = 'SPIELER' // shown until the player types a name
export const MAX_NAME = 16

// Cleans a typed name: no control characters, trimmed, at most MAX_NAME characters. Empty gives the default.
export const cleanName = (value: string): string => value.replace(/[\u0000-\u001f\u007f]/g, '').trim().slice(0, MAX_NAME) || PLAYER_NAME

// The list with the player's best score, put in at the real rank. The list keeps its length, so the last entry drops out.
// On a tie the invented entry keeps the higher place; a score below the last place does not show up.
export function hallWithPlayer(best: number, name: string = PLAYER_NAME): { name: string; score: number; me?: boolean }[] {
  const at = HALL_OF_FAME.findIndex((h) => h.score < best)
  if (best <= 0 || at < 0) return HALL_OF_FAME
  const me = { name, score: best, me: true }
  return [...HALL_OF_FAME.slice(0, at), me, ...HALL_OF_FAME.slice(at)].slice(0, HALL_OF_FAME.length)
}
