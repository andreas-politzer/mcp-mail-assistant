import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { funkiRight } from '../src/App'
import { Assistant } from '../src/components/Assistant'
import { Clock } from '../src/components/Clock'
import { Sticky } from '../src/components/Sticky'
import { Taskbar } from '../src/components/Taskbar'

afterEach(cleanup)

const common = { onClose: () => {}, onFocus: () => {}, initial: { x: 10, y: 10 } }

describe('desktop layout', () => {
  it('keeps Funki clear of the column with the bin, on every common window width', () => {
    for (const w of [1024, 1100, 1280, 1366, 1440, 1512, 1728, 1920, 2560]) {
      const binLeft = 8 + (Math.max(2, Math.floor((w - 8) / 96)) - 1) * 96
      const funkiRightEdge = w - funkiRight(w)
      expect(funkiRightEdge, `width ${w}`).toBeLessThanOrEqual(binLeft - 8)
    }
  })

  it('places Funki where it is told to', () => {
    const { container } = render(<Assistant right={200} />)
    expect((container.firstChild as HTMLElement).style.right).toBe('200px')
  })

  it('has a smaller clock and sticky note', () => {
    const clock = render(<Clock c={common} />).container.querySelector('.fixed') as HTMLElement
    expect(parseInt(clock.style.width)).toBeLessThanOrEqual(190 * 0.8)
    cleanup()
    const note = render(<Sticky {...common} />).container.querySelector('.fixed') as HTMLElement
    expect(parseInt(note.style.width)).toBeLessThanOrEqual(200 * 0.8)
  })

  it('has no separate sign-out button any more', () => {
    render(<Taskbar items={[]} onToggle={() => {}} onCommand={() => {}} />)
    expect(screen.queryByText('Abmelden')).toBeNull()
  })
})

describe('Funki', () => {
  it('speaks, and more than one line is in the rotation', () => {
    render(<Assistant />)
    const heard = new Set<string>()
    for (let i = 0; i < 60; i++) {
      fireEvent.click(screen.getByTitle('Funki – klick mich'))
      const bubble = document.querySelector('.relative.mb-12')
      if (bubble?.textContent) heard.add(bubble.textContent)
      fireEvent.click(screen.getByTitle('Funki – klick mich'))
    }
    expect(heard.size).toBeGreaterThan(8)
    for (const line of heard) expect(line.length).toBeLessThan(110)
  })
})
