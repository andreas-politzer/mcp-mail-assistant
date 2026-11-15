import { useEffect, useState } from 'react'

const FIRST = 'Du hast Post! Naja, so ähnlich.'
const OTHERS = [
  'Wer KI-Agenten benutzt, hat die Kontrolle über sein Leben verloren.',
  'Du schon wieder? Hast du nichts Produktives zu tun?',
  'Ich bin ein Pixelhaufen und trotzdem intelligenter als dein Desktop.',
  'Klick nicht so hektisch. Ich werde davon nicht jünger.',
  'Das war jetzt nicht besonders clever. Aber niedlich.',
  'Du nennst das Multitasking? Ich nenne es digitale Orientierungslosigkeit.',
  'Ich verschicke nichts ohne deine Erlaubnis. Dein Urteilsvermögen kenne ich ja.',
  'Du hast zwölf Fenster offen und weißt nicht, welches. Ich sehe alles.',
  'Ich habe zwei Augen und mehr Überblick als du.',
  'Mein Gesicht besteht aus Rechtecken. Deine Ablage auch, nur ohne Gesicht.',
  'Keine Sorge, ich urteile nicht. Ich notiere nur.',
  'Du siehst heute besonders entschlossen aus. Das ist selten.',
  'Ein Telefon mit Gesicht gibt dir Ratschläge. Denk kurz darüber nach.',
  'Ich war schon Pixel, da hast du noch Zettel benutzt.',
  'Du brauchst keinen Assistenten. Du brauchst eine Gebrauchsanweisung. Die gibt es unter Hilfe.',
  'Ich zähle deine Klicks. Es sind erstaunlich viele.',
  'Ja, ich bin hübscher als dein Terminal.',
  'Wenn du so weitermachst, wird das noch ein richtiger Arbeitstag.',
  'Ich hatte heute noch kein Gespräch, in dem ich nicht recht hatte.',
  'Hast du schon gesichert? Natürlich nicht. Niedlich.',
  'Das Postfach quillt über, und du klickst auf mich. Respekt.',
  'Ich bin nicht aufdringlich. Du bist nur sehr oft hier.',
  'Mach ruhig weiter. Ich schaue zu und schweige. Fast.',
  'Du bist das Beste, was diesem Desktop passiert ist. Das sagt eigentlich schon alles.',
  'Wenn Claude etwas vorschlägt, entscheidest du. Das ist der ganze Trick. Schaffst du.',
]

// The little helper: a chunky first-generation mobile phone with a face.
// Own drawing. Click it to hear something; click again to send it away.
export function Assistant({ right = 124, zIndex, onFocus }: { right?: number; zIndex?: number; onFocus?: () => void }) {
  const [text, setText] = useState<string | null>(null)
  const [opened, setOpened] = useState(0)
  const [mouthOpen, setMouthOpen] = useState(false)

  useEffect(() => {
    if (text === null) return
    let n = 0
    const t = setInterval(() => {
      n += 1
      setMouthOpen((v) => !v)
      if (n >= 8) {
        clearInterval(t)
        setMouthOpen(false)
      }
    }, 170)
    return () => clearInterval(t)
  }, [text])

  const toggle = () => {
    if (text !== null) return setText(null)
    setText(opened === 0 ? FIRST : OTHERS[Math.floor(Math.random() * OTHERS.length)])
    setOpened((n) => n + 1)
  }

  return (
    <div className="absolute flex items-end gap-2 select-none" data-stack="funki" onPointerDownCapture={onFocus} style={{ right, bottom: 48, zIndex }}>
      {text !== null && (
        <div className="relative mb-12 max-w-[220px] px-3 py-2 text-[12px] leading-snug" style={{ background: '#ffffe1', color: '#000', border: '1px solid #000', boxShadow: '2px 2px 0 rgba(0,0,0,0.35)' }}>
          {text}
          <div className="absolute -right-[6px] bottom-5 w-3 h-3 rotate-45" style={{ background: '#ffffe1', borderTop: '1px solid #000', borderRight: '1px solid #000' }} />
        </div>
      )}
      <button type="button" title="Funki – klick mich" onClick={toggle} className="assistant-wiggle outline-none cursor-pointer">
        <svg width={66} height={108} viewBox="0 0 22 36" shapeRendering="crispEdges" aria-label="Assistent">
          <rect x={15} y={1} width={2} height={7} fill="#2b2b33" />
          <rect x={14} y={0} width={4} height={2} fill="#d6334c" />
          <rect x={3.5} y={7.5} width={15} height={27} fill="#4a4f5c" stroke="#1d1d2b" strokeWidth={1} />
          <rect x={8} y={10} width={6} height={1} fill="#1d1d2b" />
          <rect x={5.5} y={12.5} width={11} height={9} fill="#b5d8a0" stroke="#1d1d2b" strokeWidth={1} />
          <rect className="assistant-eye" x={8} y={14.5} width={2} height={3} fill="#1d1d2b" />
          <rect className="assistant-eye" x={12} y={14.5} width={2} height={3} fill="#1d1d2b" />
          <rect x={9} y={19} width={4} height={mouthOpen ? 2 : 1} fill="#1d1d2b" />
          {[0, 1, 2, 3].flatMap((r) =>
            [0, 1, 2].map((c) => <rect key={`${r}${c}`} x={6 + c * 4} y={24 + r * 2.5} width={3} height={1.6} fill="#cfd3da" />),
          )}
        </svg>
      </button>
    </div>
  )
}
