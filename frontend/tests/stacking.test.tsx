import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { DesktopApp } from '../src/App'
import { levelOf, raised, STACK_BASE } from '../src/core/stacking'
import { useSignedInServer } from './support/harness'

afterEach(cleanup)

describe('stacking order', () => {
  it('puts the clicked element last and keeps the order of the others', () => {
    expect(raised(['a', 'b', 'c'], 'a')).toEqual(['b', 'c', 'a'])
    expect(raised(['a', 'b', 'c'], 'c')).toEqual(['a', 'b', 'c'])
    expect(raised(['a', 'b'], 'new')).toEqual(['a', 'b', 'new'])
  })

  it('gives every element its own level, never above the task bars at 70', () => {
    const ids = Array.from({ length: 30 }, (_, i) => `e${i}`)
    const levels = ids.map((id) => levelOf(ids, id))
    expect(new Set(levels).size).toBe(30)
    expect(Math.max(...levels)).toBeLessThan(70)
    expect(levelOf(ids, 'unknown')).toBeLessThan(STACK_BASE)
  })
})

describe('the desktop', () => {
  useSignedInServer()

  const z = (el: Element) => parseInt((el as HTMLElement).style.zIndex)
  const tile = (name: string) => document.querySelector(`[data-tile="${name}"]`) as HTMLElement
  const clock = () => (document.querySelector('[aria-label="Uhr"]') as Element).closest('.fixed') as HTMLElement
  const note = () => screen.getByText('Notizzettel').closest('.fixed') as HTMLElement
  const funki = () => document.querySelector('[data-stack="funki"]') as HTMLElement
  const bin = () => screen.getByText('Papierkorb').closest('button') as HTMLElement

  it('shows everything at the start, application tiles in front of the playful pieces', async () => {
    render(<DesktopApp />)
    await waitFor(() => expect(tile('Empfänger')).not.toBeNull())
    for (const el of [clock(), note(), funki(), bin(), tile('Empfänger'), tile('Dateien und Anhänge')]) expect(el).toBeTruthy()
    expect(z(tile('Empfänger'))).toBeGreaterThan(z(clock()))
    expect(z(tile('Dateien und Anhänge'))).toBeGreaterThan(z(note()))
    expect(z(tile('Empfänger'))).toBeGreaterThan(z(funki()))
  })

  it('brings whatever is clicked to the front, one after the other', async () => {
    render(<DesktopApp />)
    await waitFor(() => expect(tile('Empfänger')).not.toBeNull())

    fireEvent.pointerDown(clock())
    expect(z(clock())).toBeGreaterThan(z(tile('Empfänger')))
    expect(z(clock())).toBeGreaterThan(z(tile('Dateien und Anhänge')))

    fireEvent.pointerDown(tile('Empfänger'))
    expect(z(tile('Empfänger'))).toBeGreaterThan(z(clock()))
    expect(z(clock())).toBeGreaterThan(z(tile('Dateien und Anhänge')))

    fireEvent.pointerDown(note())
    expect(z(note())).toBeGreaterThan(z(tile('Empfänger')))

    fireEvent.pointerDown(funki())
    expect(z(funki())).toBeGreaterThan(z(note()))

    fireEvent.pointerDown(bin())
    expect(z(bin())).toBeGreaterThan(z(funki()))
    expect(z(bin())).toBeGreaterThan(z(tile('Empfänger')))

    fireEvent.pointerDown(tile('Dateien und Anhänge'))
    expect(z(tile('Dateien und Anhänge'))).toBeGreaterThan(z(bin()))
  })

  it('never lets two elements share a level and keeps all below the task bars', async () => {
    render(<DesktopApp />)
    await waitFor(() => expect(tile('Empfänger')).not.toBeNull())
    for (const el of [note(), tile('Empfänger'), clock(), bin(), funki()]) fireEvent.pointerDown(el)
    const levels = [clock(), note(), funki(), bin(), tile('Empfänger'), tile('Dateien und Anhänge')].map(z)
    expect(new Set(levels).size).toBe(levels.length)
    expect(Math.max(...levels)).toBeLessThan(70)
  })

  it('brings a tile to the front when it is opened from the start menu', async () => {
    render(<DesktopApp />)
    await waitFor(() => expect(tile('Empfänger')).not.toBeNull())
    fireEvent.pointerDown(clock())
    fireEvent.click(screen.getByText('✉ Start'))
    fireEvent.click(screen.getAllByText('Mail Assistant').find((e) => e.className.includes('flex-1'))!)
    fireEvent.click(screen.getByText('Mail-Einstellungen'))
    await waitFor(() => expect(tile('Einstellungen')).not.toBeNull())
    expect(z(tile('Einstellungen'))).toBeGreaterThan(z(clock()))
  })

  it('has no tile wider than the free space left of the clock column at the common window size', async () => {
    window.innerWidth = 1360
    render(<DesktopApp />)
    await waitFor(() => expect(tile('Empfänger')).not.toBeNull())
    const files = tile('Dateien und Anhänge')
    const right = parseInt(files.style.left) + parseInt(files.style.width)
    expect(right).toBeLessThanOrEqual(1360 - 170)
  })
})
