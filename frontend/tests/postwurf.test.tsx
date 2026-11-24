import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Postwurf } from '../src/components/Postwurf'
import { Wizard } from '../src/wizard/Wizard'
import { mockBackend } from '../src/wizard/setupApi'
import { HALL_OF_FAME, PLAYER_NAME, PLAYER_Y, POINTS_BY_ROW, UFO_Y, W, aliveCount, hallWithPlayer, newGame, period, step, type Game, type Input } from '../src/logic/postwurf'

afterEach(cleanup)
const none: Input = { left: false, right: false, fire: false }
const calm = () => 0.99 // never a bomb

describe('Postwurf rules', () => {
  it('moves the player and keeps it inside the field', () => {
    let g = newGame()
    g = step(g, { ...none, left: true }, calm)
    expect(g.player).toBeLessThan(W / 2)
    for (let i = 0; i < 200; i++) g = step(g, { ...none, left: true }, calm)
    expect(g.player).toBeGreaterThanOrEqual(8)
    for (let i = 0; i < 300; i++) g = step(g, { ...none, right: true }, calm)
    expect(g.player).toBeLessThanOrEqual(W - 8)
  })

  it('allows one shot at a time and scores a hit', () => {
    let g = newGame()
    const first = g.aliens.find((a) => a.row === 4)!
    g = { ...g, player: first.x + 7 }
    g = step(g, { ...none, fire: true }, calm)
    expect(g.shot).not.toBeNull()
    const y = g.shot!.y
    g = step(g, { ...none, fire: true }, calm)
    expect(g.shot!.y).toBeLessThan(y) // still the same shot, moving up
    for (let i = 0; i < 60 && g.shot; i++) g = step(g, none, calm)
    expect(g.score).toBeGreaterThan(0)
    expect(aliveCount(g)).toBe(39)
  })

  it('moves faster with fewer aliens', () => {
    const g = newGame()
    const few = { ...g, aliens: g.aliens.map((a, i) => ({ ...a, alive: i < 3 })) }
    expect(period(few)).toBeLessThan(period(g))
  })

  it('a bomb on the player costs a life, the last one ends the game', () => {
    let g: Game = { ...newGame(), bombs: [{ x: W / 2, y: PLAYER_Y - 3 }] }
    g = step(g, none, calm)
    expect(g.lives).toBe(2)
    expect(g.bombs).toHaveLength(0)
    g = { ...g, lives: 1, bombs: [{ x: g.player, y: PLAYER_Y - 3 }] }
    g = step(g, none, calm)
    expect(g.over).toBe(true)
    expect(step(g, none, calm)).toBe(g) // nothing moves after the end
  })

  it('aliens reaching the player end the game', () => {
    const g0 = newGame()
    const low = { ...g0, aliens: g0.aliens.map((a) => ({ ...a, y: PLAYER_Y - 8 })) }
    expect(step(low, none, calm).over).toBe(true)
  })

  it('a cleared wave starts the next one and keeps score and lives', () => {
    const g0 = newGame()
    const one = { ...g0, score: 120, lives: 2, aliens: g0.aliens.map((a, i) => ({ ...a, alive: i === 0 })) }
    const target = one.aliens[0]
    let g: Game = { ...one, shot: { x: target.x + 5, y: target.y + 8 } }
    g = step(g, none, calm)
    expect(g.level).toBe(2)
    expect(aliveCount(g)).toBe(40)
    expect(g.score).toBe(120 + POINTS_BY_ROW[0])
    expect(g.lives).toBe(2)
  })
})

describe('the bonus saucer', () => {
  it('crosses the top, can be hit for bonus points and leaves the field', () => {
    let g = newGame()
    const spawn = () => 0 // below the spawn chance, picks the left side
    g = step(g, none, spawn)
    expect(g.ufo).not.toBeNull()
    g = { ...g, ufo: { x: 100, dir: 1 }, shot: { x: 108, y: UFO_Y + 6 } }
    const before = g.score
    g = step(g, none, calm)
    expect(g.ufo).toBeNull()
    expect([50, 100, 150, 300]).toContain(g.score - before)
    let away: Game = { ...newGame(), ufo: { x: W + 16, dir: 1 } }
    away = step(away, none, calm)
    expect(away.ufo).toBeNull()
  })
})

describe('the hall of fame', () => {
  it('lists the figures by their full names, best first', () => {
    expect(HALL_OF_FAME.map((h) => h.name)).toEqual(expect.arrayContaining(['Jean-Luc Picard', 'Zaphod Beeblebrox', 'William Riker', 'Deanna Troi']))
    const scores = HALL_OF_FAME.map((h) => h.score)
    expect([...scores].sort((a, b) => b - a)).toEqual(scores)
  })
  it('shows the player only after beating the top score', () => {
    const top = HALL_OF_FAME[0].score
    expect(hallWithPlayer(0).some((h) => h.name === PLAYER_NAME)).toBe(false)
    expect(hallWithPlayer(top).some((h) => h.name === PLAYER_NAME)).toBe(false)
    const win = hallWithPlayer(top + 1)
    expect(win[0]).toMatchObject({ name: PLAYER_NAME, score: top + 1, me: true })
    expect(win).toHaveLength(HALL_OF_FAME.length)
  })
})

describe('the Postwurf window', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    localStorage.clear()
  })
  afterEach(() => vi.useRealTimers())

  it('starts on demand, lists the invented scores and closes', () => {
    const onClose = vi.fn()
    render(<Postwurf onClose={onClose} />)
    expect(screen.getByText('Bestenliste (Punkte erfunden)')).toBeTruthy()
    expect(screen.queryByText(PLAYER_NAME)).toBeNull()
    fireEvent.click(screen.getByText('Start'))
    expect(screen.queryByText('Bestenliste (Punkte erfunden)')).toBeNull()
    act(() => { vi.advanceTimersByTime(400) })
    expect(document.querySelectorAll('[data-alien]').length).toBe(40)
    fireEvent.click(screen.getByText('Schließen'))
    expect(onClose).toHaveBeenCalled()
  })

  it('the fire button shoots', () => {
    render(<Postwurf onClose={() => {}} />)
    fireEvent.click(screen.getByText('Start'))
    fireEvent.pointerDown(screen.getByLabelText('Feuer'))
    act(() => { vi.advanceTimersByTime(80) })
    expect(document.querySelector('[data-shot]')).not.toBeNull()
  })
})

describe('the game in the wizard', () => {
  it('draws the sky behind the window', () => {
    render(<Wizard backend={mockBackend()} onDone={() => {}} />)
    expect(document.querySelector('[data-wizard] [data-wallpaper="clouds"]')).not.toBeNull()
  })

  it('sits in the corner of the wizard as an extra and opens on a click', () => {
    render(<Wizard backend={mockBackend()} onDone={() => {}} />)
    expect(screen.queryByLabelText('Spielfeld Alienalarm')).toBeNull()
    fireEvent.click(screen.getByText('Alienalarm'))
    expect(screen.getByLabelText('Spielfeld Alienalarm')).toBeTruthy()
    fireEvent.click(screen.getByText('Schließen'))
    expect(screen.queryByLabelText('Spielfeld Alienalarm')).toBeNull()
  })

  it('is not part of the desktop any more', async () => {
    const menu = (await import('node:fs')).readFileSync('src/components/StartMenu.tsx', 'utf8')
    const app = (await import('node:fs')).readFileSync('src/App.tsx', 'utf8')
    const run = (await import('node:fs')).readFileSync('src/components/RunDialog.tsx', 'utf8')
    for (const src of [menu, app, run]) expect(src).not.toMatch(/postwurf|Alienalarm/)
  })
})
